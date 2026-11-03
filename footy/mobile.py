"""Extra phone-page features: Team, Props, My bets and Backtest tabs.

Everything here produces either JSON for the page's script (rendered and filtered on the
phone) or small HTML sections. Kept separate from report.py so the page builder stays readable.
"""

from __future__ import annotations

import datetime as dt
import html
import json

import numpy as np
import pandas as pd

from . import config, db
from .models import backtest, form, props


# ------------------------------------------------------------------ team histories

def team_data(frame: pd.DataFrame, names: dict, ratings: dict) -> dict:
    """Per team: current-season matches (all competitions), rating summary."""
    cur = config.current_season()
    lf = form.long_format(frame[frame.season == cur])
    out: dict[str, dict] = {}
    if lf.empty:
        return out
    cfg = config.leagues()
    domestic = lf[lf.league.map(lambda k: k in cfg and not cfg[k].is_europe)]
    home_lg = domestic.groupby("team").league.agg(lambda x: x.value_counts().index[0]).to_dict()
    for team, g in lf.sort_values("date").groupby("team"):
        lg = home_lg.get(team) or g.league.iloc[0]
        rows = []
        for r in g.itertuples():
            rows.append([r.date.strftime("%d %b"), r.league, r.opponent, "H" if r.venue == "home" else "A",
                         int(r.gf), int(r.ga), _r(r.xg_for), _r(r.xg_against),
                         _r(getattr(r, "big_chances_for", np.nan)), _r(getattr(r, "big_chances_against", np.nan)),
                         r.xg_kind == "real"])
        rt = ratings.get(lg)
        rank = None
        if rt is not None and team in rt.att:
            tbl = rt.table()
            tbl = tbl[tbl.team.isin(set(g.team) | set(frame[(frame.league == lg) & (frame.season == cur)].home))]
            if team in set(tbl.team):
                row = tbl[tbl.team == team].iloc[0]
                rank = [int(tbl.index[tbl.team == team][0]) + 1, len(tbl), _r(row.exp_gf), _r(row.exp_ga)]
        out[team] = {"lg": lg, "m": rows, "rank": rank}
        names.setdefault(team, team)
    return out


def _r(v, n=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else round(f, n)


def upcoming(frame: pd.DataFrame, per_team: int = 5) -> dict:
    """team -> next few fixtures [date, comp, opponent, H/A, match_id] (beyond the 7-day window too)."""
    today = pd.Timestamp.today().normalize()
    fx = frame[(frame.status == "fixture") & (frame.date >= today)].sort_values("date")
    out: dict[str, list] = {}
    for r in fx.itertuples():
        for team, opp, ha in ((r.home, r.away, "H"), (r.away, r.home, "A")):
            lst = out.setdefault(team, [])
            if len(lst) < per_team:
                lst.append([r.date.strftime("%a %d %b"), r.league, opp, ha, r.match_id])
    return out


def results(frame: pd.DataFrame, days: int = 60) -> dict:
    """match_id -> [home goals, away goals] for recent games (used to settle tracked bets)."""
    since = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    p = frame[(frame.status == "played") & (frame.date >= since)].dropna(subset=["hg", "ag"])
    return {m: [int(h), int(a)] for m, h, a in zip(p.match_id, p.hg, p.ag)}


# ------------------------------------------------------------------ props

def props_data(con, pred: pd.DataFrame, frame: pd.DataFrame, ratings: dict, names: dict) -> list:
    if pred.empty:
        return []
    cur = config.current_season()
    fx = pred[["match_id", "league", "date", "home", "away"]].copy()
    fx["home_name"], fx["away_name"] = fx.home.map(names), fx.away.map(names)
    pt = props.player_table(con, None, [cur])
    if pt.empty:
        return []
    pr = props.price_props(pt, fx, ratings, min_minutes=360)
    if pr.empty:
        return []
    pr = pr.merge(fx[["league", "date", "home_name", "away_name", "match_id"]].assign(
        match=fx.home_name + " v " + fx.away_name), on=["league", "date", "match"], how="left")
    pr = pr[pr.p_anytime >= 0.04]
    pr = pr.sort_values("p_anytime", ascending=False).groupby(["match_id", "team"]).head(8)
    out = []
    for r in pr.itertuples():
        out.append([r.match_id, r.league, pd.Timestamp(r.date).strftime("%a %d %b"), r.match, r.player,
                    names.get(r.team, r.team), _r(r.p_anytime, 3), _r(r.p_score_or_assist, 3),
                    _r(r.exp_shots, 2), _r(r.p_1plus_shots, 3), _r(r.p_2plus_shots, 3),
                    _r(r.p_1plus_sot, 3), _r(r.p_2plus_sot, 3)])
    return out


# ------------------------------------------------------------------ backtest (cached per finished season)

def backtest_rows(con, frame: pd.DataFrame, leagues: list[str], season: int) -> pd.DataFrame:
    con.execute("CREATE TABLE IF NOT EXISTS backtest_cache (league TEXT, season INTEGER, summary TEXT, "
                "at TEXT, PRIMARY KEY (league, season))")
    rows = []
    cfg = config.leagues()
    for lg in leagues:
        if lg in cfg and cfg[lg].is_europe:
            continue
        hit = con.execute("SELECT summary FROM backtest_cache WHERE league=? AND season=?", (lg, season)).fetchone()
        if hit:
            summ = json.loads(hit[0])
        else:
            try:
                _, summ = backtest.run(con, frame, lg, season)
            except Exception:  # noqa: BLE001 - a league with odd data shouldn't stop the page
                summ = {}
            con.execute("INSERT OR REPLACE INTO backtest_cache VALUES(?,?,?,?)",
                        (lg, season, json.dumps(summ), db.now_iso()))
            con.commit()
        if summ:
            rows.append({"league": cfg[lg].name if lg in cfg else lg, **summ})
    df = pd.DataFrame(rows)
    if not df.empty and "market_logloss" in df:
        df["vs_market"] = df.model_logloss_same_games - df.market_logloss
        df["verdict"] = np.where(df.vs_market < 0.005, "Close to the market - value flags worth a look",
                                 np.where(df.vs_market < 0.02, "Behind the market - be selective",
                                          "Well behind the market - treat value flags with suspicion"))
    return df


# ------------------------------------------------------------------ sections

TEAM_HTML = """
<p class="note">Every team in all 16 competitions: form, chance quality and what's next.</p>
<div class="mxform"><label>Competition<select id="tmL"></select></label><label>Team<select id="tmT"></select></label></div>
<div id="tmOut"></div>
"""

PROPS_HTML = """
<p class="note">Player probabilities for the next week from each player's xG/90, shots/90 and typical minutes,
scaled by how many goals the model expects his team to score in that game. Fair odds = 1 / probability;
if your bookmaker offers more, the model sees value. Props use Poisson tails, which slightly understate
the chance of big shot counts.</p>
<div class="fbar"><label>Market<select id="ppMk"><option value="6">Anytime scorer</option>
<option value="7">Score or assist</option><option value="9">1+ shots</option><option value="10">2+ shots</option>
<option value="11">1+ on target</option><option value="12">2+ on target</option></select></label>
<label>League<select id="ppLg"><option value="">All</option></select></label>
<label>Search<input id="ppQ" type="search" placeholder="Player, team or match"></label>
<label>Your odds (optional)<input id="ppOdds" type="number" step="0.01" placeholder="e.g. 3.50"></label></div>
<div id="ppOut"></div>
"""

BETS_HTML = """
<p class="note">Bets you track from the Match tab. They settle automatically from results each day.
Stored on this device only (export to move them to another phone).</p>
<div class="grid" id="btSum"></div>
<div id="btList"></div>
<div class="fbar"><button class="b" id="btExport">Export</button><button class="b" id="btImport">Import</button>
<button class="b" id="btClear">Clear settled</button></div>
<textarea id="btIO" class="hide" rows="4" style="width:100%;font:13px monospace"></textarea>
"""


def backtest_section(df: pd.DataFrame, season: int) -> str:
    from .report import table
    if df.empty:
        return '<p class="note">Backtest not available yet.</p>'
    return (f'<p class="note">How the model would have done over {config.season_label(season)}, refitting every week '
            "using only past data, scored against results and bookmakers' closing odds (margin removed). Log loss: "
            "lower is better. If the model is clearly behind the market in a league, its value flags there are "
            "probably noise. ROI = flat stakes on every 5%+ edge at average pre-match odds.</p>"
            + table(df.sort_values("vs_market") if "vs_market" in df else df,
                    [("league", "League", "text"), ("matches", "Games", "int"),
                     ("model_logloss_same_games", "Model", "f3"), ("market_logloss", "Market", "f3"),
                     ("bets", "Bets", "int"), ("roi", "ROI", "signed_pct"), ("hit_rate", "Hit", "pct"),
                     ("verdict", "Verdict", "text")]))


JS = r"""
/* ---------- Team tab */
(()=>{const L=$('#tmL'),T=$('#tmT'),O=$('#tmOut');if(!L)return;
 const lgs=[...new Set(Object.values(D.teams).map(t=>t.lg))].sort((a,b)=>Object.keys(D.lgnames).indexOf(a)-Object.keys(D.lgnames).indexOf(b));
 L.innerHTML=lgs.map(k=>`<option value="${k}">${D.lgnames[k]||k}</option>`).join('');
 function fillT(sel){const ts=Object.keys(D.teams).filter(t=>D.teams[t].lg===L.value).sort((a,b)=>nm(a).localeCompare(nm(b)));
  T.innerHTML=ts.map(t=>`<option value="${t}">${nm(t)}</option>`).join('');if(sel)T.value=sel;draw()}
 function chart(m){if(m.length<2)return'';const W=340,H=150,P=24,n=m.length,w=5;
  const roll=i=>{const s=m.slice(Math.max(0,i-w+1),i+1);return[s.reduce((a,x)=>a+(x[6]||0),0)/s.length,s.reduce((a,x)=>a+(x[7]||0),0)/s.length]};
  const pts=m.map((_,i)=>roll(i));const mx=Math.max(2.5,...pts.flat())*1.1;
  const X=i=>P+(W-P-6)*i/(n-1),Y=v=>H-P+4-(H-P-4)*v/mx;
  const line=(k,c)=>`<polyline fill="none" stroke="${c}" stroke-width="2.5" points="${pts.map((p,i)=>X(i)+','+Y(p[k])).join(' ')}"/>`;
  let g='';for(const v of [0,1,2,3])if(v<mx)g+=`<line x1="${P}" x2="${W}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="2" y="${Y(v)+4}" font-size="10" fill="var(--muted)">${v}</text>`;
  return`<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:640px">${g}${line(0,'var(--pos)')}${line(1,'var(--neg)')}</svg>
   <p class="note"><span class="pos">━ xG for</span> · <span class="neg">━ xG against</span> (5-game rolling average, all competitions)</p>`}
 function draw(){const t=T.value,d=D.teams[t];if(!d){O.innerHTML='';return}const m=d.m;
  const W=m.filter(x=>x[4]>x[5]).length,Dr=m.filter(x=>x[4]==x[5]).length,Ls=m.length-W-Dr;
  const avg=(f,arr=m)=>arr.length?(arr.reduce((a,x)=>a+(f(x)||0),0)/arr.length):0;
  const hm=m.filter(x=>x[3]=='H'),aw=m.filter(x=>x[3]=='A');
  const form=m.slice(-6).map(x=>x[4]>x[5]?'<i class="pos">W</i>':x[4]==x[5]?'<i>D</i>':'<i class="neg">L</i>').join('');
  const r=d.rank;const card=(l,v,s)=>`<div class="mk"><span>${l}</span><b>${v}</b><span>${s||''}</span></div>`;
  let h='<div class="grid">'+card('Record',`${W}-${Dr}-${Ls}`,`${m.length} games`)+card('Form (last 6)',form)+
   card('xG / game',avg(x=>x[6]).toFixed(2),`scored ${avg(x=>x[4]).toFixed(2)}`)+card('xGA / game',avg(x=>x[7]).toFixed(2),`conceded ${avg(x=>x[5]).toFixed(2)}`)+
   card('Home xG diff',(avg(x=>x[6],hm)-avg(x=>x[7],hm)).toFixed(2),`${hm.length} games`)+card('Away xG diff',(avg(x=>x[6],aw)-avg(x=>x[7],aw)).toFixed(2),`${aw.length} games`)+
   (r?card('Model rank',`${r[0]} / ${r[1]}`,`exp. ${r[2]} for · ${r[3]} against`):'')+'</div>';
  h+=chart(m);
  const up=(D.upcoming||{})[t]||[];
  if(up.length)h+='<h2>Next up</h2><div class="tw"><table><tbody>'+up.map(u=>{const f=D.fixtures.find(x=>x.id===u[4]);
   return`<tr><td class="l">${u[0]}</td><td class="l">${D.lgnames[u[1]]||u[1]}</td><td class="l">${u[3]=='H'?'v':'@'} ${nm(u[2])}</td><td>${f?`<a class="go" href="#" data-fx="${f.id}">Check ›</a>`:''}</td></tr>`}).join('')+'</tbody></table></div>';
  h+='<h2>Results</h2><div class="tw"><table><thead><tr><th class="l">Date</th><th class="l">Comp</th><th class="l">Opponent</th><th>Score</th><th>xG</th><th>xGA</th><th>BC</th><th>BCA</th></tr></thead><tbody>'+
   m.slice().reverse().map(x=>`<tr><td class="l">${x[0]}</td><td class="l">${(D.lgnames[x[1]]||x[1]).replace('League','Lg')}</td><td class="l">${x[3]=='H'?'v':'@'} ${nm(x[2])}</td><td class="${x[4]>x[5]?'pos':x[4]<x[5]?'neg':''}">${x[4]}-${x[5]}</td><td>${x[6]!=null?x[6].toFixed(2):''}${x[10]?'':'*'}</td><td>${x[7]!=null?x[7].toFixed(2):''}</td><td>${x[8]??''}</td><td>${x[9]??''}</td></tr>`).join('')+
   '</tbody></table></div><p class="note">* estimated from shots (no real xG for that game). BC = big chances.</p>';
  O.innerHTML=h}
 L.onchange=()=>fillT();T.onchange=draw;
 const fav=(()=>{try{return localStorage.getItem('team')}catch(e){return null}})()||D.fav;
 if(fav&&D.teams[fav]){L.value=D.teams[fav].lg;fillT(fav)}else fillT();
 T.addEventListener('change',()=>{try{localStorage.setItem('team',T.value)}catch(e){}});
})();

/* ---------- favourite team card on Fixtures */
(()=>{const box=$('#favCard'),t=D.fav;if(!box||!t||!D.teams[t])return;const d=D.teams[t],up=(D.upcoming||{})[t]||[];
 const last=d.m.slice(-5).map(x=>x[4]>x[5]?'<i class="pos">W</i>':x[4]==x[5]?'<i>D</i>':'<i class="neg">L</i>').join('');
 let h=`<div class="card"><h2>${nm(t)}</h2><p class="note">Form ${last}</p>`;
 const f=up.length?D.fixtures.find(x=>x.id===up[0][4]):null;
 if(f){const M=matrix(f.xh,f.xa,(D.ratings[f.lg]||{}).rho||-0.05),mk=markets(M,nm(f.h),nm(f.a));const g=k=>mk.find(x=>x[0]===k)[2];
  h+=`<p><b>${f.day} · ${nm(f.h)} v ${nm(f.a)}</b> <span class="sub">${D.lgnames[f.lg]||f.lg}</span></p><div class="grid">`+
   [['H',nm(f.h)],['D','Draw'],['A',nm(f.a)],['O2.5','Over 2.5'],['BTTS','BTTS']].map(([k,l])=>`<div class="mk"><span>${l}</span><b>${pc(g(k))}</b><span>fair ${fo(g(k))}</span></div>`).join('')+
   `</div><p><a class="go" href="#" data-fx="${f.id}">Open in Match tab ›</a>${(f.nh||f.na)?` <span class="sub">${[f.nh,f.na].filter(Boolean).join(' · ')}</span>`:''}</p>`}
 else if(up.length)h+=`<p>Next: ${up[0][0]} ${up[0][3]=='H'?'v':'@'} ${nm(up[0][2])} <span class="sub">(${D.lgnames[up[0][1]]||up[0][1]})</span></p>`;
 if(up.length>1)h+='<p class="note">Then: '+up.slice(1,4).map(u=>`${u[0]} ${u[3]=='H'?'v':'@'} ${nm(u[2])}`).join(' · ')+'</p>';
 box.innerHTML=h+'</div>'})();

/* ---------- Props tab */
(()=>{const box=$('#ppOut');if(!box)return;const lg=$('#ppLg');
 lg.innerHTML+=([...new Set(D.props.map(p=>p[1]))]).map(k=>`<option value="${k}">${D.lgnames[k]||k}</option>`).join('');
 function draw(){const c=parseInt($('#ppMk').value),q=($('#ppQ').value||'').toLowerCase(),L=lg.value,o=parseFloat($('#ppOdds').value);
  const rows=D.props.filter(p=>(!L||p[1]===L)&&(!q||(p[3]+' '+p[4]+' '+p[5]).toLowerCase().includes(q))&&p[c]!=null).sort((a,b)=>b[c]-a[c]).slice(0,150);
  box.innerHTML=rows.length?'<div class="tw"><table><thead><tr><th class="l">Player</th><th class="l">Match</th><th>Prob.</th><th>Fair</th>'+(o>1?'<th>Edge @ '+o+'</th>':'')+'<th>Exp. shots</th></tr></thead><tbody>'+
   rows.map(p=>{const e=o>1?p[c]*o-1:null;return`<tr><td class="l">${p[4]}<br><span class="sub">${p[5]}</span></td><td class="l">${p[2]}<br><span class="sub">${p[3]}</span></td><td>${pc(p[c])}</td><td>${fo(p[c])}</td>`+
   (e!=null?`<td class="${e>0?'pos':'neg'}">${(e>0?'+':'')+(100*e).toFixed(0)}%</td>`:'')+`<td>${p[8]!=null?p[8].toFixed(1):''}</td></tr>`}).join('')+'</tbody></table></div>'
   :'<p class="note">No player data for these fixtures yet.</p>'}
 $$('#ppMk,#ppLg,#ppQ,#ppOdds').forEach(x=>x.addEventListener('input',draw));draw()})();

/* ---------- My bets (localStorage) */
const BK='footy-bets';
function loadBets(){try{return JSON.parse(localStorage.getItem(BK)||'[]')}catch(e){return[]}}
function saveBets(b){try{localStorage.setItem(BK,JSON.stringify(b))}catch(e){alert('Could not save on this device (private browsing?)')}}
function settle(b){const r=(D.results||{})[b.id];if(!r||b.res)return b;const[h,a]=r,t=h+a,k=b.mk;let w=null;
 const m=k.match(/^([OU])([\d.]+)$/),ah=k.match(/^AH([+-][\d.]+)$/);
 if(k=='H')w=h>a;else if(k=='D')w=h==a;else if(k=='A')w=a>h;else if(k=='1X')w=h>=a;else if(k=='X2')w=a>=h;else if(k=='12')w=h!=a;
 else if(k=='BTTS')w=h>0&&a>0;else if(k=='BTTS_N')w=!(h>0&&a>0);else if(k=='H_CS')w=a==0;else if(k=='A_CS')w=h==0;
 else if(k=='DNB_H'||k=='DNB_A'){if(h==a)return{...b,res:'void',score:h+'-'+a};w=k=='DNB_H'?h>a:a>h}
 else if(m)w=m[1]=='O'?t>parseFloat(m[2]):t<parseFloat(m[2]);else if(ah)w=h-a+parseFloat(ah[1])>0;
 return w==null?b:{...b,res:w?'won':'lost',score:h+'-'+a}}
function drawBets(){const list=$('#btList');if(!list)return;let bets=loadBets().map(settle);saveBets(bets);
 const pl=b=>b.res==='won'?b.stake*(b.odds-1):b.res==='lost'?-b.stake:0;
 const st=bets.filter(b=>b.res&&b.res!=='void'),staked=st.reduce((a,b)=>a+b.stake,0),profit=st.reduce((a,b)=>a+pl(b),0);
 const exp=bets.reduce((a,b)=>a+b.stake*(b.p*b.odds-1),0);
 const card=(l,v,s)=>`<div class="mk"><span>${l}</span><b>${v}</b><span>${s||''}</span></div>`;
 $('#btSum').innerHTML=card('Bets',bets.length,`${bets.filter(b=>!b.res).length} open`)+card('Profit',(profit>=0?'+':'')+profit.toFixed(2),`settled stakes ${staked.toFixed(2)}`)+
  card('ROI',staked?((profit>=0?'+':'')+(100*profit/staked).toFixed(1)+'%'):'-',`${st.filter(b=>b.res==='won').length}/${st.length} won`)+card('Expected profit',(exp>=0?'+':'')+exp.toFixed(2),'from model edges');
 list.innerHTML=bets.length?'<div class="tw"><table><thead><tr><th class="l">Bet</th><th>Odds</th><th>Stake</th><th>Edge</th><th>Result</th><th></th></tr></thead><tbody>'+
  bets.slice().reverse().map((b,i)=>`<tr><td class="l">${b.label}<br><span class="sub">${b.day} · ${b.match}</span></td><td>${b.odds.toFixed(2)}</td><td>${b.stake}</td><td>${(100*(b.p*b.odds-1)).toFixed(0)}%</td>
   <td class="${b.res==='won'?'pos':b.res==='lost'?'neg':''}">${b.res?(b.res+(b.score?' '+b.score:'')):'open'}</td><td><a class="go" href="#" data-del="${bets.length-1-i}">✕</a></td></tr>`).join('')+'</tbody></table></div>'
  :'<p class="note">No bets yet - open a match in the Match tab, type the odds and tap "Track this bet".</p>'}
document.addEventListener('click',e=>{const d=e.target.closest('[data-del]');if(d){e.preventDefault();const b=loadBets();b.splice(parseInt(d.dataset.del),1);saveBets(b);drawBets()}});
(()=>{const pe=$('#mxEdge');if(!pe)return;const box=document.createElement('div');box.className='fbar';
 box.innerHTML='<label>Stake<input id="mxStake" type="number" step="1" value="10"></label><button class="b" id="mxTrack">Track this bet</button><span class="sub" id="mxTracked"></span>';
 pe.after(box);$('#mxTrack').onclick=()=>{const m=MK.find(x=>x[0]===mx.M.value),o=parseFloat(mx.P.value),s=parseFloat($('#mxStake').value)||1;
  if(!m||!(o>1)){$('#mxTracked').textContent='Type the odds first';return}
  const f=D.fixtures.find(x=>x.id===mx.F.value);if(!f){$('#mxTracked').textContent='Pick a fixture to track a bet';return}
  const b=loadBets();b.push({id:f.id,day:f.day,match:nm(f.h)+' v '+nm(f.a),mk:m[0],label:m[1],p:m[2],odds:o,stake:s,at:new Date().toISOString()});
  saveBets(b);drawBets();$('#mxTracked').textContent='✓ saved to My bets'}})();
(()=>{if(!$('#btExport'))return;const io=$('#btIO');
 $('#btExport').onclick=()=>{io.classList.remove('hide');io.value=JSON.stringify(loadBets());io.select();try{navigator.clipboard.writeText(io.value)}catch(e){}};
 $('#btImport').onclick=()=>{if(io.classList.contains('hide')){io.classList.remove('hide');io.value='';io.placeholder='Paste exported bets here, then tap Import again';return}
  try{const b=JSON.parse(io.value);if(Array.isArray(b)){saveBets(b);drawBets();io.classList.add('hide')}}catch(e){alert('That doesn\'t look like exported bets')}};
 $('#btClear').onclick=()=>{saveBets(loadBets().filter(b=>!b.res));drawBets()};drawBets()})();
"""


# ------------------------------------------------------------------ PWA bits

def write_icon(out_dir) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    for size in (180, 512):
        im = Image.new("RGB", (size, size), (11, 110, 79))
        d = ImageDraw.Draw(im)
        r = size * 0.32
        c = size / 2
        d.ellipse([c - r, c - r, c + r, c + r], fill=(255, 255, 255))
        d.polygon([(c, c - r * 0.45), (c + r * 0.43, c - r * 0.14), (c + r * 0.27, c + r * 0.36),
                   (c - r * 0.27, c + r * 0.36), (c - r * 0.43, c - r * 0.14)], fill=(29, 29, 31))
        im.save(out_dir / f"icon-{size}.png")
    (out_dir / "manifest.webmanifest").write_text(json.dumps({
        "name": app_name(), "short_name": app_name(), "start_url": ".", "display": "standalone",
        "background_color": "#f7f7f5", "theme_color": "#0b6e4f",
        "icons": [{"src": "icon-180.png", "sizes": "180x180", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]}))
    return True


def app_name() -> str:
    from .my import app_name as _n
    return _n()


def head() -> str:
    return ('<link rel="manifest" href="manifest.webmanifest"><link rel="apple-touch-icon" href="icon-180.png">'
            '<meta name="theme-color" content="#0b6e4f">'
            f'<meta name="apple-mobile-web-app-title" content="{html.escape(app_name())}">'
            '<meta name="apple-mobile-web-app-status-bar-style" content="default">')

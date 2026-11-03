"""Static HTML report: one self-contained page that works on a phone or iPad.

Built daily by .github/workflows/daily-report.yml and published to GitHub Pages.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, db
from .models import form, fpl, predict, props
from .models.data import match_frame, team_names
from .models.ratings import fit_all
from .models import referees
from .pipeline import coverage, latest_elo

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e4e4e0;--accent:#0b6e4f;
--pos:#0b6e4f;--neg:#b42318;--chip:#eef4f1}
@media (prefers-color-scheme:dark){:root{--bg:#111214;--card:#1b1c1f;--ink:#ececec;--muted:#9a9aa0;
--line:#2c2d31;--accent:#4cc38a;--pos:#4cc38a;--neg:#ff7b72;--chip:#1f2a25}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:20px 16px 8px;max-width:1200px;margin:auto}h1{margin:0;font-size:24px}
.sub{color:var(--muted);font-size:13px}
nav{position:sticky;top:0;background:var(--bg);z-index:5;border-bottom:1px solid var(--line)}
nav div{max-width:1200px;margin:auto;display:flex;gap:4px;overflow-x:auto;padding:8px 16px}
nav button{border:0;background:none;color:var(--muted);font:inherit;font-weight:600;padding:8px 12px;
border-radius:8px;white-space:nowrap;cursor:pointer}nav button.on{background:var(--chip);color:var(--accent)}
main{max-width:1200px;margin:auto;padding:12px 16px 40px}section{display:none}section.on{display:block}
h2{font-size:18px;margin:20px 0 6px}details{background:var(--card);border:1px solid var(--line);
border-radius:10px;margin:10px 0;padding:0 12px}summary{padding:12px 0;font-weight:600;cursor:pointer}
.note{color:var(--muted);font-size:13px;margin:4px 0 10px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-bottom:12px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card)}
td:first-child,th:first-child,td.l,th.l{text-align:left}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 12px 8px}
footer{color:var(--muted);font-size:12px;text-align:center;padding:20px}
.fbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 12px}
.fbar label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:2px}
input,select{font:inherit;font-size:15px;color:var(--ink);background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:7px 9px;min-height:38px}input[type=search]{min-width:170px;flex:1}
input[type=number]{width:90px}button.b{font:inherit;font-weight:600;border:1px solid var(--line);background:var(--card);
color:var(--accent);border-radius:8px;padding:7px 12px;cursor:pointer;min-height:38px}
a.go{color:var(--accent);font-weight:600;text-decoration:none}
th.s{cursor:pointer;user-select:none}th.s:after{content:" ⇅";opacity:.35;font-size:11px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin:10px 0}
.mk{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.mk b{display:block;font-size:20px}.mk span{font-size:12px;color:var(--muted)}
.mxform{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.mxform label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:2px}
.big{font-size:22px;font-weight:700}.hide{display:none!important}
table.sg td{text-align:center;padding:4px}table.sg th{text-align:center}
"""

JS = r"""
const D=JSON.parse(document.getElementById('footy-data').textContent);
const $=(q,el=document)=>el.querySelector(q), $$=(q,el=document)=>[...el.querySelectorAll(q)];
function showTab(id){$$('nav button,section').forEach(e=>e.classList.remove('on'));
 const b=$(`nav button[data-t="${id}"]`);if(b)b.classList.add('on');const s=document.getElementById(id);if(s)s.classList.add('on');
 try{localStorage.setItem('tab',id)}catch(e){}}
$$('nav button').forEach(b=>b.onclick=()=>showTab(b.dataset.t));
try{const t=localStorage.getItem('tab');if(t&&document.getElementById(t))showTab(t)}catch(e){}

/* ---------- generic filters: every section with a .fbar */
function applyFilters(sec){
 const f={};$$('[data-f]',sec).forEach(c=>{f[c.dataset.f]=(c.value||'').toLowerCase()});
 const q=f.q||'';
 $$('details[data-lg]',sec).forEach(d=>d.classList.toggle('hide',!!f.lg&&d.dataset.lg.toLowerCase()!==f.lg));
 $$('tbody tr',sec).forEach(tr=>{let ok=true;
  if(q&&!tr.textContent.toLowerCase().includes(q))ok=false;
  if(f.lg&&tr.dataset.lg&&tr.dataset.lg.toLowerCase()!==f.lg)ok=false;
  if(f.pos&&tr.dataset.pos&&tr.dataset.pos.toLowerCase()!==f.pos)ok=false;
  if(f.maxp&&tr.dataset.price&&parseFloat(tr.dataset.price)>parseFloat(f.maxp))ok=false;
  tr.classList.toggle('hide',!ok)});
 if(q)$$('details',sec).forEach(d=>{if(!d.classList.contains('hide')&&$$('tbody tr:not(.hide)',d).length)d.open=true});
}
$$('section').forEach(sec=>$$('[data-f]',sec).forEach(c=>c.addEventListener('input',()=>applyFilters(sec))));

/* ---------- sortable tables */
function sortBy(th){const tb=th.closest('table').tBodies[0],i=[...th.parentNode.children].indexOf(th);
 const dir=th.dataset.dir==='d'?'a':'d';$$('th',th.parentNode).forEach(x=>delete x.dataset.dir);th.dataset.dir=dir;
 const v=tr=>{const t=(tr.children[i]||{}).textContent||'';const n=parseFloat(t.replace(/[%£+,]/g,''));return isNaN(n)?t.toLowerCase():n};
 const rows=[...tb.rows].sort((a,b)=>{const x=v(a),y=v(b);const c=(typeof x==='number'&&typeof y==='number')?x-y:String(x).localeCompare(String(y));return dir==='a'?c:-c});
 rows.forEach(r=>tb.appendChild(r))}
document.addEventListener('click',e=>{const th=e.target.closest('th.s');if(th)sortBy(th);
 const go=e.target.closest('[data-fx]');if(go){e.preventDefault();openFixture(go.dataset.fx,go.dataset.mk,go.dataset.pr)}});

/* ---------- Poisson / Dixon-Coles */
function pmf(l,n){const p=[Math.exp(-l)];for(let k=1;k<=n;k++)p.push(p[k-1]*l/k);return p}
function matrix(lh,la,rho){const n=10,ph=pmf(lh,n),pa=pmf(la,n);const M=[];let s=0;
 for(let i=0;i<=n;i++){M.push([]);for(let j=0;j<=n;j++){let v=ph[i]*pa[j];
  if(i==0&&j==0)v*=1-lh*la*rho;else if(i==0&&j==1)v*=1+lh*rho;else if(i==1&&j==0)v*=1+la*rho;else if(i==1&&j==1)v*=1-rho;
  v=Math.max(v,0);M[i].push(v);s+=v}}
 return M.map(r=>r.map(v=>v/s))}
function markets(M,hn,an){const n=M.length;let H=0,Dr=0,A=0,bt=0,hcs=0,acs=0;const ov={0.5:0,1.5:0,2.5:0,3.5:0,4.5:0};const ah={'-1.5':0,'-0.5':0,'+0.5':0,'+1.5':0};
 for(let i=0;i<n;i++)for(let j=0;j<n;j++){const p=M[i][j];if(i>j)H+=p;else if(i==j)Dr+=p;else A+=p;if(i&&j)bt+=p;if(j==0)hcs+=p;if(i==0)acs+=p;
  for(const l in ov)if(i+j>l)ov[l]+=p;for(const l in ah)if(i-j+parseFloat(l)>0)ah[l]+=p}
 const out=[['H',hn+' win',H],['D','Draw',Dr],['A',an+' win',A],['1X',hn+' or draw',H+Dr],['X2',an+' or draw',A+Dr],['12','Either team wins',H+A],
  ['DNB_H',hn+' draw no bet',H/(H+A)],['DNB_A',an+' draw no bet',A/(H+A)],['BTTS','Both teams score',bt],['BTTS_N','BTTS - No',1-bt],
  ['H_CS',hn+' clean sheet',hcs],['A_CS',an+' clean sheet',acs]];
 for(const l of [0.5,1.5,2.5,3.5,4.5]){out.push(['O'+l,'Over '+l+' goals',ov[l]]);out.push(['U'+l,'Under '+l+' goals',1-ov[l]])}
 for(const l in ah)out.push(['AH'+l,hn+' '+l+' (handicap)',ah[l]]);
 return out}
const pc=x=>(100*x).toFixed(x<0.1?1:0)+'%', fo=x=>x>0?(1/x).toFixed(2):'-';

/* ---------- Match tab */
const mx={L:$('#mxL'),F:$('#mxF'),H:$('#mxH'),A:$('#mxA'),XH:$('#mxXH'),XA:$('#mxXA'),M:$('#mxMk'),P:$('#mxP')};
function nm(k){return D.names[k]||k}
function lamb(L,h,a){const r=D.ratings[L];if(!r)return[1.3,1.1];const th=r.teams[h]||[-0.15,0.15],ta=r.teams[a]||[-0.15,0.15];
 return[Math.exp(r.mu+r.ha+th[0]+ta[1]),Math.exp(r.mu+ta[0]+th[1])]}
function fillTeams(L,h,a){const ts=Object.keys((D.ratings[L]||{teams:{}}).teams).sort((x,y)=>nm(x).localeCompare(nm(y)));
 const o=ts.map(t=>`<option value="${t}">${nm(t)}</option>`).join('');mx.H.innerHTML=o;mx.A.innerHTML=o;
 mx.H.value=h||ts[0];mx.A.value=a||ts[1]}
function fillFixtures(){const L=mx.L.value;const fx=D.fixtures.filter(f=>!L||f.lg===L);
 mx.F.innerHTML='<option value="">- pick a fixture -</option>'+fx.map(f=>`<option value="${f.id}">${f.day} · ${nm(f.h)} v ${nm(f.a)}</option>`).join('')}
function setXG(){const[lh,la]=lamb(mx.L.value,mx.H.value,mx.A.value);mx.XH.value=lh.toFixed(2);mx.XA.value=la.toFixed(2);mx.F.value='';render()}
function openFixture(id,mk,pr){const f=D.fixtures.find(x=>x.id===id);if(!f)return;mx.L.value=f.lg;fillFixtures();fillTeams(f.lg,f.h,f.a);
 mx.F.value=id;mx.XH.value=f.xh.toFixed(2);mx.XA.value=f.xa.toFixed(2);render();if(mk){mx.M.value=mk;const b=pr?parseFloat(pr):(f.b365||{})[mk];mx.P.value=b?b.toFixed(2):''}
 price();showTab($('#mxsec').id);window.scrollTo(0,0)}
let MK=[];
function render(){const L=mx.L.value,hn=nm(mx.H.value),an=nm(mx.A.value),lh=parseFloat(mx.XH.value)||0.05,la=parseFloat(mx.XA.value)||0.05;
 const rho=(D.ratings[L]||{}).rho||-0.05;const M=matrix(lh,la,rho);MK=markets(M,hn,an);
 const f=D.fixtures.find(x=>x.id===mx.F.value);
 $('#mxNews').innerHTML=f&&(f.nh||f.na)?`<p class="note">Team news already in these xG: ${[f.nh,f.na].filter(Boolean).join(' · ')}</p>`:'';
 const key=['H','D','A','O2.5','U2.5','BTTS','H_CS','A_CS'];
 $('#mxOut').innerHTML=MK.filter(m=>key.includes(m[0])).map(m=>`<div class="mk"><span>${m[1]}</span><b>${pc(m[2])}</b><span>fair ${fo(m[2])}</span></div>`).join('');
 $('#mxAll').innerHTML='<table><thead><tr><th class="l">Market</th><th>Model</th><th>Fair odds</th></tr></thead><tbody>'+
  MK.map(m=>`<tr><td class="l">${m[1]}</td><td>${pc(m[2])}</td><td>${fo(m[2])}</td></tr>`).join('')+'</tbody></table>';
 let g='<table class="sg"><thead><tr><th>'+hn.slice(0,10)+' v '+an.slice(0,10)+'</th>'+[0,1,2,3,4,5].map(j=>`<th>${j}</th>`).join('')+'</tr></thead><tbody>';
 const top=[];for(let i=0;i<=5;i++){g+=`<tr><th>${i}</th>`;for(let j=0;j<=5;j++){g+=`<td>${(100*M[i][j]).toFixed(1)}</td>`;top.push([i+'-'+j,M[i][j]])}g+='</tr>'}
 $('#mxGrid').innerHTML=g+'</tbody></table>';top.sort((a,b)=>b[1]-a[1]);
 $('#mxTop').textContent='Most likely: '+top.slice(0,5).map(t=>`${t[0]} (${pc(t[1])})`).join(', ');
 const cur=mx.M.value;mx.M.innerHTML=MK.map(m=>`<option value="${m[0]}">${m[1]}</option>`).join('');if(cur)mx.M.value=cur;price()}
function price(){const m=MK.find(x=>x[0]===mx.M.value);const o=parseFloat(mx.P.value);const out=$('#mxEdge');
 if(!m){out.textContent='';return}if(!(o>1)){out.innerHTML=`Model ${pc(m[2])} · fair odds <b>${fo(m[2])}</b> - type the bookmaker's price`;return}
 const e=m[2]*o-1,k=Math.min(Math.max((m[2]*o-1)/(o-1),0)*0.25,0.05);
 out.innerHTML=`Model ${pc(m[2])} · fair ${fo(m[2])} · edge <span class="${e>0?'pos':'neg'} big">${(e>0?'+':'')+(100*e).toFixed(1)}%</span> · `+
  (e>0?`quarter-Kelly stake <b>${(100*k).toFixed(1)}%</b> of bankroll`:'no bet')}
if(mx.L){mx.L.innerHTML=Object.keys(D.ratings).map(k=>`<option value="${k}">${D.lgnames[k]||k}</option>`).join('');
 mx.L.onchange=()=>{fillFixtures();fillTeams(mx.L.value);setXG()};mx.F.onchange=()=>mx.F.value?openFixture(mx.F.value):null;
 mx.H.onchange=setXG;mx.A.onchange=setXG;mx.XH.oninput=render;mx.XA.oninput=render;mx.M.onchange=price;mx.P.oninput=price;
 const first=D.fixtures[0];if(first){mx.L.value=first.lg;fillFixtures();fillTeams(first.lg,first.h,first.a);mx.F.value=first.id;
  mx.XH.value=first.xh.toFixed(2);mx.XA.value=first.xa.toFixed(2);render()}else{fillFixtures();fillTeams(mx.L.value);setXG()}}

/* ---------- Value tab (rendered from data so the filters can re-rank it) */
function renderValue(){const box=$('#vbox');if(!box)return;const me=parseFloat($('#vMin').value)/100||0,bk=$('#vBook').value,
 mk=$('#vMk').value,lg=($('#vLg').value||''),q=($('#vQ').value||'').toLowerCase(),mx_=parseFloat($('#vMax').value)||99;
 const rows=D.value.filter(v=>v.book===bk&&v.edge>=me&&v.price<=mx_&&(!mk||v.market===mk)&&(!lg||v.lg===lg)&&
  (!q||(v.match+' '+v.sel).toLowerCase().includes(q))).sort((a,b)=>b.edge-a.edge);
 $('#vCount').textContent=rows.length+' selection'+(rows.length==1?'':'s');
 box.innerHTML=rows.length?'<div class="tw"><table><thead><tr><th class="l s">Date</th><th class="l s">League</th><th class="l s">Match</th><th class="l s">Pick</th><th class="s">Model</th><th class="s">Mkt</th><th class="s">Fair</th><th class="s">Price</th><th class="s">Edge</th><th class="s">Stake</th><th class="l">News</th><th></th></tr></thead><tbody>'+
  rows.map(v=>`<tr><td class="l">${v.day}</td><td class="l">${D.lgnames[v.lg]||v.lg}</td><td class="l">${v.match}</td><td class="l">${v.sel}</td><td>${pc(v.p)}</td><td>${v.mp!=null?pc(v.mp):''}</td><td>${fo(v.p)}</td><td>${v.price.toFixed(2)}</td><td><span class="${v.edge>0?'pos':'neg'}">${(v.edge>0?'+':'')+(100*v.edge).toFixed(1)}%</span></td><td>${(100*v.stake).toFixed(1)}%</td><td class="l">${v.news||''}</td><td><a class="go" href="#" data-fx="${v.id}" data-mk="${v.mkkey}" data-pr="${v.price}">Check ›</a></td></tr>`).join('')+'</tbody></table></div>'
  :`<p class="note">${D.value.length?'No selections match these filters - lower the minimum edge.':'No bookmaker prices for the coming fixtures yet: football-data.co.uk publishes them a day or two before each round. Until then use the fair odds below and type the Bet365 price into the Match tab.'}</p>`}
$$('#vMin,#vBook,#vMk,#vLg,#vQ,#vMax').forEach(c=>c&&c.addEventListener('input',renderValue));renderValue();
"""


def _fmt(v, kind):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if kind == "pct":
        return f"{v:.0%}"
    if kind == "pct1":
        return f"{v:.1%}"
    if kind == "signed_pct":
        cls = "pos" if v > 0 else "neg"
        return f'<span class="{cls}">{v:+.1%}</span>'
    if kind == "signed":
        cls = "pos" if v > 0 else "neg" if v < 0 else ""
        return f'<span class="{cls}">{v:+.1f}</span>'
    if kind == "f2":
        return f"{v:.2f}"
    if kind == "f1":
        return f"{v:.1f}"
    if kind == "int":
        return f"{int(round(v))}"
    if kind == "html":
        return str(v)
    return html.escape(str(v))


def table(df: pd.DataFrame, spec: list[tuple[str, str, str]], attrs: dict[str, str] | None = None) -> str:
    """spec: (column, header, kind); attrs: data-attribute name -> column (used by the page's filters)."""
    if df is None or df.empty:
        return '<p class="note">Nothing to show yet.</p>'
    spec = [s for s in spec if s[0] in df.columns]
    attrs = {k: c for k, c in (attrs or {}).items() if c in df.columns}
    left = ("text", "html")
    head = "".join(f'<th class="s {"l" if k in left else ""}">{html.escape(h)}</th>' for _, h, k in spec)
    rows = []
    for _, r in df.iterrows():
        a = "".join(f' data-{k}="{html.escape(str(r[c]))}"' for k, c in attrs.items() if pd.notna(r[c]))
        cells = "".join(f'<td class="{"l" if k in left else ""}">{_fmt(r[c], k)}</td>' for c, _, k in spec)
        rows.append(f"<tr{a}>{cells}</tr>")
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def fbar(leagues: list[tuple[str, str]] | None = None, extra: str = "", placeholder: str = "Search team or player") -> str:
    """Filter bar: search box + optional league picker (+ extra controls)."""
    lg = ""
    if leagues:
        opts = "".join(f'<option value="{html.escape(k)}">{html.escape(n)}</option>' for k, n in leagues)
        lg = f'<select data-f="lg" aria-label="League"><option value="">All leagues</option>{opts}</select>'
    return (f'<div class="fbar"><input type="search" data-f="q" placeholder="{placeholder}" aria-label="Search">'
            f'{lg}{extra}</div>')


def tickets_section() -> str:
    """Upcoming LFC sale dates and credit levels, from `python -m footy tickets check` state."""
    from .tickets import alerts, credits as cr
    state = alerts.load_state()
    cfg = cr.load()
    if not state.get("events") and not cfg:
        return ""
    bal = cr.balances(cfg)
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for e, who in alerts._groups([e for e in alerts.events_from_state(state)
                                  if e.when > now and alerts._wanted(e, cfg, bal)]):
        rows.append({"when": alerts._fmt_when(e), "what": e.title, "who": who,
                     "you": {"eligible": "✓ you qualify", "not_eligible": "not enough credits",
                             "higher_stage": "earlier stage for you", "unknown": ""}[cr.eligibility(e, bal)]})
    ev = pd.DataFrame(rows)
    lv = pd.DataFrame(cr.levels(cfg))
    ex = pd.DataFrame([{"match": f["name"], "comp": f["competition"],
                        "ko": dt.datetime.fromisoformat(f["kickoff"]).strftime("%a %d %b %H:%M") if f.get("kickoff") else "TBC",
                        "exchange": {"open": "OPEN", "none": "none listed", "closed": "closed"}.get(f["exchange"], "-"),
                        "watch": "✓" if f.get("watch") else ""} for f in state.get("fixtures", {}).values()])
    checked = state.get("checked_at", "")[:16].replace("T", " ")
    err = f'<p class="note neg">Last check failed: {html.escape(state["last_error"])}</p>' if state.get("last_error") else ""
    out = [f'<p class="note">Read from liverpoolfc.com\'s public ticket pages (last checked {checked} UK). '
           'Always confirm on the club site - this never buys anything for you.</p>', err,
           '<p><a id="webcal" href="lfc-tickets.ics">📅 Subscribe to sale dates in your calendar</a></p>'
           "<script>(()=>{const a=document.getElementById('webcal');a.href='webcal://'+location.host+"
           "location.pathname.replace(/[^/]*$/,'')+'lfc-tickets.ics'})()</script>",
           "<h2>Upcoming sale dates</h2>",
           table(ev, [("when", "When (UK)", "text"), ("what", "What", "text"), ("who", "Fixture", "text"),
                      ("you", "You", "text")]) if not ev.empty else '<p class="note">None announced yet.</p>']
    if not lv.empty:
        out += ["<h2>Your credits</h2>", table(lv, [("category", "Category", "text"), ("credits", "Credits", "int"),
                                                   ("level", "Level", "text"), ("next_level", "Next level", "text"),
                                                   ("games_needed", "Games to go", "int")])]
    if not ex.empty:
        out += ["<h2>Ticket Exchange</h2>", table(ex, [("match", "Match", "text"), ("comp", "Competition", "text"),
                                                      ("ko", "Kick-off", "text"), ("exchange", "Exchange", "text"),
                                                      ("watch", "Alerts", "text")])]
    return "".join(out)


def _json(obj) -> str:
    import json
    def conv(o):
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else round(float(o), 4)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, float):
            return None if np.isnan(o) else round(o, 4)
        if isinstance(o, dict):
            return {str(k): conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(v) for v in o]
        return o
    return json.dumps(conv(obj), separators=(",", ":")).replace("</", "<\\/")


def build(out: Path | str = "site/index.html", days: int = 7, min_edge: float = 0.05) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    cur = config.current_season()
    frame = match_frame(con, None, [cur - 2, cur - 1, cur])
    names = team_names(con)
    nm = lambda k: names.get(k, k)  # noqa: E731
    lgs = config.leagues()
    ln = lambda k: lgs[k].name if k in lgs else k  # noqa: E731
    order = [k for k in lgs if k in set(frame.league)] if not frame.empty else []
    ratings = fit_all(frame, order, elo=latest_elo(con, frame)) if order else {}
    sections: dict[str, str] = {}
    lg_opts = lambda keys: [(k, ln(k)) for k in order if k in set(keys)]  # noqa: E731

    # ------------------------------------------------------------------ predictions
    pred = predict.predict_fixtures(con, frame, ratings, days) if ratings else pd.DataFrame()
    if not pred.empty:
        pred["home_name"], pred["away_name"] = pred.home.map(nm), pred.away.map(nm)
        pred["match"] = pred.home_name + " v " + pred.away_name
        pred["day"] = pred.date.dt.strftime("%a %d %b")
        pred["check"] = [f'<a class="go" href="#" data-fx="{html.escape(m)}">Check ›</a>' for m in pred.match_id]
        pred = pred.sort_values(["date", "kickoff", "league"], na_position="last")

    # data for the in-page match explorer and value table
    data = {"names": {}, "lgnames": {k: ln(k) for k in order}, "ratings": {}, "fixtures": [], "value": []}
    cur_teams = {}
    for k in order:
        c = frame[(frame.league == k) & (frame.season == cur)]
        cur_teams[k] = set(c.home) | set(c.away)
    for k, r in ratings.items():
        teams = [t for t in r.teams if t in cur_teams.get(k, set())] or list(r.teams)
        data["ratings"][k] = {"mu": r.mu, "ha": r.home_adv, "rho": r.rho,
                              "teams": {t: [r.att.get(t, -0.15), r.dfn.get(t, 0.15)] for t in teams}}
        for t in teams:
            data["names"][t] = nm(t)
    if not pred.empty:
        for _, r in pred.iterrows():
            data["fixtures"].append({
                "id": r.match_id, "lg": r.league, "day": r.day, "h": r.home, "a": r.away,
                "xh": r.xg_home, "xa": r.xg_away, "nh": r.get("news_home") or "", "na": r.get("news_away") or "",
                "b365": {k: r.get(f"b365_{k}") for k in ("H", "D", "A", "O2.5", "U2.5")
                         if pd.notna(r.get(f"b365_{k}"))}})
            data["names"][r.home], data["names"][r.away] = r.home_name, r.away_name
        for book in ("b365", "best"):
            vb = predict.value_bets(pred, -1.0, max_price=100, book=book)
            for _, v in vb.iterrows():
                data["value"].append({
                    "id": v.match_id, "mkkey": v.sel_key, "book": book, "lg": v.league,
                    "day": pd.Timestamp(v.date).strftime("%a %d %b"), "match": v.match,
                    "sel": v.selection if v.market == "1X2" else v.market, "market": v.market,
                    "p": v.model_p, "mp": v.market_p, "price": v.price, "edge": v.edge, "stake": v.stake_pct,
                    "news": v.team_news})

    # ------------------------------------------------------------------ Value
    mk_opts = "".join(f'<option value="{m}">{m}</option>' for m in ("1X2", "Over 2.5", "Under 2.5"))
    lgo = "".join(f'<option value="{k}">{html.escape(n)}</option>' for k, n in lg_opts(pred.league if not pred.empty else []))
    has_prices = bool(data["value"])
    fair = pred.copy() if not pred.empty else pred
    if not fair.empty:
        for k in ("H", "D", "A", "O2.5", "BTTS"):
            fair[f"fair_{k}"] = 1 / fair[k]
        fair["league_name"] = fair.league.map(ln)
    sections["Value"] = (
        '<p class="note">Model probability × price − 1 = edge. Expected goals already allow for listed '
        "injuries and suspensions. Bet365 prices are football-data's snapshot (usually a day or two before "
        "kick-off) - always check the live price; tap <b>Check ›</b> to re-price any bet in the Match tab.</p>"
        f'<div class="fbar{"" if has_prices else " hide"}"><label>Min edge %<input id="vMin" type="number" value="{int(min_edge * 100)}" step="1"></label>'
        '<label>Prices<select id="vBook"><option value="b365">Bet365</option><option value="best">Best available</option>'
        '</select></label>'
        f'<label>Market<select id="vMk"><option value="">All</option>{mk_opts}</select></label>'
        f'<label>League<select id="vLg"><option value="">All</option>{lgo}</select></label>'
        '<label>Max odds<input id="vMax" type="number" value="8" step="0.5"></label>'
        '<label>Search<input id="vQ" type="search" placeholder="Team"></label>'
        '<span class="sub" id="vCount"></span></div><div id="vbox"></div>'
        + ("" if has_prices else "<h2>Model fair odds - compare with Bet365</h2>"
           '<p class="note">If Bet365 offers <b>more</b> than the fair odds, the model sees value.</p>'
           + fbar(lg_opts(fair.league if not fair.empty else []), placeholder="Search team")
           + table(fair, [("day", "Date", "text"), ("league_name", "League", "text"), ("match", "Match", "text"),
                          ("fair_H", "Home", "f2"), ("fair_D", "Draw", "f2"), ("fair_A", "Away", "f2"),
                          ("fair_O2.5", "O2.5", "f2"), ("fair_BTTS", "BTTS", "f2"), ("check", "", "html")],
                   attrs={"lg": "league"})))

    # ------------------------------------------------------------------ Match explorer
    sections["Match"] = (
        '<p class="note">Pick a fixture (or any two teams), adjust expected goals for team news if you like, '
        "then type a bookmaker's price to see your edge. Works for every league and European competition.</p>"
        '<div class="card"><div class="mxform">'
        '<label>Competition<select id="mxL"></select></label>'
        '<label>Fixture<select id="mxF"></select></label>'
        '<label>Home<select id="mxH"></select></label><label>Away<select id="mxA"></select></label>'
        '<label>Home xG<input id="mxXH" type="number" step="0.05" min="0.05"></label>'
        '<label>Away xG<input id="mxXA" type="number" step="0.05" min="0.05"></label>'
        '</div><div id="mxNews"></div></div>'
        '<div class="grid" id="mxOut"></div>'
        '<div class="card"><h2>Check a price</h2><div class="mxform">'
        '<label>Market<select id="mxMk"></select></label>'
        '<label>Bookmaker odds (decimal)<input id="mxP" type="number" step="0.01" min="1.01" placeholder="e.g. 2.10"></label>'
        '</div><p id="mxEdge"></p></div>'
        '<h2>Scorelines (%)</h2><div class="tw" id="mxGrid"></div><p class="note" id="mxTop"></p>'
        '<details><summary>All markets</summary><div class="tw" id="mxAll"></div></details>')

    # ------------------------------------------------------------------ Fixtures
    fx_html = []
    for k in order:
        p = pred[pred.league == k] if not pred.empty else pred
        if p.empty:
            continue
        euro_note = " <span class='sub'>(no free odds - use Check ›)</span>" if lgs.get(k) and lgs[k].is_europe else ""
        fx_html.append(f'<details open data-lg="{k}"><summary>{ln(k)} · {len(p)} matches{euro_note}</summary>' + table(
            p, [("day", "Date", "text"), ("match", "Match", "text"), ("xg_home", "xG H", "f2"),
                ("xg_away", "xG A", "f2"), ("H", "Home", "pct"), ("D", "Draw", "pct"), ("A", "Away", "pct"),
                ("mkt_H", "Mkt H", "pct"), ("mkt_D", "Mkt D", "pct"), ("mkt_A", "Mkt A", "pct"),
                ("O2.5", "O2.5", "pct"), ("BTTS", "BTTS", "pct"), ("H_CS", "H CS", "pct"),
                ("A_CS", "A CS", "pct"), ("exp_cards", "Cards", "f1"), ("referee", "Referee", "text"),
                ("news_home", "Home news", "text"), ("news_away", "Away news", "text"), ("check", "", "html")],
            attrs={"lg": "league"}) + "</details>")
    sections["Fixtures"] = ('<p class="note">Model probabilities for the next week; "Mkt" = bookmaker odds '
                            "with the margin removed. Tap a column header to sort.</p>"
                            + fbar(lg_opts(pred.league if not pred.empty else []), placeholder="Search team")
                            + ("".join(fx_html) or '<p class="note">No upcoming fixtures.</p>'))

    # ------------------------------------------------------------------ Cards
    if not pred.empty and "exp_cards" in pred:
        c = pred.sort_values("exp_cards", ascending=False).copy()
        c["league_name"] = c.league.map(ln)
        sections["Cards"] = ('<p class="note">Expected cards from both teams\' card rates and the referee\'s '
                             "strictness (1.00 = league average). Over-lines use a negative binomial.</p>"
                             + fbar(lg_opts(c.league), placeholder="Search team or referee") + table(
            c, [("day", "Date", "text"), ("league_name", "League", "text"), ("match", "Match", "text"),
                ("referee", "Referee", "text"), ("ref_strictness", "Ref x", "f2"), ("exp_cards", "Exp cards", "f1"),
                ("cards_O3.5", "O3.5", "pct"), ("cards_O4.5", "O4.5", "pct"), ("cards_O5.5", "O5.5", "pct")],
            attrs={"lg": "league"}))

    # ------------------------------------------------------------------ Tables + regression
    tb_html, reg_rows = [], []
    for k in order:
        f = frame[(frame.league == k) & (frame.season == cur)]
        t = form.league_table(f)
        if t.empty:
            continue
        t["team"] = t.team.map(nm)
        share = t.real_xg_share.mean()
        kind = "real xG" if share > 0.9 else "shots-based proxy xG" if share < 0.1 else f"{share:.0%} real xG"
        tb_html.append(f"<details data-lg=\"{k}\"><summary>{ln(k)} <span class='sub'>({kind})</span></summary>" + table(
            t, [("Pos", "#", "int"), ("team", "Team", "text"), ("P", "P", "int"), ("Pts", "Pts", "int"),
                ("GD", "GD", "int"), ("xG", "xG", "f1"), ("xGA", "xGA", "f1"), ("xGD", "xGD", "signed"),
                ("xPts", "xPts", "f1"), ("xPos", "xPos", "int"), ("Pts-xPts", "Pts−xPts", "signed"),
                ("G-xG", "G−xG", "signed"), ("GA-xGA", "GA−xGA", "signed"), ("BC/90", "BC/g", "f1"),
                ("BCA/90", "BCA/g", "f1"), ("SoT", "SoT/g", "f1"), ("Corners", "Corn/g", "f1")]) + "</details>")
        fl = form.regression_flags(t, 6)
        fl = fl[fl.verdict != "In line with performances"]
        for _, r in fl.iterrows():
            reg_rows.append(dict(lg=k, league=ln(k), team=r.team, Pts=r.Pts, xPts=r.xPts, luck=r.luck_score,
                                 verdict=r.verdict))
    reg = pd.DataFrame(reg_rows)
    if not reg.empty:
        reg = reg.reindex(reg.luck.abs().sort_values(ascending=False).index)
    ls_html = []
    for k in order:
        if lgs.get(k) and lgs[k].is_europe and k in ratings:
            ls = getattr(ratings[k], "league_strength", None)
            if ls is not None and not ls.empty:
                ls_html.append("<details><summary>League strength (for European games)</summary>" + table(
                    ls.assign(league=ls.league.map(ln)),
                    [("league", "League", "text"), ("strength", "Strength", "f2"),
                     ("elo_prior", "From Elo", "f2"), ("from_results", "Adj. from results", "f2")]) + "</details>")
                break
    sections["Tables"] = ('<p class="note">Actual table vs expected (xPts = points the chances deserved). '
                          "Pts−xPts and G−xG show who's riding luck. Tap a header to sort.</p>"
                          + fbar([(k, ln(k)) for k in order if k in cur_teams and cur_teams[k]], placeholder="Search team")
                          + "".join(tb_html) + "".join(ls_html))
    sections["Regression"] = ('<p class="note">Teams whose points per game differ from their xPts by more than '
                              "0.35 - classic candidates to oppose or back.</p>"
                              + fbar(lg_opts(reg.lg) if not reg.empty else None, placeholder="Search team") + table(
        reg, [("league", "League", "text"), ("team", "Team", "text"), ("Pts", "Pts", "int"),
              ("xPts", "xPts", "f1"), ("luck", "±Pts/g", "signed"), ("verdict", "Verdict", "text")],
        attrs={"lg": "lg"}))

    # ------------------------------------------------------------------ FPL
    if "E0" in ratings:
        xp, tick = fpl.expected_points(con, ratings["E0"], 5)
        if not xp.empty:
            xp["team"] = xp.team.map(nm)
            xp = xp[xp.start_rate >= 0.3]
            gw = [c for c in xp.columns if c.startswith("GW")]
            spec = [("player", "Player", "text"), ("team", "Team", "text"), ("position", "Pos", "text"),
                    ("price", "£m", "f1"), ("selected_pct", "Own%", "f1"), ("start_rate", "Starts", "pct"),
                    *[(g, g, "f1") for g in gw], ("xPts_5gw", "5GW", "f1"), ("xPts_per_m", "per £m", "f2"),
                    ("news", "News", "text")]
            extra = ('<select data-f="pos" aria-label="Position"><option value="">All positions</option>'
                     + "".join(f'<option value="{p}">{p}</option>' for p in ("GK", "DEF", "MID", "FWD")) + "</select>"
                     '<select data-f="maxp" aria-label="Max price"><option value="">Any price</option>'
                     + "".join(f'<option value="{v}">≤ £{v}m</option>' for v in (4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0,
                                                                              9.0, 10.0, 11.0, 12.0)) + "</select>")
            parts = ['<p class="note">Expected FPL points over the next 5 gameweeks. Built from FPL xG/xA, fixture '
                     "difficulty from the team model, clean-sheet odds, recent starts, defensive contributions, "
                     "saves and bonus. Filter by position/price; tap a header to sort (e.g. per £m).</p>",
                     fbar(None, extra, placeholder="Search player or team"),
                     table(xp.head(250), spec, attrs={"pos": "position", "price": "price"})]
            if not tick.empty:
                tick["team"] = tick.team.map(nm)
                tick["opp"] = (tick.opponent.map(nm).str[:3].str.upper() + np.where(tick.home, "(H)", "(a)")
                               + np.where(tick.after_europe, "★", ""))
                agg = tick.groupby("team").agg(xg=("xg_for", "sum"), cs=("p_cs", "sum")).reset_index()
                fx = tick.sort_values("gw").groupby("team").opp.agg(" ".join)
                agg["fixtures"] = agg.team.map(fx)
                parts.append("<details><summary>Fixture ticker (next 5 GWs)</summary><p class='note'>★ = plays in "
                             "Europe 1-4 days before (rotation risk).</p>" + table(
                    agg.sort_values("xg", ascending=False),
                    [("team", "Team", "text"), ("fixtures", "Fixtures", "text"),
                     ("xg", "Exp. goals", "f1"), ("cs", "Exp. clean sheets", "f1")]) + "</details>")
            sections["FPL"] = "".join(parts)

    # ------------------------------------------------------------------ Players
    pt = props.player_table(con, None, [cur])
    pl_html = []
    if not pt.empty:
        pt = pt[pt.minutes >= 360]
        pt["team"] = pt.team.map(nm)
        for k in order:
            p = pt[pt.league == k]
            if p.empty:
                continue
            p = p.sort_values("xgi_p90", ascending=False).head(60)
            pl_html.append(f'<details data-lg="{k}"><summary>{ln(k)}</summary>' + table(p, [
                ("player", "Player", "text"), ("team", "Team", "text"), ("minutes", "Mins", "int"),
                ("goals", "G", "int"), ("xg", "xG", "f1"), ("g_minus_xg", "G−xG", "signed"),
                ("assists", "A", "int"), ("xa", "xA", "f1"), ("xg_p90", "xG/90", "f2"), ("xa_p90", "xA/90", "f2"),
                ("shots_p90", "Sh/90", "f1"), ("big_chances_created", "BCC", "int")]) + "</details>")
    sections["Players"] = ('<p class="note">Top 60 per league by xG + xA per 90 (min 360 minutes). Search finds a '
                           "player in any league.</p>"
                           + fbar(lg_opts(pt.league) if not pt.empty else None, placeholder="Search player or team")
                           + ("".join(pl_html) or '<p class="note">No player data yet.</p>'))

    # ------------------------------------------------------------------ Data
    cov = coverage(con)
    if not cov.empty:
        cov["league"] = cov.league.map(ln)
        cov["season"] = cov.season.map(config.season_label)
    if tk := tickets_section():
        sections["LFC tickets"] = tk
    sections["Data"] = table(cov, [("league", "League", "text"), ("season", "Season", "text"),
                                   ("played", "Played", "int"), ("with_xg", "Real xG", "int"),
                                   ("xg_sources", "xG from", "text"), ("with_big_chances", "Big chances", "int"),
                                   ("with_shots", "Shots", "int"), ("with_odds", "Odds", "int")])

    # Match tab second, right after Value
    keys = list(sections)
    keys.insert(1, keys.pop(keys.index("Match")))
    sections = {k: sections[k] for k in keys}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
    ids = {k: ("mxsec" if k == "Match" else f"s{i}") for i, k in enumerate(sections)}
    nav = "".join(f'<button data-t="{ids[k]}" class="{"on" if i == 0 else ""}">{k}</button>'
                  for i, k in enumerate(sections))
    body = "".join(f'<section id="{ids[k]}" class="{"on" if i == 0 else ""}">{v}</section>'
                   for i, (k, v) in enumerate(sections.items()))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Footy Edge</title>
<meta name="apple-mobile-web-app-capable" content="yes"><style>{CSS}</style></head><body>
<header><h1>⚽ Footy Edge</h1><div class="sub">Updated {stamp} · {config.season_label(cur)}</div></header>
<nav><div>{nav}</div></nav><main>{body}</main>
<footer>Probabilities, not certainties. Bet only what you can afford to lose · BeGambleAware.org · 0808 8020 133</footer>
<script type="application/json" id="footy-data">{_json(data)}</script>
<script>{JS}</script></body></html>"""
    out.write_text(page, encoding="utf-8")
    return out

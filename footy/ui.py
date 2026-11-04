"""App shell for the phone page: design, bottom navigation, and the interactive screens
(Today, Matches, match detail with every market, Bet builder, My bets with closing-line value).

report.py builds the data and the data-heavy sections; this module lays them out and provides
the script that renders the interactive parts on the phone.
"""

from __future__ import annotations

import html

# ------------------------------------------------------------------ navigation
# section id -> (title, group). Groups are the five bottom tabs.
SECTIONS = {
    "today": ("Today", "today"),
    "matches": ("Matches", "matches"),
    "fixtures": ("Table view", "matches"),
    "mxsec": ("Match", "matches"),
    "value": ("Value", "bets"),
    "builder": ("Builder", "bets"),
    "mybets": ("My bets", "bets"),
    "fpl": ("FPL", "fpl"),
    "more": ("More", "more"),
    "team": ("Team", "more"),
    "props": ("Player props", "more"),
    "tables": ("League tables", "more"),
    "regression": ("Regression", "more"),
    "cards": ("Cards & referees", "more"),
    "players": ("Players", "more"),
    "backtest": ("Backtest", "more"),
    "tickets": ("LFC tickets", "more"),
    "data": ("Data", "more"),
}
TABS = [("today", "Today", "⌂"), ("matches", "Matches", "⚽"), ("bets", "Bets", "£"), ("fpl", "FPL", "★"),
        ("more", "More", "☰")]
SEGMENTS = {"matches": ["matches", "fixtures", "mxsec"], "bets": ["value", "builder", "mybets"]}
SEG_LABEL = {"matches": "Cards", "fixtures": "Table", "mxsec": "Calculator", "value": "Value",
             "builder": "Builder", "mybets": "My bets"}
MORE_ICONS = {"team": "📈", "props": "🎯", "tables": "📊", "regression": "🔄", "cards": "🟨", "players": "👟",
              "backtest": "🧪", "tickets": "🎟️", "data": "🗂️"}


CSS = """
:root{--bg:#f2f3f5;--card:#fff;--ink:#16181d;--muted:#6b7078;--line:#e3e5e8;--accent:#0b7a55;--accent2:#e6f4ee;
--pos:#0b7a55;--neg:#c2341f;--home:#0b7a55;--draw:#9aa1a9;--away:#3563c9;--shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08)}
@media (prefers-color-scheme:dark){:root{--bg:#0e1013;--card:#181b20;--ink:#eceef1;--muted:#99a0a8;--line:#272b31;
--accent:#3ccf91;--accent2:#15291f;--pos:#3ccf91;--neg:#ff7b6b;--home:#3ccf91;--draw:#6c737b;--away:#7aa2ff;--shadow:none}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,sans-serif}
.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(12px);
-webkit-backdrop-filter:blur(12px);padding:calc(env(safe-area-inset-top) + 10px) 16px 8px}
.top .row{max-width:760px;margin:auto;display:flex;align-items:center;gap:10px}
.top h1{font-size:20px;margin:0;font-weight:800;letter-spacing:-.3px}.top .upd{margin-left:auto;font-size:12px;color:var(--muted)}
.back{border:0;background:none;color:var(--accent);font:inherit;font-weight:600;padding:4px 0;cursor:pointer}
main{max-width:760px;margin:auto;padding:4px 16px calc(96px + env(safe-area-inset-bottom))}
section{display:none}section.on{display:block}
.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:30;background:var(--card);border-top:1px solid var(--line);
display:flex;justify-content:space-around;padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
.tabbar button{flex:1;border:0;background:none;color:var(--muted);font:inherit;font-size:11px;font-weight:600;
display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 0;cursor:pointer}
.tabbar button span{font-size:20px;line-height:1}.tabbar button.on{color:var(--accent)}
.seg{display:flex;background:var(--line);border-radius:10px;padding:3px;margin:6px 0 12px}
.seg button{flex:1;border:0;background:none;font:inherit;font-size:14px;font-weight:600;color:var(--muted);padding:7px 4px;
border-radius:8px;cursor:pointer}.seg button.on{background:var(--card);color:var(--ink);box-shadow:var(--shadow)}
h2{font-size:17px;margin:18px 0 8px;letter-spacing:-.2px}h3{font-size:15px;margin:12px 0 6px}
.card{background:var(--card);border-radius:14px;padding:12px 14px;margin:10px 0;box-shadow:var(--shadow);border:1px solid var(--line)}
.card h2{margin:2px 0 8px}
.note{color:var(--muted);font-size:13px;margin:4px 0 10px}.sub{color:var(--muted);font-size:12px}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}.hide{display:none!important}
details{background:var(--card);border:1px solid var(--line);border-radius:14px;margin:10px 0;padding:0 14px;box-shadow:var(--shadow)}
summary{padding:12px 0;font-weight:600;cursor:pointer}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-bottom:12px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:600;background:var(--card);position:sticky;top:0}
td:first-child,th:first-child,td.l,th.l{text-align:left}
th.s{cursor:pointer;user-select:none}th.s:after{content:" ⇅";opacity:.35;font-size:11px}
.fbar{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin:8px 0 12px}
.fbar label,.form label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:3px}
input,select,textarea{font:inherit;font-size:16px;color:var(--ink);background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:8px 10px;min-height:40px}input[type=search]{min-width:160px;flex:1}input[type=number]{width:96px}
.form{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
button.b{font:inherit;font-weight:700;border:0;background:var(--accent);color:#fff;border-radius:10px;padding:9px 14px;cursor:pointer;min-height:40px}
button.b.ghost{background:var(--accent2);color:var(--accent)}
a.go{color:var(--accent);font-weight:600;text-decoration:none}
.chips{display:flex;gap:6px;overflow-x:auto;padding:2px 0 8px;scrollbar-width:none}.chips::-webkit-scrollbar{display:none}
.chip{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 12px;font:inherit;
font-size:13px;font-weight:600;white-space:nowrap;cursor:pointer}.chip.on{background:var(--ink);color:var(--card);border-color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin:8px 0}
.mk{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 11px}
.mk b{display:block;font-size:20px;letter-spacing:-.3px}.mk span{font-size:12px;color:var(--muted)}
.mk i,.pill{font-style:normal;display:inline-block;min-width:20px;text-align:center;border-radius:5px;padding:1px 4px;
margin-right:3px;font-size:12px;font-weight:700;color:#fff;background:var(--draw)}
.pill.W,.mk i.pos{background:var(--pos);color:#fff}.pill.L,.mk i.neg{background:var(--neg);color:#fff}.pill.D{background:var(--draw)}
.mk i.pos,.mk i.neg{font-weight:700}
.badge{width:26px;height:26px;border-radius:50%;flex:none;object-fit:contain;background:var(--line);display:inline-flex;
align-items:center;justify-content:center;font-size:10px;font-weight:800;color:#fff}
.fx{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 12px;margin:8px 0;box-shadow:var(--shadow);cursor:pointer}
.fx .meta{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:6px}
.fx .tm{display:flex;align-items:center;gap:8px;margin:3px 0;font-weight:600}.fx .tm .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fx .tm .p{font-variant-numeric:tabular-nums;font-weight:700}
.bar{display:flex;height:6px;border-radius:3px;overflow:hidden;margin:8px 0 6px;background:var(--line)}
.bar i{display:block;height:100%}.bar .h{background:var(--home)}.bar .d{background:var(--draw)}.bar .a{background:var(--away)}
.fx .foot{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.fx .foot b{color:var(--ink)}
.dayh{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin:16px 2px 4px}
.hero{display:flex;align-items:center;justify-content:space-between;gap:8px;text-align:center}
.hero .t{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;font-weight:700}
.hero .badge{width:44px;height:44px;font-size:14px}.hero .vs{color:var(--muted);font-size:13px}
.mrow{display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid var(--line)}
.mrow:last-child{border-bottom:0}.mrow .l{flex:1}.mrow .p{width:52px;text-align:right;font-weight:700}
.mrow .o{width:52px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.add{border:1px solid var(--line);background:var(--card);color:var(--accent);font-weight:800;border-radius:8px;width:34px;height:30px;cursor:pointer;font-size:16px}
.add.in{background:var(--accent);color:#fff;border-color:var(--accent)}
.kpi{display:flex;gap:8px;overflow-x:auto}.kpi .mk{min-width:120px}
.slipbar{position:fixed;right:14px;bottom:calc(76px + env(safe-area-inset-bottom));z-index:25;background:var(--ink);color:var(--card);
border-radius:999px;padding:10px 16px;font-weight:700;box-shadow:0 4px 14px rgba(0,0,0,.25);cursor:pointer}
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.tile{background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:14px;box-shadow:var(--shadow);cursor:pointer;font-weight:700}.tile span{display:block;font-size:24px;margin-bottom:6px}
.tile small{display:block;color:var(--muted);font-weight:500;font-size:12px;margin-top:2px}
table.sg td{text-align:center;padding:5px}table.sg th{text-align:center}
.big{font-size:22px;font-weight:800}
footer{color:var(--muted);font-size:12px;text-align:center;padding:16px 0 0}
"""


def section_shell(sid: str, inner: str) -> str:
    title, group = SECTIONS.get(sid, (sid, "more"))
    head = ""
    if group in SEGMENTS and sid in SEGMENTS[group]:
        btns = "".join(f'<button data-go="{s}" class="{"on" if s == sid else ""}">{SEG_LABEL[s]}</button>'
                       for s in SEGMENTS[group])
        head = f'<div class="seg">{btns}</div>'
    elif group == "more" and sid != "more":
        head = f'<button class="back" data-go="more">‹ More</button><h2>{html.escape(title)}</h2>'
    return f'<section id="{sid}" data-group="{group}">{head}{inner}</section>'


def more_menu(present: list[str], blurbs: dict[str, str]) -> str:
    tiles = "".join(f'<div class="tile" data-go="{s}"><span>{MORE_ICONS.get(s, "•")}</span>{html.escape(SECTIONS[s][0])}'
                    f'<small>{html.escape(blurbs.get(s, ""))}</small></div>'
                    for s in present if SECTIONS.get(s, ("", ""))[1] == "more" and s != "more")
    return f'<h2>More</h2><div class="tiles">{tiles}</div>'


TODAY_HTML = '<div id="tdOut"></div>'
MATCHES_HTML = ('<div class="chips" id="mlChips"></div>'
                '<input type="search" id="mlQ" placeholder="Search team" style="width:100%;margin-bottom:4px">'
                '<div id="mlOut"></div>')
MATCH_HTML = """
<div class="card" id="mdHero"></div>
<div class="card"><div class="form">
<label>Home xG<input id="mxXH" type="number" step="0.05" min="0.05"></label>
<label>Away xG<input id="mxXA" type="number" step="0.05" min="0.05"></label></div>
<p class="note" id="mxNews" style="margin:8px 0 0"></p>
<details style="box-shadow:none;margin:8px 0 0"><summary>Change match / any two teams</summary><div class="form" style="padding-bottom:12px">
<label>Competition<select id="mxL"></select></label><label>Fixture<select id="mxF"></select></label>
<label>Home<select id="mxH"></select></label><label>Away<select id="mxA"></select></label></div></details></div>
<div class="chips" id="mdGroups"></div>
<div class="card" id="mdList"></div>
<div class="card"><h2>Check a price</h2><div class="form">
<label>Market<select id="mxMk"></select></label>
<label>Bookmaker odds<input id="mxP" type="number" step="0.01" min="1.01" placeholder="e.g. 2.10"></label>
<label>Stake<input id="mxStake" type="number" step="1" value="10"></label></div>
<p id="mxEdge"></p><div class="fbar"><button class="b" id="mxTrack">Track this bet</button>
<button class="b ghost" id="mxSlip">Add to builder</button><span class="sub" id="mxTracked"></span></div></div>
"""
BUILDER_HTML = """
<p class="note">Add selections with <b>+</b> on any match. Legs from the <b>same match</b> are priced together
from the scoreline probabilities (so Over 2.5 + BTTS isn't simply multiplied); corners and half markets are treated
as independent. Legs from different matches are multiplied.</p>
<div id="blOut"></div>
"""
MYBETS_HTML = """
<p class="note">Bets you track settle automatically from results. <b>CLV</b> (closing-line value) compares your
odds with the margin-free closing price: consistently beating the close is the best early sign of a real edge,
long before profit tells you anything. Stored on this device - export to move phones.</p>
<div class="kpi" id="btSum"></div><div id="btList"></div>
<div class="fbar"><button class="b ghost" id="btExport">Export</button><button class="b ghost" id="btImport">Import</button>
<button class="b ghost" id="btClear">Clear settled</button></div>
<textarea id="btIO" class="hide" rows="4" style="width:100%;font:13px monospace"></textarea>
"""


JS = r"""
const D=JSON.parse(document.getElementById('footy-data').textContent);
const $=(q,el=document)=>el.querySelector(q), $$=(q,el=document)=>[...el.querySelectorAll(q)];
const LS={get(k,d){try{const v=localStorage.getItem(k);return v==null?d:JSON.parse(v)}catch(e){return d}},
 set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
function nm(k){return D.names[k]||k}
const pc=x=>x==null||isNaN(x)?'-':(100*x).toFixed(x<0.1&&x>0?1:0)+'%', fo=x=>x>0?(1/x).toFixed(2):'-';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ---------- navigation */
const TAB_OF={};$$('section').forEach(s=>TAB_OF[s.id]=s.dataset.group);
const lastIn=LS.get('lastIn',{});
function show(id,push=true){const s=document.getElementById(id);if(!s)return;
 $$('section').forEach(e=>e.classList.toggle('on',e===s));const g=TAB_OF[id];
 $$('.tabbar button').forEach(b=>b.classList.toggle('on',b.dataset.tab===g));
 $$('[data-go]',s).forEach(b=>b.classList&&b.closest('.seg')&&b.classList.toggle('on',b.dataset.go===id));
 lastIn[g]=id;LS.set('lastIn',lastIn);LS.set('tab',id);if(push)window.scrollTo(0,0);
 if(id==='mybets')drawBets();if(id==='builder')drawSlip();if(id==='today')drawToday()}
const ROOT={today:'today',matches:'matches',bets:'value',fpl:'fpl',more:'more'};
$$('.tabbar button').forEach(b=>b.onclick=()=>{const t=b.dataset.tab,cur=TAB_OF[LS.get('tab','today')];
 show(cur===t||t==='more'?ROOT[t]:(lastIn[t]&&document.getElementById(lastIn[t])?lastIn[t]:ROOT[t]))});
document.addEventListener('click',e=>{const g=e.target.closest('[data-go]');if(g){e.preventDefault();show(g.dataset.go)}
 const th=e.target.closest('th.s');if(th)sortBy(th);
 const fx=e.target.closest('[data-fx]');if(fx){e.preventDefault();openFixture(fx.dataset.fx,fx.dataset.mk,fx.dataset.pr)}
 const ad=e.target.closest('[data-add]');if(ad){e.preventDefault();e.stopPropagation();toggleSlip(ad.dataset.fid,ad.dataset.add);ad.classList.toggle('in')}
 const del=e.target.closest('[data-del]');if(del){e.preventDefault();const b=loadBets();b.splice(parseInt(del.dataset.del),1);saveBets(b);drawBets()}
 const sd=e.target.closest('[data-sdel]');if(sd){e.preventDefault();const s=LS.get('slip',[]);s.splice(parseInt(sd.dataset.sdel),1);LS.set('slip',s);drawSlip()}});

/* ---------- generic table filters + sorting (data sections) */
function applyFilters(sec){const f={};$$('[data-f]',sec).forEach(c=>{f[c.dataset.f]=(c.value||'').toLowerCase()});const q=f.q||'';
 $$('details[data-lg]',sec).forEach(d=>d.classList.toggle('hide',!!f.lg&&d.dataset.lg.toLowerCase()!==f.lg));
 $$('tbody tr',sec).forEach(tr=>{let ok=true;if(q&&!tr.textContent.toLowerCase().includes(q))ok=false;
  if(f.lg&&tr.dataset.lg&&tr.dataset.lg.toLowerCase()!==f.lg)ok=false;if(f.pos&&tr.dataset.pos&&tr.dataset.pos.toLowerCase()!==f.pos)ok=false;
  if(f.maxp&&tr.dataset.price&&parseFloat(tr.dataset.price)>parseFloat(f.maxp))ok=false;tr.classList.toggle('hide',!ok)});
 if(q)$$('details',sec).forEach(d=>{if(!d.classList.contains('hide')&&$$('tbody tr:not(.hide)',d).length)d.open=true})}
$$('section').forEach(sec=>$$('[data-f]',sec).forEach(c=>c.addEventListener('input',()=>applyFilters(sec))));
function sortBy(th){const tb=th.closest('table').tBodies[0],i=[...th.parentNode.children].indexOf(th);const dir=th.dataset.dir==='d'?'a':'d';
 $$('th',th.parentNode).forEach(x=>delete x.dataset.dir);th.dataset.dir=dir;
 const v=tr=>{const t=(tr.children[i]||{}).textContent||'';const n=parseFloat(t.replace(/[%£+,]/g,''));return isNaN(n)?t.toLowerCase():n};
 [...tb.rows].sort((a,b)=>{const x=v(a),y=v(b);const c=(typeof x==='number'&&typeof y==='number')?x-y:String(x).localeCompare(String(y));return dir==='a'?c:-c}).forEach(r=>tb.appendChild(r))}

/* ---------- badges */
const PAL=['#0b7a55','#3563c9','#c2341f','#7b3fc2','#c27b1f','#1f8fa8','#a8326e','#4a5a1f'];
function badge(t,cls='badge'){const id=(D.badges||{})[t],n=nm(t),ini=n.split(/\s+/).filter(w=>w.length>2||n.split(' ').length==1).map(w=>w[0]).join('').slice(0,3).toUpperCase()||n.slice(0,2).toUpperCase();
 let h=0;for(const c of t)h=(h*31+c.charCodeAt(0))>>>0;const bg=PAL[h%PAL.length];
 const fb=`<span class="${cls}" style="background:${bg}">${esc(ini)}</span>`;
 return id?`<img class="${cls}" loading="lazy" alt="" src="https://images.fotmob.com/image_resources/logo/teamlogo/${id}_small.png" onerror="this.outerHTML=this.dataset.fb" data-fb='${fb.replace(/'/g,"&#39;")}'>`:fb}

/* ---------- market engine */
function pmf(l,n){const p=[Math.exp(-l)];for(let k=1;k<=n;k++)p.push(p[k-1]*l/k);return p}
function nbPmf(m,n=30,vr=1.3){const r=m/(vr-1),q=r/(r+m),out=[Math.pow(q,r)];for(let k=1;k<=n;k++)out.push(out[k-1]*(k-1+r)/k*(1-q));return out}
function matrix(lh,la,rho){const n=10,ph=pmf(lh,n),pa=pmf(la,n);const M=[];let s=0;
 for(let i=0;i<=n;i++){M.push([]);for(let j=0;j<=n;j++){let v=ph[i]*pa[j];
  if(i==0&&j==0)v*=1-lh*la*rho;else if(i==0&&j==1)v*=1+lh*rho;else if(i==1&&j==0)v*=1+la*rho;else if(i==1&&j==1)v*=1-rho;
  v=Math.max(v,0);M[i].push(v);s+=v}}return M.map(r=>r.map(v=>v/s))}
function sumM(M,f){let s=0;for(let i=0;i<M.length;i++)for(let j=0;j<M.length;j++)if(f(i,j))s+=M[i][j];return s}
/* each market: k key, g group, l label, p prob, f(i,j) scoreline predicate (if goal-based), s(r) settle from [hg,ag,hht,aht,hc,ac] */
function ahRes(d,l){/* d = margin from the backed side's view, l = its handicap -> W/HW/P/HL/L */
 const one=x=>x>0?1:x<0?-1:0;const q=Math.round(l*4);
 if(Math.abs(q)%2===1){const a=one(d+l-0.25),b=one(d+l+0.25),t=a+b;return t===2?'W':t===1?'HW':t===0?'P':t===-1?'HL':'L'}
 const r=one(d+l);return r>0?'W':r<0?'L':'P'}
function marketList(c){const M=matrix(c.lh,c.la,c.rho),hn=c.hn,an=c.an,out=[];
 const S=(k,g,l,f)=>out.push({k,g,l,f,p:sumM(M,f),s:r=>f(r[0],r[1])});
 S('H','Result',hn+' win',(i,j)=>i>j);S('D','Result','Draw',(i,j)=>i==j);S('A','Result',an+' win',(i,j)=>i<j);
 S('1X','Result',hn+' or draw',(i,j)=>i>=j);S('X2','Result',an+' or draw',(i,j)=>i<=j);S('12','Result','Either team wins',(i,j)=>i!=j);
 for(const t of ['H','A']){const nmx=t=='H'?hn:an,p=sumM(M,(i,j)=>t=='H'?i>j:j>i),d=sumM(M,(i,j)=>i==j);
  out.push({k:'DNB_'+t,g:'Result',l:nmx+' draw no bet',p:p/(1-d),s:r=>r[0]==r[1]?null:(t=='H'?r[0]>r[1]:r[1]>r[0])})}
 for(const l of [0.5,1.5,2.5,3.5,4.5]){S('O'+l,'Goals','Over '+l+' goals',(i,j)=>i+j>l);S('U'+l,'Goals','Under '+l+' goals',(i,j)=>i+j<l)}
 S('BTTS','Goals','Both teams to score',(i,j)=>i>0&&j>0);S('BTTS_N','Goals','Both teams to score - No',(i,j)=>!(i>0&&j>0));
 S('O2.5_BTTS','Goals','Over 2.5 & BTTS',(i,j)=>i+j>2&&i>0&&j>0);
 for(const l of [0.5,1.5,2.5]){S('H_O'+l,'Team goals',hn+' over '+l,(i,j)=>i>l);S('A_O'+l,'Team goals',an+' over '+l,(i,j)=>j>l)}
 S('H_CS','Team goals',hn+' clean sheet',(i,j)=>j==0);S('A_CS','Team goals',an+' clean sheet',(i,j)=>i==0);
 S('H_WTN','Team goals',hn+' win to nil',(i,j)=>i>j&&j==0);S('A_WTN','Team goals',an+' win to nil',(i,j)=>j>i&&i==0);
 /* Asian handicaps -2.5..+2.5 in quarter steps. Half lines can't push; whole lines push on the exact margin;
    quarter lines split the stake across the two neighbouring lines (half win / half loss / half refund).
    p is the win chance with refunds taken out, so fair odds = 1/p and edge = p x odds - 1 stay correct. */
 const diff={};for(let i=0;i<M.length;i++)for(let j=0;j<M.length;j++)diff[i-j]=(diff[i-j]||0)+M[i][j];
 const ahLabel=l=>l===0?'0':(l>0?'+':'')+l;
 for(let q=-10;q<=10;q++){const l=q/4;
  for(const side of ['H','A']){const sign=side==='H'?1:-1,name=side==='H'?hn:an;
   const o={W:0,HW:0,P:0,HL:0,L:0};for(const d in diff)o[ahRes(sign*Number(d),l)]+=diff[d];
   const a=o.W+o.HW/2,b=o.L+o.HL/2;
   out.push({k:(side==='H'?'AH':'AHA')+ahLabel(l),g:'Handicap',l:name+' '+ahLabel(l)+(Math.abs(q)%2===1?'':Number.isInteger(l)?' (refund if level)':''),
    p:a/(a+b),refund:o.P+o.HW/2+o.HL/2,
    /* only half lines (x.5) can be priced inside a same-game builder - the others can refund */
    f:Math.abs(q)%4===2?((i,j)=>sign*(i-j)+l>0):undefined,
    s:r=>({W:true,L:false,P:null,HW:'hw',HL:'hl'}[ahRes(sign*(r[0]-r[1]),l)])})}}
 const h1=c.h1||0.44,l1=(c.lh+c.la)*h1,l2=(c.lh+c.la)*(1-h1),P1=matrix(c.lh*h1,c.la*h1,0);
 const sumP=f=>sumM(P1,f);
 out.push({k:'1H_H',g:'Halves',l:'Half-time: '+hn,p:sumP((i,j)=>i>j),s:r=>r[2]==null?null:r[2]>r[3]});
 out.push({k:'1H_D',g:'Halves',l:'Half-time: draw',p:sumP((i,j)=>i==j),s:r=>r[2]==null?null:r[2]==r[3]});
 out.push({k:'1H_A',g:'Halves',l:'Half-time: '+an,p:sumP((i,j)=>i<j),s:r=>r[2]==null?null:r[2]<r[3]});
 for(const l of [0.5,1.5]){out.push({k:'1H_O'+l,g:'Halves',l:'1st half over '+l,p:1-pmf(l1,2).slice(0,l+0.5).reduce((a,b)=>a+b,0),s:r=>r[2]==null?null:r[2]+r[3]>l});
  out.push({k:'2H_O'+l,g:'Halves',l:'2nd half over '+l,p:1-pmf(l2,2).slice(0,l+0.5).reduce((a,b)=>a+b,0),s:r=>r[2]==null?null:(r[0]-r[2])+(r[1]-r[3])>l})}
 out.push({k:'BOTH_H',g:'Halves',l:'Goal in both halves',p:(1-Math.exp(-l1))*(1-Math.exp(-l2)),s:r=>r[2]==null?null:(r[2]+r[3]>0&&(r[0]-r[2])+(r[1]-r[3])>0)});
 out.push({k:'2H_MORE',g:'Halves',l:'More goals in 2nd half',p:(()=>{const a=pmf(l1,12),b=pmf(l2,12);let s=0;for(let x=0;x<=12;x++)for(let y=x+1;y<=12;y++)s+=a[x]*b[y];return s})(),
  s:r=>r[2]==null?null:(r[0]-r[2])+(r[1]-r[3])>r[2]+r[3]});
 if(c.ch&&c.ca){const ph=nbPmf(c.ch),pa=nbPmf(c.ca),pt=nbPmf(c.ch+c.ca,40),cum=(p,l)=>1-p.slice(0,Math.floor(l)+1).reduce((a,b)=>a+b,0);
  for(const l of [7.5,8.5,9.5,10.5,11.5,12.5]){out.push({k:'C_O'+l,g:'Corners',l:'Corners over '+l,p:cum(pt,l),s:r=>r[4]==null?null:r[4]+r[5]>l});
   out.push({k:'C_U'+l,g:'Corners',l:'Corners under '+l,p:1-cum(pt,l),s:r=>r[4]==null?null:r[4]+r[5]<l})}
  for(const l of [3.5,4.5,5.5,6.5]){out.push({k:'HC_O'+l,g:'Corners',l:hn+' corners over '+l,p:cum(ph,l),s:r=>r[4]==null?null:r[4]>l});
   out.push({k:'AC_O'+l,g:'Corners',l:an+' corners over '+l,p:cum(pa,l),s:r=>r[4]==null?null:r[5]>l})}
  let hm=0,eq=0;for(let x=0;x<ph.length;x++)for(let y=0;y<pa.length;y++){if(x>y)hm+=ph[x]*pa[y];else if(x==y)eq+=ph[x]*pa[y]}
  out.push({k:'C_H',g:'Corners',l:'Most corners: '+hn,p:hm,s:r=>r[4]==null?null:r[4]>r[5]});
  out.push({k:'C_A',g:'Corners',l:'Most corners: '+an,p:1-hm-eq,s:r=>r[4]==null?null:r[5]>r[4]})}
 const sc=[];for(let i=0;i<=5;i++)for(let j=0;j<=5;j++)sc.push([i,j,M[i][j]]);sc.sort((a,b)=>b[2]-a[2]);
 for(const [i,j] of sc.slice(0,12))S('CS_'+i+'-'+j,'Scores','Correct score '+i+'-'+j,(x,y)=>x==i&&y==j);
 out.M=M;return out}
function ctxFor(f,lh,la){return{lh:lh??f.xh,la:la??f.xa,rho:(D.ratings[f.lg]||{}).rho||-0.05,h1:(D.h1||{})[f.lg],ch:f.ch,ca:f.ca,hn:nm(f.h),an:nm(f.a)}}
function fxById(id){return D.fixtures.find(x=>x.id===id)}
function pHDA(f){const m=marketList(ctxFor(f));const g=k=>m.find(x=>x.k===k).p;return[g('H'),g('D'),g('A'),g('O2.5'),g('BTTS')]}
function probBar(h,d,a){return`<div class="bar"><i class="h" style="width:${100*h}%"></i><i class="d" style="width:${100*d}%"></i><i class="a" style="width:${100*a}%"></i></div>`}
function formPills(t,n=5){const d=(D.teams||{})[t];if(!d)return'';return d.m.slice(-n).map(x=>{const r=x[4]>x[5]?'W':x[4]==x[5]?'D':'L';return`<span class="pill ${r}">${r}</span>`}).join('')}

/* ---------- Matches (cards) */
function fxCard(f){const [h,d,a,o,b]=pHDA(f);const news=(f.nh||f.na)?' · <span title="team news">🩹</span>':'';
 return`<div class="fx" data-fx="${f.id}"><div class="meta"><span>${esc(D.lgnames[f.lg]||f.lg)}</span><span>${esc(f.day)}${f.ko?' · '+esc(f.ko):''}</span></div>
 <div class="tm">${badge(f.h)}<span class="nm">${esc(nm(f.h))}</span><span class="p">${pc(h)}</span></div>
 <div class="tm">${badge(f.a)}<span class="nm">${esc(nm(f.a))}</span><span class="p">${pc(a)}</span></div>
 ${probBar(h,d,a)}<div class="foot"><span>Draw <b>${pc(d)}</b></span><span>xG <b>${f.xh.toFixed(1)}–${f.xa.toFixed(1)}</b></span><span>O2.5 <b>${pc(o)}</b></span><span>BTTS <b>${pc(b)}</b></span>${news}</div></div>`}
(()=>{const box=$('#mlOut');if(!box)return;const lgs=[...new Set(D.fixtures.map(f=>f.lg))];let cur=LS.get('mlLg','');
 const ch=$('#mlChips');function chips(){ch.innerHTML=['',...lgs].map(k=>`<button class="chip ${k===cur?'on':''}" data-lg="${k}">${k?esc(D.lgnames[k]||k):'All'}</button>`).join('')}
 function draw(){const q=($('#mlQ').value||'').toLowerCase();const fx=D.fixtures.filter(f=>(!cur||f.lg===cur)&&(!q||(nm(f.h)+' '+nm(f.a)).toLowerCase().includes(q)));
  let h='',day='';for(const f of fx){if(f.day!==day){day=f.day;h+=`<div class="dayh">${esc(day)}</div>`}h+=fxCard(f)}
  box.innerHTML=h||'<p class="note">No fixtures in the next week for this filter.</p>'}
 ch.addEventListener('click',e=>{const b=e.target.closest('[data-lg]');if(!b)return;cur=b.dataset.lg;LS.set('mlLg',cur);chips();draw()});
 $('#mlQ').addEventListener('input',draw);chips();draw()})();

/* ---------- Match detail / calculator */
const mx={L:$('#mxL'),F:$('#mxF'),H:$('#mxH'),A:$('#mxA'),XH:$('#mxXH'),XA:$('#mxXA'),M:$('#mxMk'),P:$('#mxP')};
let MK=[],CUR=null,GRP=LS.get('mdGrp','Result');
function lamb(L,h,a){const r=D.ratings[L];if(!r)return[1.3,1.1];const th=r.teams[h]||[-0.15,0.15],ta=r.teams[a]||[-0.15,0.15];
 return[Math.exp(r.mu+r.ha+th[0]+ta[1]),Math.exp(r.mu+ta[0]+th[1])]}
function fillTeams(L,h,a){const ts=Object.keys((D.ratings[L]||{teams:{}}).teams).sort((x,y)=>nm(x).localeCompare(nm(y)));
 const o=ts.map(t=>`<option value="${t}">${esc(nm(t))}</option>`).join('');mx.H.innerHTML=o;mx.A.innerHTML=o;mx.H.value=h||ts[0];mx.A.value=a||ts[1]}
function fillFixtures(){const L=mx.L.value;mx.F.innerHTML='<option value="">- any two teams -</option>'+D.fixtures.filter(f=>!L||f.lg===L).map(f=>`<option value="${f.id}">${esc(f.day)} · ${esc(nm(f.h))} v ${esc(nm(f.a))}</option>`).join('')}
function setXG(){const[lh,la]=lamb(mx.L.value,mx.H.value,mx.A.value);mx.XH.value=lh.toFixed(2);mx.XA.value=la.toFixed(2);mx.F.value='';render()}
function openFixture(id,mk,pr){const f=fxById(id);if(!f)return;mx.L.value=f.lg;fillFixtures();fillTeams(f.lg,f.h,f.a);mx.F.value=id;
 mx.XH.value=f.xh.toFixed(2);mx.XA.value=f.xa.toFixed(2);render();if(mk){mx.M.value=mk;const b=pr?parseFloat(pr):((f.b365||{})[mk]||((f.px||{})[mk]||[])[0]);mx.P.value=b?b.toFixed(2):'';
  const m=MK.find(x=>x.k===mk);if(m&&m.g!==GRP){GRP=m.g;drawList()}}price();show('mxsec')}
function curCtx(){const f=fxById(mx.F.value);const lh=parseFloat(mx.XH.value)||0.05,la=parseFloat(mx.XA.value)||0.05;
 if(f)return[f,ctxFor(f,lh,la)];const L=mx.L.value;return[null,{lh,la,rho:(D.ratings[L]||{}).rho||-0.05,h1:(D.h1||{})[L],hn:nm(mx.H.value),an:nm(mx.A.value)}]}
function render(){const[f,c]=curCtx();CUR=f;MK=marketList(c);const g=k=>MK.find(x=>x.k===k).p;
 const h=f?f.h:mx.H.value,a=f?f.a:mx.A.value;
 $('#mdHero').innerHTML=`<div class="sub" style="text-align:center;margin-bottom:8px">${esc(D.lgnames[mx.L.value]||'')}${f?' · '+esc(f.day)+(f.ko?' '+esc(f.ko):''):''}</div>
  <div class="hero"><div class="t">${badge(h,'badge')}${esc(nm(h))}<div>${formPills(h)}</div></div><div class="vs">xG<br><b class="big">${c.lh.toFixed(1)}–${c.la.toFixed(1)}</b></div>
  <div class="t">${badge(a,'badge')}${esc(nm(a))}<div>${formPills(a)}</div></div></div>${probBar(g('H'),g('D'),g('A'))}
  <div class="hero sub"><span>${pc(g('H'))}</span><span>Draw ${pc(g('D'))}</span><span>${pc(g('A'))}</span></div>`;
 $('#mxNews').innerHTML=f&&(f.nh||f.na)?'Team news already in these xG: '+esc([f.nh,f.na].filter(Boolean).join(' · ')):(f&&f.ref?'':'');
 const groups=[...new Set(MK.map(m=>m.g))];if(!groups.includes(GRP))GRP='Result';
 $('#mdGroups').innerHTML=groups.map(x=>`<button class="chip ${x===GRP?'on':''}" data-grp="${x}">${x}</button>`).join('');drawList();
 const cur=mx.M.value;mx.M.innerHTML=MK.map(m=>`<option value="${m.k}">${esc(m.l)}</option>`).join('');if(cur&&MK.find(m=>m.k===cur))mx.M.value=cur;price()}
function canBuild(m){return !(m.g==='Handicap'&&!m.f)}
function drawList(){const slip=LS.get('slip',[]),fid=CUR?CUR.id:'';$$('#mdGroups .chip').forEach(b=>b.classList.toggle('on',b.dataset.grp===GRP));
 const PX=(CUR&&CUR.px)||{};
 let h=MK.filter(m=>m.g===GRP).map(m=>{const x=PX[m.k],e=x?m.p*x[0]-1:null;return`<div class="mrow"><span class="l">${esc(m.l)}${x?`<br><span class="sub">best ${x[0].toFixed(2)} ${esc(x[1]||'')} · <span class="${e>0?'pos':'neg'}">${(e>0?'+':'')+(100*e).toFixed(0)}%</span></span>`:''}</span><span class="p">${pc(m.p)}</span><span class="o">${fo(m.p)}</span>
  ${fid?(canBuild(m)?`<button class="add ${slip.some(s=>s.id===fid&&s.mk===m.k)?'in':''}" data-add="${m.k}" data-fid="${fid}" aria-label="add to builder">+</button>`:'<span style="width:34px"></span>'):''}</div>`}).join('');
 if(GRP==='Scores'){const M=MK.M;let g='<div class="tw"><table class="sg"><thead><tr><th></th>'+[0,1,2,3,4,5].map(j=>`<th>${j}</th>`).join('')+'</tr></thead><tbody>';
  for(let i=0;i<=5;i++){g+=`<tr><th>${i}</th>`;for(let j=0;j<=5;j++)g+=`<td>${(100*M[i][j]).toFixed(1)}</td>`;g+='</tr>'}h=g+'</tbody></table></div><p class="note">Rows = home goals, columns = away goals (%).</p>'+h}
 $('#mdList').innerHTML='<div class="mrow sub"><span class="l">Market</span><span class="p">Prob.</span><span class="o">Fair</span>'+(fid?'<span style="width:34px"></span>':'')+'</div>'+h+
  (GRP==='Corners'?'<p class="note">Corners: team corner rates vs league average, negative binomial (checked out-of-sample this season).</p>':'')+
  (GRP==='Handicap'?'<p class="note">Whole lines (0, ±1, ±2) refund your stake on that exact margin; quarter lines (±0.25, ±0.75...) split it across the two neighbouring lines, so you can half-win or half-lose. Prob. is the win chance with refunds taken out, so fair odds = 1 ÷ Prob. Only ±0.5 / ±1.5 / ±2.5 lines can go in the builder.</p>':'')+
  (GRP==='Halves'?`<p class="note">Halves: ${pc((D.h1||{})[mx.L.value]||0.44)} of goals in this league come before half-time.</p>`:'')}
$('#mdGroups')&&$('#mdGroups').addEventListener('click',e=>{const b=e.target.closest('[data-grp]');if(!b)return;GRP=b.dataset.grp;LS.set('mdGrp',GRP);drawList()});
function price(){const m=MK.find(x=>x.k===mx.M.value);const o=parseFloat(mx.P.value);const out=$('#mxEdge');
 if(!m){out.textContent='';return}if(!(o>1)){out.innerHTML=`Model ${pc(m.p)} · fair odds <b>${fo(m.p)}</b> - type the bookmaker's price`;return}
 const e=m.p*o-1,k=Math.min(Math.max((m.p*o-1)/(o-1),0)*0.25,0.05);
 out.innerHTML=`Model ${pc(m.p)} · fair ${fo(m.p)} · edge <span class="${e>0?'pos':'neg'} big">${(e>0?'+':'')+(100*e).toFixed(1)}%</span><br>`+(e>0?`quarter-Kelly stake <b>${(100*k).toFixed(1)}%</b> of bankroll`:'no bet at this price')}
if(mx.L){mx.L.innerHTML=Object.keys(D.ratings).map(k=>`<option value="${k}">${esc(D.lgnames[k]||k)}</option>`).join('');
 mx.L.onchange=()=>{fillFixtures();fillTeams(mx.L.value);setXG()};mx.F.onchange=()=>mx.F.value?openFixture(mx.F.value):setXG();
 mx.H.onchange=setXG;mx.A.onchange=setXG;mx.XH.oninput=render;mx.XA.oninput=render;mx.M.onchange=price;mx.P.oninput=price;
 const first=D.fixtures.find(f=>f.h===D.fav||f.a===D.fav)||D.fixtures[0];
 if(first){mx.L.value=first.lg;fillFixtures();fillTeams(first.lg,first.h,first.a);mx.F.value=first.id;mx.XH.value=first.xh.toFixed(2);mx.XA.value=first.xa.toFixed(2);render()}
 else{fillFixtures();fillTeams(mx.L.value);setXG()}}

/* ---------- Value */
function renderValue(){const box=$('#vbox');if(!box)return;const adj=($('#vMode')||{}).value!=='raw';
 const P=v=>adj&&v.pb!=null?v.pb:v.p, E=v=>P(v)*v.price-1, S=v=>Math.min(Math.max((P(v)*v.price-1)/(v.price-1),0)*0.25,0.05);
 const me=parseFloat($('#vMin').value)/100||0,bk=$('#vBook').value,mk=$('#vMk').value,lg=($('#vLg').value||''),q=($('#vQ').value||'').toLowerCase(),mx_=parseFloat($('#vMax').value)||99;
 const rows=D.value.filter(v=>v.book===bk&&E(v)>=me&&v.price<=mx_&&(!mk||v.market===mk)&&(!lg||v.lg===lg)&&(!q||(v.match+' '+v.sel).toLowerCase().includes(q))).sort((a,b)=>E(b)-E(a));
 $('#vCount').textContent=rows.length+' selection'+(rows.length==1?'':'s');
 box.innerHTML=rows.length?rows.map(v=>`<div class="fx" data-fx="${v.id}" data-mk="${v.mkkey}" data-pr="${v.price}"><div class="meta"><span>${esc(D.lgnames[v.lg]||v.lg)} · ${esc(v.day)}</span><span class="${E(v)>0?'pos':'neg'}">${(E(v)>0?'+':'')+(100*E(v)).toFixed(1)}% edge</span></div>
  <div class="tm"><span class="nm">${esc(v.sel)} <span class="sub">· ${esc(v.match)}</span></span><span class="p">${v.price.toFixed(2)}<br><span class="sub">${esc(v.bk||'')}</span></span></div>
  <div class="foot"><span>Model <b>${pc(P(v))}</b></span><span>Market <b>${v.mp!=null?pc(v.mp):'-'}</b></span><span>Fair <b>${fo(P(v))}</b></span><span>Stake <b>${(100*S(v)).toFixed(1)}%</b></span>${v.news?`<span>🩹 ${esc(v.news)}</span>`:''}</div></div>`).join('')
  :`<p class="note">${D.value.length?'No selections match these filters - lower the minimum edge.':'No bookmaker prices for the coming fixtures yet: football-data.co.uk publishes them a day or two before each round. Until then use the fair odds below and type the Bet365 price into a match.'}</p>`}
$$('#vMin,#vBook,#vMode,#vMk,#vLg,#vQ,#vMax').forEach(c=>c&&c.addEventListener('input',renderValue));renderValue();

/* ---------- Bet builder (slip) */
function toggleSlip(fid,mk){const s=LS.get('slip',[]);const i=s.findIndex(x=>x.id===fid&&x.mk===mk);if(i>=0)s.splice(i,1);else s.push({id:fid,mk});LS.set('slip',s);slipBadge()}
function slipBadge(){let b=$('#slipbar');const n=LS.get('slip',[]).length;if(!b){b=document.createElement('div');b.id='slipbar';b.className='slipbar';b.dataset.go='builder';document.body.appendChild(b)}
 b.textContent=`Builder · ${n} leg${n==1?'':'s'}`;b.classList.toggle('hide',!n)}
function slipProb(legs){/* legs: [{id,mk}] -> {p, parts} */ const by={};for(const l of legs)(by[l.id]=by[l.id]||[]).push(l.mk);let p=1;const parts=[];
 for(const id in by){const f=fxById(id);if(!f){parts.push({id,miss:true});continue}const m=marketList(ctxFor(f));const sel=by[id].map(k=>m.find(x=>x.k===k)).filter(Boolean);
  const goal=sel.filter(x=>x.f),other=sel.filter(x=>!x.f);let pg=goal.length?sumM(m.M,(i,j)=>goal.every(x=>x.f(i,j))):1;
  const pind=goal.reduce((a,x)=>a*x.p,1);let po=other.reduce((a,x)=>a*x.p,1);p*=pg*po;parts.push({f,sel,p:pg*po,naive:pind*po})}
 return{p,parts}}
function drawSlip(){const box=$('#blOut');if(!box)return;const legs=LS.get('slip',[]);slipBadge();
 if(!legs.length){box.innerHTML='<div class="card"><p class="note">Your builder is empty. Open a match and tap <b>+</b> next to any market.</p></div>';return}
 const r=slipProb(legs);let h='';let i=0;
 for(const pt of r.parts){if(pt.miss){h+=`<div class="card"><p class="note">A fixture in your builder has started or dropped out of the next 7 days.</p></div>`;continue}
  h+=`<div class="card"><div class="meta sub">${esc(D.lgnames[pt.f.lg]||pt.f.lg)} · ${esc(pt.f.day)}</div><h3>${esc(nm(pt.f.h))} v ${esc(nm(pt.f.a))}</h3>`+
  pt.sel.map(m=>{const idx=legs.findIndex(l=>l.id===pt.f.id&&l.mk===m.k);return`<div class="mrow"><span class="l">${esc(m.l)}</span><span class="p">${pc(m.p)}</span><button class="add" data-sdel="${idx}">✕</button></div>`}).join('')+
  (pt.sel.length>1?`<p class="note">Together: <b>${pc(pt.p)}</b> (fair ${fo(pt.p)})${Math.abs(pt.p-pt.naive)>0.005?` - simply multiplying would say ${pc(pt.naive)}`:''}</p>`:'')+'</div>'}
 h+=`<div class="card"><h2>Combined</h2><div class="kpi"><div class="mk"><span>Probability</span><b>${pc(r.p)}</b></div><div class="mk"><span>Fair odds</span><b>${fo(r.p)}</b></div></div>
  <div class="form"><label>Bookmaker odds<input id="blOdds" type="number" step="0.01" placeholder="e.g. 6.50"></label><label>Stake<input id="blStake" type="number" value="5"></label></div>
  <p id="blEdge"></p><div class="fbar"><button class="b" id="blTrack">Track this bet</button><button class="b ghost" id="blClear">Clear</button></div></div>`;
 box.innerHTML=h;const upd=()=>{const o=parseFloat($('#blOdds').value);const e=r.p*o-1;$('#blEdge').innerHTML=o>1?`Edge <span class="${e>0?'pos':'neg'} big">${(e>0?'+':'')+(100*e).toFixed(1)}%</span>`:''};
 $('#blOdds').oninput=upd;$('#blClear').onclick=()=>{LS.set('slip',[]);drawSlip()};
 $('#blTrack').onclick=()=>{const o=parseFloat($('#blOdds').value);if(!(o>1)){$('#blEdge').textContent='Type the bookmaker odds first';return}
  const legsFull=[];for(const pt of r.parts)if(!pt.miss)for(const m of pt.sel)legsFull.push({id:pt.f.id,mk:m.k,label:m.l,match:nm(pt.f.h)+' v '+nm(pt.f.a),day:pt.f.day});
  const b=loadBets();b.push({legs:legsFull,label:legsFull.length+'-leg builder',day:legsFull[0].day,match:[...new Set(legsFull.map(l=>l.match))].join(', '),p:r.p,odds:o,stake:parseFloat($('#blStake').value)||1,at:new Date().toISOString()});
  saveBets(b);LS.set('slip',[]);show('mybets')}}

/* ---------- My bets + closing-line value */
function loadBets(){return LS.get('footy-bets',[])}function saveBets(b){LS.set('footy-bets',b)}
let _DUMMY=null;
function settleLeg(id,mk){const r=(D.results||{})[id];if(!r)return undefined;
 if(mk.startsWith('CS_')){const [i,j]=mk.slice(3).split('-').map(Number);return r[0]==i&&r[1]==j}
 _DUMMY=_DUMMY||marketList({lh:1.4,la:1.2,rho:0,hn:'',an:'',ch:5,ca:4});const m=_DUMMY.find(x=>x.k===mk);if(!m)return undefined;
 const v=m.s(r);return v==null?null:v}
function settle(b){if(b.res)return b;const legs=b.legs||[{id:b.id,mk:b.mk}];let all=true,anyVoid=false;
 if(!b.legs){const v=settleLeg(b.id,b.mk);if(v===undefined)return b;const r=(D.results||{})[b.id];const sc=r?r[0]+'-'+r[1]:'';
  return{...b,res:v===null?'void':v==='hw'?'half-won':v==='hl'?'half-lost':v?'won':'lost',score:sc}}
 for(const l of legs){const v=settleLeg(l.id,l.mk);if(v===undefined)return b;if(v===null){anyVoid=true;continue}if(!v){const r=(D.results||{})[l.id];return{...b,res:'lost',score:r?r[0]+'-'+r[1]:''}}}
 const r=(D.results||{})[legs[0].id];return{...b,res:anyVoid&&legs.length==1?'void':'won',score:legs.length==1&&r?r[0]+'-'+r[1]:''}}
function clvOf(b){if(b.legs)return null;const c=((D.closing||{})[b.id]||{})[b.mk];return c?b.odds/c-1:null}
function drawBets(){const list=$('#btList');if(!list)return;let bets=loadBets().map(settle);saveBets(bets);
 const pl=b=>b.res==='won'?b.stake*(b.odds-1):b.res==='half-won'?b.stake*(b.odds-1)/2:b.res==='lost'?-b.stake:b.res==='half-lost'?-b.stake/2:0;
 const st=bets.filter(b=>b.res&&b.res!=='void');
 const staked=st.reduce((a,b)=>a+b.stake,0),profit=st.reduce((a,b)=>a+pl(b),0),exp=bets.reduce((a,b)=>a+b.stake*(b.p*b.odds-1),0);
 const cl=bets.map(clvOf).filter(x=>x!=null),avgC=cl.length?cl.reduce((a,b)=>a+b,0)/cl.length:null;
 const card=(l,v,s)=>`<div class="mk"><span>${l}</span><b>${v}</b><span>${s||''}</span></div>`;
 $('#btSum').innerHTML=card('Profit',(profit>=0?'+':'')+profit.toFixed(2),`${st.length} settled · ${bets.filter(b=>!b.res).length} open`)+
  card('ROI',staked?((profit>=0?'+':'')+(100*profit/staked).toFixed(1)+'%'):'-',`${st.filter(b=>b.res==='won'||b.res==='half-won').length}/${st.length} won`)+
  card('Avg CLV',avgC==null?'-':((avgC>=0?'+':'')+(100*avgC).toFixed(1)+'%'),cl.length?`${cl.filter(x=>x>0).length}/${cl.length} beat the close`:'after kick-off')+
  card('Expected',(exp>=0?'+':'')+exp.toFixed(2),'from model edges');
 list.innerHTML=bets.length?bets.map((b,i)=>({b,i})).reverse().map(({b,i})=>{const c=clvOf(b);return`<div class="fx"><div class="meta"><span>${esc(b.day||'')} · ${esc(b.match||'')}</span>
  <span class="${/won/.test(b.res||'')?'pos':/lost/.test(b.res||'')?'neg':''}">${b.res?(b.res+(b.score?' '+b.score:'')):'open'}</span></div>
  <div class="tm"><span class="nm">${esc(b.label)}</span><span class="p">${b.odds.toFixed(2)}</span><a class="go" href="#" data-del="${i}" style="margin-left:8px">✕</a></div>
  ${b.legs?`<div class="sub">${b.legs.map(l=>esc(l.label)+' ('+esc(l.match)+')').join(' · ')}</div>`:''}
  <div class="foot"><span>Stake <b>${b.stake}</b></span><span>Edge <b>${(100*(b.p*b.odds-1)).toFixed(0)}%</b></span>${c!=null?`<span>CLV <b class="${c>0?'pos':'neg'}">${(c>0?'+':'')+(100*c).toFixed(1)}%</b></span>`:''}</div></div>`}).join('')
  :'<div class="card"><p class="note">No bets yet - open a match, type the odds and tap "Track this bet", or build a multi in the Builder.</p></div>'}
(()=>{if(!$('#btExport'))return;const io=$('#btIO');
 $('#btExport').onclick=()=>{io.classList.remove('hide');io.value=JSON.stringify(loadBets());io.select();try{navigator.clipboard.writeText(io.value)}catch(e){}};
 $('#btImport').onclick=()=>{if(io.classList.contains('hide')){io.classList.remove('hide');io.value='';io.placeholder='Paste exported bets here, then tap Import again';return}
  try{const b=JSON.parse(io.value);if(Array.isArray(b)){saveBets(b);drawBets();io.classList.add('hide')}}catch(e){alert('That doesn\'t look like exported bets')}};
 $('#btClear').onclick=()=>{saveBets(loadBets().filter(b=>!b.res));drawBets()}})();
(()=>{const t=$('#mxTrack');if(!t)return;t.onclick=()=>{const m=MK.find(x=>x.k===mx.M.value),o=parseFloat(mx.P.value),s=parseFloat($('#mxStake').value)||1;
  if(!m||!(o>1)){$('#mxTracked').textContent='Type the odds first';return}if(!CUR){$('#mxTracked').textContent='Pick a fixture to track a bet';return}
  const b=loadBets();b.push({id:CUR.id,day:CUR.day,match:nm(CUR.h)+' v '+nm(CUR.a),mk:m.k,label:m.l,p:m.p,odds:o,stake:s,at:new Date().toISOString()});
  saveBets(b);$('#mxTracked').textContent='✓ saved to My bets'};
 $('#mxSlip').onclick=()=>{if(!CUR){$('#mxTracked').textContent='Pick a fixture first';return}const mm=MK.find(x=>x.k===mx.M.value);if(mm&&!canBuild(mm)){$('#mxTracked').textContent='Lines that can refund (whole/quarter handicaps) can\'t go in the builder';return}
  toggleSlip(CUR.id,mx.M.value);drawList();$('#mxTracked').textContent='✓ builder updated'}})();

/* ---------- Today */
function drawToday(){const box=$('#tdOut');if(!box)return;let h='';const fav=D.fav;
 const nowD=new Date();h+=`<p class="sub" style="margin:4px 2px 0">${nowD.toLocaleDateString('en-GB',{weekday:'long',day:'numeric',month:'long'})}</p>`;
 if(fav&&D.teams&&D.teams[fav]){const up=(D.upcoming||{})[fav]||[];const f=up.length?fxById(up[0][4]):null;
  h+=`<div class="card"><div class="tm" style="display:flex;align-items:center;gap:10px">${badge(fav)}<h2 style="margin:0;flex:1">${esc(nm(fav))}</h2><span>${formPills(fav)}</span></div>`;
  if(f){const [ph,pd,pa,po,pb]=pHDA(f);h+=`<div class="fx" data-fx="${f.id}" style="box-shadow:none"><div class="meta"><span>${esc(D.lgnames[f.lg]||f.lg)}</span><span>${esc(f.day)}${f.ko?' · '+esc(f.ko):''}</span></div>
   <div class="tm">${badge(f.h)}<span class="nm">${esc(nm(f.h))}</span><span class="p">${pc(ph)}</span></div><div class="tm">${badge(f.a)}<span class="nm">${esc(nm(f.a))}</span><span class="p">${pc(pa)}</span></div>
   ${probBar(ph,pd,pa)}<div class="foot"><span>Draw <b>${pc(pd)}</b></span><span>O2.5 <b>${pc(po)}</b></span><span>BTTS <b>${pc(pb)}</b></span></div>
   ${(f.nh||f.na)?`<p class="note" style="margin:6px 0 0">🩹 ${esc([f.nh,f.na].filter(Boolean).join(' · '))}</p>`:''}</div>`}
  else if(up.length)h+=`<p>Next: ${esc(up[0][0])} ${up[0][3]=='H'?'v':'@'} ${esc(nm(up[0][2]))}</p>`;
  if(up.length>1)h+=`<p class="note">Then ${up.slice(1,4).map(u=>`${esc(u[0])} ${u[3]=='H'?'v':'@'} ${esc(nm(u[2]))}`).join(' · ')}</p>`;h+='</div>'}
 const F=D.fpl;if(F){const dl=F.deadline?new Date(F.deadline):null;let left='';if(dl){const ms=dl-nowD;if(ms>0){const d=Math.floor(ms/864e5),hh=Math.floor(ms%864e5/36e5);left=d?`${d}d ${hh}h`:`${hh}h ${Math.floor(ms%36e5/6e4)}m`}}
  h+=`<div class="card" data-go="fpl" style="cursor:pointer"><h2>FPL · GW${F.gw}${left?` <span class="sub">deadline in ${left}</span>`:''}</h2><div class="kpi">
   <div class="mk"><span>Captain pick</span><b style="font-size:17px">${esc(F.captain||'-')}</b><span>vice ${esc(F.vice||'-')}</span></div>
   <div class="mk"><span>Your XI</span><b>${F.now_pts.toFixed(1)}</b><span>best ${F.best_pts.toFixed(1)}</span></div>
   ${F.move?`<div class="mk"><span>Best transfer</span><b style="font-size:15px">${esc(F.move)}</b><span>${esc(F.move_gain)}</span></div>`:''}</div>
   ${F.changes&&F.changes.length?`<p class="note">Line-up: ${esc(F.changes)}</p>`:''}${F.flagged&&F.flagged.length?`<p class="note">⚠ ${esc(F.flagged.join(' · '))}</p>`:''}</div>`}
 const adj=v=>v.pb!=null?v.pb:v.p;const short=(D.value||[]).map(v=>({...v,e:adj(v)*v.price-1})).filter(v=>v.e>=0.03).sort((a,b)=>b.e-a.e)
  .filter((v,i,a)=>a.findIndex(x=>x.id===v.id&&x.mkkey===v.mkkey)===i).slice(0,4);
 h+=`<div class="card"><h2>Shortlist <span class="sub">market-adjusted</span></h2>`+(short.length?short.map(v=>`<div class="mrow" data-fx="${v.id}" data-mk="${v.mkkey}" data-pr="${v.price}" style="cursor:pointer"><span class="l">${esc(v.sel)}<br><span class="sub">${esc(v.match)} · ${esc(v.day)} · ${esc(v.book==='b365'?'Bet365':(v.bk||''))}</span></span><span class="p">${v.price.toFixed(2)}</span><span class="o pos">+${(100*v.e).toFixed(0)}%</span></div>`).join('')
  :`<p class="note">${(D.value||[]).length?'Nothing clears a 3% market-adjusted edge right now.':'No bookmaker prices for the next games yet.'}</p>`)+`<p style="margin:6px 0 0"><a class="go" href="#" data-go="value">All value ›</a></p></div>`;
 const bets=loadBets().map(settle),open=bets.filter(b=>!b.res);const st=bets.filter(b=>b.res&&b.res!=='void');const pr=st.reduce((a,b)=>a+(b.res==='won'?b.stake*(b.odds-1):b.res==='half-won'?b.stake*(b.odds-1)/2:b.res==='half-lost'?-b.stake/2:-b.stake),0);
 if(bets.length)h+=`<div class="card" data-go="mybets" style="cursor:pointer"><h2>My bets</h2><div class="kpi"><div class="mk"><span>Open</span><b>${open.length}</b></div><div class="mk"><span>Profit</span><b class="${pr>=0?'pos':'neg'}">${(pr>=0?'+':'')+pr.toFixed(2)}</b></div></div></div>`;
 const big=D.fixtures.filter(f=>['E0','UCL','UEL','SP1','I1','D1','F1'].includes(f.lg)).slice(0,6);
 if(big.length)h+='<h2>Coming up</h2>'+big.map(fxCard).join('')+'<p><a class="go" href="#" data-go="matches">All matches ›</a></p>';
 h+=`<footer>Updated ${esc(D.stamp||'')} · probabilities, not certainties<br>Bet only what you can afford to lose · BeGambleAware.org · 0808 8020 133</footer>`;
 box.innerHTML=h}
slipBadge();
show(LS.get('tab','today'),false);if(!document.querySelector('section.on'))show('today',false);
"""


def page(title: str, head_extra: str, sections: dict[str, str], data_json: str, extra_js: str, stamp: str,
         blurbs: dict[str, str]) -> str:
    order = list(SECTIONS)
    present = [s for s in order if s in sections]
    body = "".join(section_shell(s, sections[s]) for s in present)
    body += section_shell("more", more_menu(present, blurbs))
    tabs = "".join(f'<button data-tab="{t}"><span>{i}</span>{lab}</button>' for t, lab, i in TABS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{html.escape(title)}</title>
<meta name="apple-mobile-web-app-capable" content="yes">{head_extra}<style>{CSS}</style></head><body>
<div class="top"><div class="row"><h1>{html.escape(title)}</h1><span class="upd">{html.escape(stamp)}</span></div></div>
<main>{body}</main><nav class="tabbar">{tabs}</nav>
<script type="application/json" id="footy-data">{data_json}</script>
<script>{JS}{extra_js}</script></body></html>"""

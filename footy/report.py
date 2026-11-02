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
from .pipeline import coverage

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
"""

JS = """
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
document.querySelectorAll('nav button,section').forEach(e=>e.classList.remove('on'));
b.classList.add('on');document.getElementById(b.dataset.t).classList.add('on');
try{localStorage.setItem('tab',b.dataset.t)}catch(e){}});
try{const t=localStorage.getItem('tab');if(t){const b=document.querySelector(`nav button[data-t="${t}"]`);if(b)b.click()}}catch(e){}
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
    return html.escape(str(v))


def table(df: pd.DataFrame, spec: list[tuple[str, str, str]]) -> str:
    """spec: (column, header, kind)"""
    if df is None or df.empty:
        return '<p class="note">Nothing to show yet.</p>'
    spec = [s for s in spec if s[0] in df.columns]
    head = "".join(f'<th class="{"l" if k == "text" else ""}">{html.escape(h)}</th>' for _, h, k in spec)
    rows = []
    for _, r in df.iterrows():
        cells = "".join(f'<td class="{"l" if k == "text" else ""}">{_fmt(r[c], k)}</td>' for c, _, k in spec)
        rows.append(f"<tr>{cells}</tr>")
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


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
    ratings = fit_all(frame, order) if order else {}
    sections: dict[str, str] = {}

    # --- value + fixtures
    pred = predict.predict_fixtures(con, frame, ratings, days) if ratings else pd.DataFrame()
    if not pred.empty:
        pred["home_name"], pred["away_name"] = pred.home.map(nm), pred.away.map(nm)
        pred["match"] = pred.home_name + " v " + pred.away_name
        pred["day"] = pred.date.dt.strftime("%a %d %b")
    vb = predict.value_bets(pred, min_edge).head(40) if not pred.empty else pd.DataFrame()
    if not vb.empty:
        vb["league"] = vb.league.map(ln)
        vb["day"] = pd.to_datetime(vb.date).dt.strftime("%a %d %b")
    sections["Value"] = (
        f'<p class="note">Selections where model probability × best available price beats 1 + {min_edge:.0%}. '
        "Check team news first, and check the league's backtest record - big edges often mean the model is "
        "missing something.</p>" +
        table(vb, [("day", "Date", "text"), ("league", "League", "text"), ("match", "Match", "text"),
                   ("market", "Market", "text"), ("selection", "Pick", "text"), ("model_p", "Model", "pct"),
                   ("market_p", "Market", "pct"), ("fair_odds", "Fair", "f2"), ("price", "Best price", "f2"),
                   ("edge", "Edge", "signed_pct"), ("stake_pct", "Stake", "pct1")]))

    fx_html = []
    for k in order:
        p = pred[pred.league == k] if not pred.empty else pred
        if p.empty:
            continue
        fx_html.append(f"<details open><summary>{ln(k)} · {len(p)} matches</summary>" + table(
            p, [("day", "Date", "text"), ("match", "Match", "text"), ("xg_home", "xG H", "f2"),
                ("xg_away", "xG A", "f2"), ("H", "Home", "pct"), ("D", "Draw", "pct"), ("A", "Away", "pct"),
                ("mkt_H", "Mkt H", "pct"), ("mkt_D", "Mkt D", "pct"), ("mkt_A", "Mkt A", "pct"),
                ("O2.5", "O2.5", "pct"), ("BTTS", "BTTS", "pct"), ("H_CS", "H CS", "pct"),
                ("A_CS", "A CS", "pct")]) + "</details>")
    sections["Fixtures"] = ('<p class="note">Model probabilities for the next week; "Mkt" = bookmaker odds '
                            "with the margin removed.</p>" + ("".join(fx_html) or '<p class="note">No fixtures '
                                                              "with odds published yet.</p>"))

    # --- tables
    tb_html, reg_rows = [], []
    for k in order:
        f = frame[(frame.league == k) & (frame.season == cur)]
        t = form.league_table(f)
        if t.empty:
            continue
        t["team"] = t.team.map(nm)
        share = t.real_xg_share.mean()
        kind = "real xG" if share > 0.9 else "shots-based proxy xG" if share < 0.1 else f"{share:.0%} real xG"
        tb_html.append(f"<details><summary>{ln(k)} <span class='sub'>({kind})</span></summary>" + table(
            t, [("Pos", "#", "int"), ("team", "Team", "text"), ("P", "P", "int"), ("Pts", "Pts", "int"),
                ("GD", "GD", "int"), ("xG", "xG", "f1"), ("xGA", "xGA", "f1"), ("xGD", "xGD", "signed"),
                ("xPts", "xPts", "f1"), ("xPos", "xPos", "int"), ("Pts-xPts", "Pts−xPts", "signed"),
                ("G-xG", "G−xG", "signed"), ("GA-xGA", "GA−xGA", "signed"), ("BC/90", "BC/g", "f1"),
                ("BCA/90", "BCA/g", "f1"), ("SoT", "SoT/g", "f1"), ("Corners", "Corn/g", "f1")]) + "</details>")
        fl = form.regression_flags(t, 6)
        fl = fl[fl.verdict != "In line with performances"]
        for _, r in fl.iterrows():
            reg_rows.append(dict(league=ln(k), team=r.team, Pts=r.Pts, xPts=r.xPts, luck=r.luck_score,
                                 verdict=r.verdict))
    reg = pd.DataFrame(reg_rows)
    if not reg.empty:
        reg = reg.reindex(reg.luck.abs().sort_values(ascending=False).index)
    sections["Tables"] = ('<p class="note">Actual table vs expected (xPts = points the chances deserved). '
                          "Pts−xPts and G−xG show who's riding luck.</p>" + "".join(tb_html))
    sections["Regression"] = ('<p class="note">Teams whose points per game differ from their xPts by more than '
                              "0.35 - classic candidates to oppose or back.</p>" + table(
        reg, [("league", "League", "text"), ("team", "Team", "text"), ("Pts", "Pts", "int"),
              ("xPts", "xPts", "f1"), ("luck", "±Pts/g", "signed"), ("verdict", "Verdict", "text")]))

    # --- FPL
    if "E0" in ratings:
        xp, tick = fpl.expected_points(con, ratings["E0"], 5)
        if not xp.empty:
            xp["team"] = xp.team.map(nm)
            xp = xp[xp.start_rate >= 0.4]
            gw = [c for c in xp.columns if c.startswith("GW")]
            spec = [("player", "Player", "text"), ("team", "Team", "text"), ("position", "Pos", "text"),
                    ("price", "£m", "f1"), ("selected_pct", "Own%", "f1"), *[(g, g, "f1") for g in gw],
                    ("xPts_5gw", "5GW", "f1"), ("xPts_per_m", "per £m", "f2")]
            parts = ['<p class="note">Expected FPL points over the next 5 gameweeks (regular starters). '
                     "Built from FPL xG/xA, fixture difficulty from the team model, clean-sheet odds, "
                     "defensive contributions, saves and bonus.</p>",
                     "<h2>Top 30 overall</h2>", table(xp.head(30), spec)]
            for pos in ("GK", "DEF", "MID", "FWD"):
                parts += [f"<details><summary>Best {pos}s</summary>",
                          table(xp[xp.position == pos].head(15), spec), "</details>"]
            parts.append("<details><summary>Budget picks (≤ £5.5m) by points per £m</summary>" +
                         table(xp[xp.price <= 5.5].sort_values("xPts_per_m", ascending=False).head(20), spec) +
                         "</details>")
            if not tick.empty:
                tick["team"] = tick.team.map(nm)
                tick["opp"] = tick.opponent.map(nm).str[:3].str.upper() + np.where(tick.home, "(H)", "(a)")
                agg = tick.groupby("team").agg(xg=("xg_for", "sum"), cs=("p_cs", "sum")).reset_index()
                fx = tick.sort_values("gw").groupby("team").opp.agg(" ".join)
                agg["fixtures"] = agg.team.map(fx)
                parts.append("<h2>Fixture ticker (next 5 GWs)</h2>" + table(
                    agg.sort_values("xg", ascending=False),
                    [("team", "Team", "text"), ("fixtures", "Fixtures", "text"),
                     ("xg", "Exp. goals", "f1"), ("cs", "Exp. clean sheets", "f1")]))
            sections["FPL"] = "".join(parts)

    # --- players
    pt = props.player_table(con, None, [cur])
    pl_html = []
    if not pt.empty:
        pt = pt[pt.minutes >= 360]
        pt["team"] = pt.team.map(nm)
        for k in order:
            p = pt[pt.league == k]
            if p.empty:
                continue
            p = p.sort_values("xgi_p90", ascending=False).head(25)
            pl_html.append(f"<details><summary>{ln(k)}</summary>" + table(p, [
                ("player", "Player", "text"), ("team", "Team", "text"), ("minutes", "Mins", "int"),
                ("goals", "G", "int"), ("xg", "xG", "f1"), ("g_minus_xg", "G−xG", "signed"),
                ("assists", "A", "int"), ("xa", "xA", "f1"), ("xg_p90", "xG/90", "f2"), ("xa_p90", "xA/90", "f2"),
                ("shots_p90", "Sh/90", "f1"), ("big_chances_created", "BCC", "int")]) + "</details>")
    sections["Players"] = ('<p class="note">Top 25 per league by xG + xA per 90 (min 360 minutes).</p>' +
                           ("".join(pl_html) or '<p class="note">No player data yet.</p>'))

    # --- data
    cov = coverage(con)
    if not cov.empty:
        cov["league"] = cov.league.map(ln)
        cov["season"] = cov.season.map(config.season_label)
    sections["Data"] = table(cov, [("league", "League", "text"), ("season", "Season", "text"),
                                   ("played", "Played", "int"), ("with_xg", "Real xG", "int"),
                                   ("xg_sources", "xG from", "text"), ("with_big_chances", "Big chances", "int"),
                                   ("with_shots", "Shots", "int"), ("with_odds", "Odds", "int")])

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
    nav = "".join(f'<button data-t="s{i}" class="{"on" if i == 0 else ""}">{k}</button>'
                  for i, k in enumerate(sections))
    body = "".join(f'<section id="s{i}" class="{"on" if i == 0 else ""}">{v}</section>'
                   for i, v in enumerate(sections.values()))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Footy Edge</title>
<meta name="apple-mobile-web-app-capable" content="yes"><style>{CSS}</style></head><body>
<header><h1>⚽ Footy Edge</h1><div class="sub">Updated {stamp} · {config.season_label(cur)}</div></header>
<nav><div>{nav}</div></nav><main>{body}</main>
<footer>Probabilities, not certainties. Bet only what you can afford to lose · BeGambleAware.org · 0808 8020 133</footer>
<script>{JS}</script></body></html>"""
    out.write_text(page, encoding="utf-8")
    return out

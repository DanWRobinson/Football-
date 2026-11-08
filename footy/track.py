"""Live track record: predictions and flagged bets saved at the time each run made them, scored once
the games are played - the honest test, with no hindsight.

Every run logs (1) the model's and the market's probabilities for each upcoming fixture and (2) every
priced selection the Value tab could show. Scoring uses, for each match, the last snapshot taken
before kick-off.
"""

from __future__ import annotations

import datetime as dt
import html

import numpy as np
import pandas as pd

from . import config, db

ADJ_MIN = 0.03        # the Value tab's default: market-adjusted edge >= 3% ...
RAW_MIN = 0.05        # ... and the raw-model view at >= 5%
MAX_PRICE = 8.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS pred_log (
    run_at TEXT, match_id TEXT, league TEXT, date TEXT, kickoff TEXT,
    p_h REAL, p_d REAL, p_a REAL, p_o25 REAL, m_h REAL, m_d REAL, m_a REAL, m_o25 REAL,
    PRIMARY KEY (run_at, match_id)
);
CREATE TABLE IF NOT EXISTS value_log (
    run_at TEXT, match_id TEXT, sel_key TEXT, book TEXT, price REAL, p REAL, pb REAL, mp REAL, bookmaker TEXT,
    PRIMARY KEY (run_at, match_id, sel_key, book)
);
"""


def _f(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def log_run(con, pred: pd.DataFrame, value: list[dict]) -> None:
    con.executescript(SCHEMA)
    if pred is None or pred.empty:
        return
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
    rows = []
    for _, r in pred.iterrows():
        g = r.get
        rows.append((run_at, r.match_id, r.league, pd.Timestamp(r.date).date().isoformat(),
                     r.kickoff if isinstance(r.kickoff, str) else None, _f(g("H")), _f(g("D")), _f(g("A")),
                     _f(g("O2.5")), _f(g("mkt_H")), _f(g("mkt_D")), _f(g("mkt_A")), _f(g("mkt_O2.5"))))
    con.executemany("INSERT OR REPLACE INTO pred_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT OR REPLACE INTO value_log VALUES(?,?,?,?,?,?,?,?,?)",
                    [(run_at, v["id"], v["mkkey"], v["book"], _f(v["price"]), _f(v["p"]), _f(v.get("pb")),
                      _f(v.get("mp")), v.get("bk")) for v in value])
    con.commit()


# ------------------------------------------------------------------ scoring

def _pre_kickoff(df: pd.DataFrame) -> pd.DataFrame:
    """Keep each match's snapshots taken before kick-off (kick-off assumed 11:00 if unknown)."""
    ko = pd.to_datetime(df.date + "T" + df.kickoff.fillna("11:00"), errors="coerce")
    return df[pd.to_datetime(df.run_at) < ko]


def _settle(key: str, hg: int, ag: int) -> float | None:
    """Profit multiplier per unit stake given decimal odds later: returns 'fraction won' in
    {1, 0.5, 0, -0.5(half lost), -1(lost)} encoded as win share; None = refund."""
    if key in ("H", "D", "A"):
        return 1.0 if {"H": hg > ag, "D": hg == ag, "A": hg < ag}[key] else -1.0
    if key[0] in "OU" and key[1:2].replace(".", "").isdigit():
        line = float(key[1:])
        over = hg + ag > line
        return 1.0 if (over if key[0] == "O" else hg + ag < line) else -1.0
    if key.startswith("AH"):
        away = key.startswith("AHA")
        line = float(key[3:] if away else key[2:])
        d = (ag - hg) if away else (hg - ag)
        q = round(line * 4)
        parts = [line - 0.25, line + 0.25] if q % 2 else [line, line]
        res = [np.sign(d + x) for x in parts]
        return float(sum(res)) / 2 if any(res) else None
    return None


def score(con) -> dict:
    con.executescript(SCHEMA)
    pl = db.read_sql(con, "SELECT * FROM pred_log")
    if pl.empty:
        return {}
    played = db.read_sql(con, "SELECT match_id, home_goals AS hg, away_goals AS ag, home, away FROM matches "
                              "WHERE status='played' AND home_goals IS NOT NULL")
    from .models.data import team_names
    nm = team_names(con)
    played["match"] = played.home.map(lambda k: nm.get(k, k)) + " v " + played.away.map(lambda k: nm.get(k, k))
    pl = _pre_kickoff(pl).sort_values("run_at").groupby("match_id").tail(1).merge(played, on="match_id")
    out = {"since": db.read_sql(con, "SELECT MIN(run_at) AS s FROM pred_log").s.iloc[0][:10], "games": len(pl)}
    if pl.empty:
        return out
    pl["res"] = np.select([pl.hg > pl.ag, pl.hg == pl.ag], ["H", "D"], "A")
    pl["p_true"] = np.select([pl.res == "H", pl.res == "D"], [pl.p_h, pl.p_d], pl.p_a)
    pl["m_true"] = np.select([pl.res == "H", pl.res == "D"], [pl.m_h, pl.m_d], pl.m_a)
    pl["hit"] = pl[["p_h", "p_d", "p_a"]].values.argmax(axis=1) == pl.res.map({"H": 0, "D": 1, "A": 2}).values
    m = pl.dropna(subset=["m_true"])
    out.update(model_ll=float(-np.log(m.p_true.clip(1e-9)).mean()) if len(m) else None,
               market_ll=float(-np.log(m.m_true.clip(1e-9)).mean()) if len(m) else None,
               priced=len(m), hit=float(pl.hit.mean()))
    lg = []
    names = config.leagues()
    for k, g in pl.groupby("league"):
        gm = g.dropna(subset=["m_true"])
        lg.append({"league": names[k].name if k in names else k, "games": len(g),
                   "model": -np.log(gm.p_true.clip(1e-9)).mean() if len(gm) else np.nan,
                   "market": -np.log(gm.m_true.clip(1e-9)).mean() if len(gm) else np.nan})
    out["leagues"] = pd.DataFrame(lg)
    if "model" in out["leagues"]:
        out["leagues"]["gap"] = out["leagues"].model - out["leagues"].market

    # flagged bets: last pre-kick-off snapshot per selection
    vl = db.read_sql(con, "SELECT v.*, p.date, p.kickoff, p.league FROM value_log v JOIN "
                          "(SELECT DISTINCT match_id, date, kickoff, league FROM pred_log) p USING(match_id)")
    if not vl.empty:
        vl = _pre_kickoff(vl).sort_values("run_at").groupby(["match_id", "sel_key", "book"]).tail(1)
        vl = vl.merge(played, on="match_id")
        vl["adj_edge"] = vl.pb.fillna(vl.p) * vl.price - 1
        vl["raw_edge"] = vl.p * vl.price - 1
        vl["share"] = [_settle(k, h, a) for k, h, a in zip(vl.sel_key, vl.hg, vl.ag)]
        vl["pnl"] = [0.0 if s is None else (s * (pr - 1) if s > 0 else s) for s, pr in zip(vl.share, vl.price)]
        clo = closing(con, list(vl.match_id.unique()))
        vl["clv"] = [pr / clo[(m_, k)] - 1 if (m_, k) in clo else np.nan for m_, k, pr in zip(vl.match_id, vl.sel_key, vl.price)]
        ok = vl.price <= MAX_PRICE
        out["bets_adj"] = vl[ok & (vl.book == "best") & (vl.adj_edge >= ADJ_MIN)]
        out["bets_raw"] = vl[ok & (vl.book == "best") & (vl.raw_edge >= RAW_MIN)]
        out["bets_b365"] = vl[ok & (vl.book == "b365") & (vl.adj_edge >= ADJ_MIN)]
    return out


def closing(con, ids: list[str]) -> dict:
    """(match_id, key) -> margin-free closing odds, where football-data has published them."""
    from .models.value import devig
    od = db.odds_wide(con, ids)
    out = {}
    for _, r in od.iterrows():
        for market, sels, keys in (("1X2", "HDA", ["H", "D", "A"]), ("OU2.5", "OU", ["O2.5", "U2.5"])):
            for book in ("pinnacle_close", "avg_close"):
                cols = [f"{market}_{s}_{book}" for s in sels]
                if all(c in r and pd.notna(r[c]) for c in cols):
                    p = devig([r[c] for c in cols])
                    out.update({(r.match_id, k): 1 / q for k, q in zip(keys, p)})
                    break
    return out


def section(con) -> str:
    """HTML for the top of the Backtest tab."""
    from .report import table
    s = score(con)
    if not s or not s.get("games"):
        return ('<h2>Live track record</h2><p class="note">Starts with this run: every prediction and flagged bet is '
                "saved before kick-off and scored after the final whistle. First results appear after the next games finish.</p>")
    card = lambda l, v, sub="": f'<div class="mk"><span>{l}</span><b>{v}</b><span>{sub}</span></div>'  # noqa: E731
    h = [f'<h2>Live track record <span class="sub">since {html.escape(s["since"])}</span></h2>'
         '<p class="note">Saved before kick-off by each daily run and scored afterwards - no hindsight. Lower log loss '
         "is better; the market is the sharpest pre-match price we had (Pinnacle or exchanges). Small samples swing "
         "a lot: judge the model on hundreds of games and bets, not one weekend.</p>"]
    kp = [card("Games scored", s["games"], f'{s.get("priced", 0)} with market prices')]
    if s.get("model_ll") is not None:
        gap = s["model_ll"] - s["market_ll"]
        kp.append(card("Model vs market", f'{gap:+.3f}', "ahead" if gap < 0 else "behind (lower is better)"))
    kp.append(card("Top pick hit rate", f'{s.get("hit", 0):.0%}', "most likely result"))
    for key, label in (("bets_adj", "Flagged bets (default view)"), ("bets_b365", "Flagged at Bet365")):
        b = s.get(key)
        if b is not None and len(b):
            clv = b.clv.dropna()
            kp.append(card(label, f'{b.pnl.sum():+.1f}u', f'{len(b)} bets · ROI {b.pnl.mean():+.0%}'
                           + (f' · CLV {clv.mean():+.1%}' if len(clv) else "")))
    h.append('<div class="kpi">' + "".join(kp) + "</div>")
    if isinstance(s.get("leagues"), pd.DataFrame) and not s["leagues"].empty:
        h.append(table(s["leagues"].sort_values("games", ascending=False),
                       [("league", "League", "text"), ("games", "Games", "int"), ("model", "Model", "f3"),
                        ("market", "Market", "f3"), ("gap", "Gap", "f3")]))
    b = s.get("bets_adj")
    if b is not None and len(b):
        recent = b.sort_values("date", ascending=False).head(20).copy()
        recent["result"] = ["won" if x and x > 0.6 else "half won" if x and x > 0 else "refund" if x is None
                            else "half lost" if x > -0.6 else "lost" for x in recent.share]
        recent["score"] = recent.hg.astype(str) + "-" + recent.ag.astype(str)
        h.append("<details><summary>Recent flagged bets</summary>" + table(recent, [
            ("date", "Date", "text"), ("match", "Match", "text"), ("sel_key", "Pick", "text"),
            ("price", "Price", "f2"), ("bookmaker", "Where", "text"), ("adj_edge", "Edge", "signed_pct"),
            ("score", "Score", "text"), ("result", "Result", "text"), ("pnl", "P/L", "signed")]) + "</details>")
    return "".join(h)

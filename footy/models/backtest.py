"""Walk-forward backtest: refit weekly using only past data, score against results
and closing odds. Tells you whether the model actually beats the market in a league
before you trust its 'value' flags there."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import db
from .probs import markets, score_matrix
from .ratings import fit
from .value import devig, edge


def run(con, frame: pd.DataFrame, league: str, season: int, min_edge: float = 0.05,
        settings: dict | None = None, max_price: float = 8.0) -> tuple[pd.DataFrame, dict]:
    hist = pd.concat([frame[["league", "season", "home"]].rename(columns={"home": "team"}),
                      frame[["league", "season", "away"]].rename(columns={"away": "team"})]).drop_duplicates()
    target = frame[(frame.league == league) & (frame.season == season) & (frame.status == "played")]
    target = target.dropna(subset=["hg", "ag"])
    if target.empty:
        return pd.DataFrame(), {}
    od = db.odds_wide(con, list(target.match_id))
    target = target.merge(od, on="match_id", how="left")
    teams_now = sorted(set(target.home) | set(target.away))
    target["week"] = target.date.dt.to_period("W-MON").dt.start_time
    rows = []
    for wk, games in target.groupby("week"):
        try:
            rt = fit(frame, league, wk, settings, hist, teams_now)
        except ValueError:
            continue
        if rt.n_matches < 40:  # not enough history to say anything
            continue
        for _, r in games.iterrows():
            lh, la = rt.lambdas(r.home, r.away)
            mk = markets(score_matrix(lh, la, rt.rho))
            res = "H" if r.hg > r.ag else "D" if r.hg == r.ag else "A"
            row = dict(date=r.date, home=r.home, away=r.away, hg=r.hg, ag=r.ag, result=res,
                       p_H=mk["H"], p_D=mk["D"], p_A=mk["A"], p_O=mk["O2.5"], over=(r.hg + r.ag) > 2.5)
            for book in ("pinnacle_close", "avg_close", "pinnacle", "avg"):
                cols = [f"1X2_{s}_{book}" for s in "HDA"]
                if all(c in r and pd.notna(r[c]) for c in cols):
                    q = devig([r[c] for c in cols])
                    row.update(m_H=q[0], m_D=q[1], m_A=q[2], m_book=book)
                    break
            for s in "HDA":  # bet at the pre-match average price (what you could realistically get)
                row[f"o_{s}"] = r.get(f"1X2_{s}_avg", np.nan)
            row["o_O"] = r.get("OU2.5_O_avg", np.nan)
            row["o_U"] = r.get("OU2.5_U_avg", np.nan)
            rows.append(row)
    bt = pd.DataFrame(rows)
    if bt.empty:
        return bt, {}
    return bt, summarise(bt, min_edge, max_price)


def summarise(bt: pd.DataFrame, min_edge: float = 0.05, max_price: float = 8.0) -> dict:
    eps = 1e-12
    p_true = np.select([bt.result == "H", bt.result == "D"], [bt.p_H, bt.p_D], bt.p_A)
    out = {"matches": len(bt), "model_logloss": float(-np.mean(np.log(p_true + eps)))}
    onehot = np.column_stack([(bt.result == s).astype(float) for s in "HDA"])
    out["model_brier"] = float(np.mean(np.sum((bt[["p_H", "p_D", "p_A"]].values - onehot) ** 2, axis=1)))
    if "m_H" in bt and bt.m_H.notna().any():
        m = bt.dropna(subset=["m_H"])
        pm = np.select([m.result == "H", m.result == "D"], [m.m_H, m.m_D], m.m_A)
        pt = np.select([m.result == "H", m.result == "D"], [m.p_H, m.p_D], m.p_A)
        out["market_logloss"] = float(-np.mean(np.log(pm + eps)))
        out["model_logloss_same_games"] = float(-np.mean(np.log(pt + eps)))
    bets = []
    for _, r in bt.iterrows():
        for s, p, o, win in (("H", r.p_H, r.o_H, r.result == "H"), ("D", r.p_D, r.o_D, r.result == "D"),
                             ("A", r.p_A, r.o_A, r.result == "A"), ("O", r.p_O, r.o_O, r.over),
                             ("U", 1 - r.p_O, r.o_U, not r.over)):
            if pd.notna(o) and o <= max_price and edge(p, o) >= min_edge:
                bets.append(dict(sel=s, price=o, win=bool(win), pnl=(o - 1) if win else -1.0))
    b = pd.DataFrame(bets)
    out["bets"] = len(b)
    if len(b):
        out["roi"] = float(b.pnl.mean())
        out["profit_units"] = float(b.pnl.sum())
        out["hit_rate"] = float(b.win.mean())
        out["avg_price"] = float(b.price.mean())
    return out

"""Upcoming fixture predictions with market comparison."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, db
from .probs import markets, score_matrix
from .ratings import Ratings
from .value import devig, edge, kelly

BOOK_PREF = ["pinnacle", "avg", "b365"]


def _market_probs(row, market, sels):
    for book in BOOK_PREF:
        cols = [f"{market}_{s}_{book}" for s in sels]
        if all(c in row and pd.notna(row[c]) for c in cols):
            return devig([row[c] for c in cols]), book
    return None, None


def predict_fixtures(con, frame: pd.DataFrame, ratings: dict[str, Ratings],
                     days_ahead: int = 14, leagues: list[str] | None = None) -> pd.DataFrame:
    today = pd.Timestamp.today().normalize()
    fx = frame[(frame.status == "fixture") & (frame.date >= today) &
               (frame.date <= today + pd.Timedelta(days=days_ahead))]
    if leagues:
        fx = fx[fx.league.isin(leagues)]
    if fx.empty:
        return pd.DataFrame()
    od = db.odds_wide(con, list(fx.match_id))
    fx = fx.merge(od, on="match_id", how="left")
    max_goals = config.model_settings().get("max_goals", 10)
    rows = []
    for _, r in fx.iterrows():
        rt = ratings.get(r.league)
        if rt is None:
            continue
        lh, la = rt.lambdas(r.home, r.away)
        mk = markets(score_matrix(lh, la, rt.rho, max_goals))
        row = dict(match_id=r.match_id, league=r.league, date=r.date, kickoff=r.kickoff,
                   home=r.home, away=r.away, home_name=r.home_name, away_name=r.away_name,
                   xg_home=lh, xg_away=la, **{k: v for k, v in mk.items()})
        mp, book = _market_probs(r, "1X2", ["H", "D", "A"])
        if mp is not None:
            row.update(mkt_H=mp[0], mkt_D=mp[1], mkt_A=mp[2], mkt_book=book)
        ou, _ = _market_probs(r, "OU2.5", ["O", "U"])
        if ou is not None:
            row.update(**{"mkt_O2.5": ou[0], "mkt_U2.5": ou[1]})
        for m, s in (("1X2", "H"), ("1X2", "D"), ("1X2", "A"), ("OU2.5", "O"), ("OU2.5", "U")):
            best = r.get(f"{m}_{s}_max")
            if pd.isna(best) if best is not None else True:
                best = r.get(f"{m}_{s}_avg")
            row[f"best_{s if m == '1X2' else s + '2.5'}"] = best
        rows.append(row)
    return pd.DataFrame(rows)


def value_bets(pred: pd.DataFrame, min_edge: float = 0.05, max_price: float = 8.0,
               kelly_fraction: float = 0.25) -> pd.DataFrame:
    """Flatten predictions into one row per selection where model edge >= min_edge."""
    if pred.empty:
        return pred
    sels = [("1X2", "H", "H", "best_H", "mkt_H"), ("1X2", "D", "D", "best_D", "mkt_D"),
            ("1X2", "A", "A", "best_A", "mkt_A"), ("Over 2.5", "O2.5", "O2.5", "best_O2.5", "mkt_O2.5"),
            ("Under 2.5", "U2.5", "U2.5", "best_U2.5", "mkt_U2.5")]
    out = []
    for _, r in pred.iterrows():
        for market, label, pcol, ocol, mcol in sels:
            price = r.get(ocol)
            if price is None or pd.isna(price) or price > max_price:
                continue
            p = r[pcol]
            e = edge(p, price)
            if e >= min_edge:
                sel = {"H": r.home_name or r.home, "A": r.away_name or r.away, "D": "Draw"}.get(label, label)
                out.append(dict(date=r.date, league=r.league, match=f"{r.home_name or r.home} v {r.away_name or r.away}",
                                market=market, selection=sel, model_p=p, fair_odds=1 / p,
                                market_p=r.get(mcol, np.nan), price=price, edge=e,
                                stake_pct=kelly(p, price, kelly_fraction)))
    return pd.DataFrame(out).sort_values("edge", ascending=False) if out else pd.DataFrame()

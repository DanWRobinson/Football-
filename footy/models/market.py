"""Market-implied expected goals.

For each played match with closing odds, find the home/away expected goals that best reproduce
the bookmakers' margin-free closing probabilities (home win, away win, over 2.5). Closing prices
pack in information public data can't see - line-ups, injuries, motivation - so using them as an
extra target makes the team ratings sharper. Only closing prices of *past* matches are used, so
there is no look-ahead in backtests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .value import devig

BOOKS = ["pinnacle_close", "avg_close", "b365_close"]
_GRID = np.arange(0.15, 4.01, 0.05)


def _grid_markets():
    g = np.arange(11)
    lh, la = np.meshgrid(_GRID, _GRID, indexing="ij")
    lh, la = lh.ravel(), la.ravel()
    ph = poisson.pmf(g[None, :], lh[:, None])
    pa = poisson.pmf(g[None, :], la[:, None])
    M = ph[:, :, None] * pa[:, None, :]
    i, j = np.indices((11, 11))
    H = (M * (i > j)).sum(axis=(1, 2))
    A = (M * (i < j)).sum(axis=(1, 2))
    O = (M * (i + j > 2.5)).sum(axis=(1, 2))
    return lh, la, np.column_stack([H, A, O])


_LH, _LA, _MK = _grid_markets()


def implied_lambdas(probs: np.ndarray) -> np.ndarray:
    """probs: (n, 3) array of [P(home), P(away), P(over 2.5)] -> (n, 2) expected goals."""
    out = np.full((len(probs), 2), np.nan)
    ok = ~np.isnan(probs).any(axis=1)
    idx = np.where(ok)[0]
    for s in range(0, len(idx), 500):
        chunk = idx[s:s + 500]
        d = ((probs[chunk, None, :] - _MK[None, :, :]) ** 2).sum(axis=2)
        best = d.argmin(axis=1)
        out[chunk, 0], out[chunk, 1] = _LH[best], _LA[best]
    return out


def add_market_xg(frame: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """Adds h_mkt_xg / a_mkt_xg columns (NaN where no closing 1X2 + O/U 2.5 prices)."""
    f = frame.copy()
    f["h_mkt_xg"] = np.nan
    f["a_mkt_xg"] = np.nan
    if odds is None or odds.empty:
        return f
    o = odds.set_index("match_id")
    rows, ids = [], []
    for mid in f.loc[f.status == "played", "match_id"]:
        if mid not in o.index:
            continue
        r = o.loc[mid]
        for b in BOOKS:
            c1 = [f"1X2_{s}_{b}" for s in "HDA"]
            c2 = [f"OU2.5_{s}_{b}" for s in "OU"]
            if all(c in r.index and pd.notna(r[c]) for c in c1 + c2):
                p1 = devig([r[c] for c in c1])
                p2 = devig([r[c] for c in c2])
                rows.append([p1[0], p1[2], p2[0]])
                ids.append(mid)
                break
    if rows:
        lam = implied_lambdas(np.array(rows))
        m = pd.DataFrame({"match_id": ids, "h_mkt_xg": lam[:, 0], "a_mkt_xg": lam[:, 1]}).set_index("match_id")
        f = f.set_index("match_id")
        f.loc[m.index, "h_mkt_xg"] = m.h_mkt_xg
        f.loc[m.index, "a_mkt_xg"] = m.a_mkt_xg
        f = f.reset_index()
    return f

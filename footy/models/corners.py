"""Corners and goal-timing inputs for the match markets.

Corners: each side's expected corners = league average x (team's corners won vs average)
x (opponent's corners conceded vs average), with team rates shrunk toward the league average
(5 pseudo-games) and split by venue via the league's home/away averages. Totals and team lines
are then Poisson.

Halves: the league's share of goals scored before half-time (from football-data's half-time
scores), used to split each side's expected goals into first and second halves.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PSEUDO = 12
DEFAULT_H1 = 0.44


def first_half_share(frame: pd.DataFrame) -> dict[str, float]:
    p = frame[(frame.status == "played")].dropna(subset=["hg", "ag", "h_ht_goals", "a_ht_goals"]) \
        if "h_ht_goals" in frame else frame.iloc[0:0]
    out = {}
    for lg, g in p.groupby("league"):
        ft = (g.hg + g.ag).sum()
        if ft >= 200:
            out[lg] = float(((g.h_ht_goals + g.a_ht_goals).sum()) / ft)
    return out


def corner_rates(frame: pd.DataFrame, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict]:
    """team rates (for/against per game, shrunk) and league averages (home, away)."""
    p = frame[(frame.status == "played")].dropna(subset=["h_corners", "a_corners"])
    if as_of is not None:
        p = p[p.date < as_of]
    if p.empty:
        return pd.DataFrame(), {}
    p = p[p.season >= p.season.max() - 1]
    w = np.where(p.season == p.season.max(), 1.0, 0.5)   # last season counts half
    p = p.assign(w=w)
    lg_avg = {}
    for lg, g in p.groupby("league"):
        cur = g[g.season == g.season.max()]
        src = cur if len(cur) >= 60 else g   # this season's level once there's enough of it
        lg_avg[lg] = (np.average(src.h_corners, weights=src.w), np.average(src.a_corners, weights=src.w))
    rows = pd.concat([
        pd.DataFrame({"league": p.league, "team": p.home, "for": p.h_corners, "against": p.a_corners,
                      "exp_for": p.league.map(lambda l: lg_avg[l][0]), "exp_against": p.league.map(lambda l: lg_avg[l][1]),
                      "w": p.w}),
        pd.DataFrame({"league": p.league, "team": p.away, "for": p.a_corners, "against": p.h_corners,
                      "exp_for": p.league.map(lambda l: lg_avg[l][1]), "exp_against": p.league.map(lambda l: lg_avg[l][0]),
                      "w": p.w})])
    g = rows.groupby(["league", "team"])
    t = pd.DataFrame({
        "n": g.w.sum(),
        "for": g.apply(lambda x: (x["for"] * x.w).sum()), "exp_for": g.apply(lambda x: (x.exp_for * x.w).sum()),
        "against": g.apply(lambda x: (x.against * x.w).sum()),
        "exp_against": g.apply(lambda x: (x.exp_against * x.w).sum())}).reset_index()
    # ratio to what an average team would have produced in the same fixtures, shrunk toward 1
    t["att"] = (t["for"] + PSEUDO * t.exp_for / t.n) / (t.exp_for + PSEUDO * t.exp_for / t.n)
    t["dfn"] = (t["against"] + PSEUDO * t.exp_against / t.n) / (t.exp_against + PSEUDO * t.exp_against / t.n)
    return t, lg_avg


def expected_corners(fixtures: pd.DataFrame, frame: pd.DataFrame, as_of=None) -> pd.DataFrame:
    t, lg_avg = corner_rates(frame, as_of)
    out = fixtures.copy()
    if t.empty:
        out["corners_h"] = out["corners_a"] = np.nan
        return out
    idx = t.set_index(["league", "team"])
    ch, ca = [], []
    for r in out.itertuples():
        if r.league not in lg_avg:
            ch.append(np.nan)
            ca.append(np.nan)
            continue
        hm, aw = lg_avg[r.league]
        h = idx.loc[(r.league, r.home)] if (r.league, r.home) in idx.index else None
        a = idx.loc[(r.league, r.away)] if (r.league, r.away) in idx.index else None
        ch.append(hm * (h.att if h is not None else 1) * (a.dfn if a is not None else 1))
        ca.append(aw * (a.att if a is not None else 1) * (h.dfn if h is not None else 1))
    out["corners_h"], out["corners_a"] = ch, ca
    return out


# Real corner counts are over-dispersed: variance ~1.3x the mean (checked on 2026 data), so
# lines are priced with a negative binomial rather than Poisson.
VAR_RATIO = 1.3


def nb_over(mean: float, line: float) -> float:
    from scipy.stats import nbinom
    if not mean or mean != mean:
        return float("nan")
    r = mean / (VAR_RATIO - 1)
    return float(1 - nbinom.cdf(int(line), r, r / (r + mean)))

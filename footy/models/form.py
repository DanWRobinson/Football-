"""League tables, expected-points tables, regression flags and rolling form."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .probs import xpts_from_xg


def long_format(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per team per played match, with 'for' and 'against' metrics."""
    p = frame[frame.status == "played"].dropna(subset=["hg", "ag"]).copy()
    if p.empty:
        return pd.DataFrame()
    xp = [xpts_from_xg(h, a) if pd.notna(h) and pd.notna(a) else (np.nan, np.nan)
          for h, a in zip(p.h_xg, p.a_xg)]
    p["h_xpts"], p["a_xpts"] = [x[0] for x in xp], [x[1] for x in xp]
    metrics = ["xg", "xg_real", "npxg", "xgot", "big_chances", "big_chances_missed", "shots", "sot",
               "corners", "yellow", "red", "possession", "ppda", "deep", "xpts"]
    rows = []
    for side, opp, venue in (("h", "a", "home"), ("a", "h", "away")):
        d = pd.DataFrame({
            "match_id": p.match_id, "league": p.league, "season": p.season, "date": p.date,
            "team": p["home" if side == "h" else "away"], "opponent": p["away" if side == "h" else "home"],
            "venue": venue, "gf": p[f"{side}g"].astype(float), "ga": p[f"{opp}g"].astype(float),
            "xg_kind": p.xg_kind,
        })
        for m in metrics:
            if f"{side}_{m}" in p:
                d[f"{m}_for"] = p[f"{side}_{m}"].values
                d[f"{m}_against"] = p[f"{opp}_{m}"].values if m != "xpts" else np.nan
        d["pts"] = np.where(d.gf > d.ga, 3, np.where(d.gf == d.ga, 1, 0))
        rows.append(d)
    out = pd.concat(rows).sort_values(["date", "match_id"]).reset_index(drop=True)
    return out.rename(columns={"xpts_for": "xpts"}).drop(columns=["xpts_against"], errors="ignore")


def league_table(frame: pd.DataFrame, venue: str | None = None, last_n: int | None = None) -> pd.DataFrame:
    lf = long_format(frame)
    if lf.empty:
        return lf
    if venue in ("home", "away"):
        lf = lf[lf.venue == venue]
    if last_n:
        lf = lf.groupby("team", group_keys=False).apply(lambda g: g.tail(last_n))
    g = lf.groupby("team")
    t = pd.DataFrame({
        "P": g.size(), "W": g.apply(lambda x: (x.gf > x.ga).sum()), "D": g.apply(lambda x: (x.gf == x.ga).sum()),
        "L": g.apply(lambda x: (x.gf < x.ga).sum()), "GF": g.gf.sum(), "GA": g.ga.sum(), "Pts": g.pts.sum(),
        "xG": g.xg_for.sum(min_count=1), "xGA": g.xg_against.sum(min_count=1), "xPts": g.xpts.sum(min_count=1),
        "BC": g.big_chances_for.sum(min_count=1), "BCA": g.big_chances_against.sum(min_count=1),
        "Shots": g.shots_for.mean(), "ShotsA": g.shots_against.mean(),
        "SoT": g.sot_for.mean(), "SoTA": g.sot_against.mean(),
        "Corners": g.corners_for.mean(), "CornersA": g.corners_against.mean(),
        "Cards": (g.yellow_for.mean() + 2 * g.red_for.mean()),
        "real_xg_share": g.xg_kind.apply(lambda x: (x == "real").mean()),
    })
    t["GD"] = t.GF - t.GA
    t["xGD"] = t.xG - t.xGA
    t["xG/90"] = t.xG / t.P
    t["xGA/90"] = t.xGA / t.P
    t["BC/90"] = t.BC / t.P
    t["BCA/90"] = t.BCA / t.P
    t["G-xG"] = t.GF - t.xG          # +ve = finishing above chance quality (likely to cool)
    t["GA-xGA"] = t.GA - t.xGA       # -ve = conceding fewer than chances allowed (keeper/luck)
    t["Pts-xPts"] = t.Pts - t.xPts   # +ve = "lucky" on points
    t = t.sort_values(["Pts", "GD", "GF"], ascending=False)
    t.insert(0, "Pos", range(1, len(t) + 1))
    t["xPos"] = t.xPts.rank(ascending=False, method="min")
    return t.reset_index()


def rolling(frame: pd.DataFrame, team: str, window: int = 6) -> pd.DataFrame:
    lf = long_format(frame)
    d = lf[lf.team == team].copy()
    for c in ["xg_for", "xg_against", "gf", "ga", "big_chances_for", "big_chances_against", "xpts", "pts"]:
        if c in d:
            d[f"{c}_roll"] = d[c].rolling(window, min_periods=1).mean()
    return d


def regression_flags(table: pd.DataFrame, min_games: int = 6) -> pd.DataFrame:
    """Teams whose results most diverge from underlying numbers."""
    t = table[table.P >= min_games].copy()
    if t.empty:
        return t.assign(luck_score=pd.Series(dtype=float), verdict=pd.Series(dtype=str))
    t["luck_score"] = (t["Pts-xPts"] / t.P).round(2)
    t["verdict"] = np.select(
        [t.luck_score > 0.35, t.luck_score < -0.35],
        ["Overperforming - expect results to drop", "Underperforming - expect results to improve"],
        "In line with performances")
    return t.sort_values("luck_score", key=abs, ascending=False)

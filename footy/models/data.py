"""Turn raw DB rows into an analysis-ready match frame.

For each match we pick each metric from the best available source (per-league
priority) and, where no real xG exists, compute a shots-based proxy xG whose
coefficients are fitted on matches that have both real xG and shots.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, db

DEFAULT_PROXY = (0.27, 0.035)  # xG per shot on target, per shot off target
METRICS = ["xg", "npxg", "xgot", "big_chances", "big_chances_missed", "shots", "sot", "shots_in_box",
           "corners", "yellow", "red", "possession", "ppda", "deep", "touches_opp_box", "fouls"]


def _priority(league: str) -> list[str]:
    try:
        lg = config.league(league)
        return list(lg.xg_sources) + ["football_data"]
    except KeyError:
        return ["understat", "fotmob", "api_football", "football_data", "demo"]


def calibrate_proxy(frame: pd.DataFrame) -> tuple[float, float]:
    """Least squares xG ~ a*SOT + b*off-target shots on rows with both."""
    cols = ["xg", "shots", "sot"]
    rows = []
    for s in ("h", "a"):
        part = frame[[f"{s}_{c}" for c in cols]].copy()
        part.columns = cols
        rows.append(part)
    d = pd.concat(rows).dropna()
    d = d[d.shots >= d.sot]
    if len(d) < 200:
        return DEFAULT_PROXY
    X = np.column_stack([d.sot, d.shots - d.sot])
    coef, *_ = np.linalg.lstsq(X, d.xg.values, rcond=None)
    if not (0.1 < coef[0] < 0.5 and 0 <= coef[1] < 0.15):
        return DEFAULT_PROXY
    return float(coef[0]), float(coef[1])


def match_frame(con, leagues: list[str] | None = None, seasons: list[int] | None = None,
                proxy: tuple[float, float] | None = None) -> pd.DataFrame:
    m = db.matches(con, leagues, seasons)
    if m.empty:
        return m
    st = db.read_sql(con, "SELECT * FROM team_match_stats")
    st = st[st.match_id.isin(set(m.match_id))]
    m = m.rename(columns={"home_goals": "hg", "away_goals": "ag"})
    if not st.empty:
        st = st.merge(m[["match_id", "league"]], on="match_id")
        # rank sources per league
        ranks = {}
        for lg in st.league.unique():
            pr = _priority(lg)
            for s in st.source.unique():
                ranks[(lg, s)] = pr.index(s) if s in pr else len(pr)
        st["rank"] = [ranks[(l, s)] for l, s in zip(st.league, st.source)]
        st = st.sort_values("rank").drop_duplicates(["match_id", "side", "metric"])
        xg_src = st[(st.metric == "xg") & (st.side == "h")].set_index("match_id").source
        wide = st.pivot_table(index="match_id", columns=["side", "metric"], values="value", aggfunc="first")
        wide.columns = [f"{s}_{mt}" for s, mt in wide.columns]
        m = m.merge(wide, left_on="match_id", right_index=True, how="left")
        m["xg_source"] = m.match_id.map(xg_src)
    for s in ("h", "a"):
        for mt in METRICS:
            if f"{s}_{mt}" not in m:
                m[f"{s}_{mt}"] = np.nan
    if "xg_source" not in m:
        m["xg_source"] = None

    a, b = proxy or calibrate_proxy(m)
    for s in ("h", "a"):
        m[f"{s}_xg_proxy"] = a * m[f"{s}_sot"] + b * (m[f"{s}_shots"] - m[f"{s}_sot"]).clip(lower=0)
        m[f"{s}_xg_real"] = m[f"{s}_xg"]
        m[f"{s}_xg"] = m[f"{s}_xg"].fillna(m[f"{s}_xg_proxy"])
    m["xg_kind"] = np.where(m.h_xg_real.notna(), "real", np.where(m.h_xg_proxy.notna(), "proxy", "none"))
    m.loc[m.status != "played", "xg_kind"] = "none"
    m.attrs["proxy_coef"] = (a, b)
    return m


def team_names(con) -> dict[str, str]:
    """canonical key -> nice display name (most common provider spelling)."""
    df = db.read_sql(con, "SELECT home AS k, home_name AS n FROM matches UNION ALL "
                          "SELECT away, away_name FROM matches")
    if df.empty:
        return {}
    df = df.dropna()
    best = df.groupby("k").n.agg(lambda x: x.value_counts().index[0])
    return best.to_dict()

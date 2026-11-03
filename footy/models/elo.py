"""Our own Elo ratings, computed from every result in the database.

Used when ClubElo can't be reached (it refuses some cloud servers). Teams start from a
level that reflects their league (Premier League highest, National League lowest; top
European leagues in between) and then move with each result: standard football Elo with
home advantage and a goal-difference multiplier. Because promoted/relegated teams keep
their rating and European games connect the leagues, the scale is comparable across
competitions - which is what the team-rating priors need.
"""

from __future__ import annotations

import pandas as pd

from .. import config

START = {"E0": 1650, "E1": 1450, "E2": 1330, "E3": 1230, "EC": 1130, "SP1": 1620, "I1": 1610, "D1": 1600,
         "F1": 1570, "P1": 1520, "N1": 1500, "B1": 1480, "SC0": 1420}
OUTSIDER_START = 1450   # clubs we only see in European competition
K = 20.0
HOME = 65.0


def compute(frame: pd.DataFrame) -> pd.DataFrame:
    """DataFrame[league, team, elo] for every team in the current season (one row per competition)."""
    p = frame[frame.status == "played"].dropna(subset=["hg", "ag"]).sort_values("date")
    if p.empty:
        return pd.DataFrame(columns=["league", "team", "elo"])
    cfg = config.leagues()
    r: dict[str, float] = {}
    for lg, h, a, hg, ag in zip(p.league, p.home, p.away, p.hg, p.ag):
        start = START.get(lg, OUTSIDER_START) if not (lg in cfg and cfg[lg].is_europe) else OUTSIDER_START
        rh = r.setdefault(h, start)
        ra = r.setdefault(a, start)
        we = 1 / (10 ** (-(rh - ra + HOME) / 400) + 1)
        w = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        gd = abs(int(hg) - int(ag))
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = K * mult * (w - we)
        r[h], r[a] = rh + delta, ra - delta
    cur = frame[frame.season == frame.season.max()]
    rows = pd.concat([cur[["league", "home"]].rename(columns={"home": "team"}),
                      cur[["league", "away"]].rename(columns={"away": "team"})]).drop_duplicates()
    rows["elo"] = rows.team.map(r)
    return rows.dropna(subset=["elo"]).reset_index(drop=True)

"""FPL expected points using FPL's own xG/xA plus fixture-adjusted team ratings.

Scoring (2025/26 rules): appearance 1 (<60 min) / 2 (60+); goal GK 10, DEF 6, MID 5,
FWD 4; assist 3; clean sheet GK/DEF 4, MID 1; -1 per 2 goals conceded (GK/DEF);
GK +1 per 3 saves; defensive contributions +2 (DEF 10 CBIT, MID/FWD 12 CBIRT);
bonus 1-3; yellow -1, red -3.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .. import db
from .ratings import Ratings

GOAL_PTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
EUROPE_ROTATION = 0.93   # start probability multiplier for a GW straight after a European game
CS_PTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
DC_THRESHOLD = {"GK": None, "DEF": 10, "MID": 12, "FWD": 12}
# prior per-90 rates used to shrink small samples (pseudo-minutes)
PRIOR = {"GK": (0.0, 0.01), "DEF": (0.05, 0.06), "MID": (0.18, 0.15), "FWD": (0.38, 0.11)}
PSEUDO_MIN = 450


def load(con):
    players = db.read_sql(con, "SELECT * FROM fpl_players")
    fixtures = db.read_sql(con, "SELECT * FROM fpl_fixtures")
    events = db.read_sql(con, "SELECT * FROM fpl_events")
    teams = db.read_sql(con, "SELECT * FROM fpl_teams")
    return players, fixtures, events, teams


def next_event(events: pd.DataFrame) -> int | None:
    nxt = events[events.is_next == 1]
    if len(nxt):
        return int(nxt.id.iloc[0])
    unf = events[events.finished == 0]
    return int(unf.id.min()) if len(unf) else None


def team_games_played(fixtures: pd.DataFrame) -> dict[str, int]:
    f = fixtures[fixtures.finished == 1]
    return pd.concat([f.team_h, f.team_a]).value_counts().to_dict()


def europe_dates(con) -> dict[str, list]:
    """team -> dates of its European matches (any UEFA competition in the DB)."""
    from .. import config
    keys = [k for k, v in config.leagues().items() if v.is_europe]
    if not keys:
        return {}
    m = db.read_sql(con, f"SELECT date, home, away FROM matches WHERE league IN ({','.join('?' * len(keys))})", keys)
    out: dict[str, list] = {}
    for _, r in m.iterrows():
        d = pd.Timestamp(r.date)
        out.setdefault(r.home, []).append(d)
        out.setdefault(r.away, []).append(d)
    return out


def recent_starts(con, fixtures: pd.DataFrame, last_n: int = 6) -> pd.DataFrame:
    """Share of each player's team's last N finished fixtures that he started."""
    try:
        h = db.read_sql(con, "SELECT * FROM fpl_history")
    except Exception:
        return pd.DataFrame()
    if h.empty:
        return pd.DataFrame(columns=["recent_start_rate"])
    done = fixtures[fixtures.finished == 1].sort_values("kickoff")
    team_last = {}
    for t in pd.concat([done.team_h_id, done.team_a_id]).unique():
        f = done[(done.team_h_id == t) | (done.team_a_id == t)].tail(last_n)
        team_last[t] = set(f.id)
    players = db.read_sql(con, "SELECT id, team_id FROM fpl_players").set_index("id").team_id
    h = h[h.player_id.isin(players.index)]
    h["team_id"] = h.player_id.map(players)
    h = h[[fx in team_last.get(t, set()) for fx, t in zip(h.fixture, h.team_id)]]
    out = h.groupby("player_id").starts.sum().to_frame("starts_recent")
    out["recent_start_rate"] = out.starts_recent / [max(len(team_last.get(players[i], ())), 1) for i in out.index]
    return out.clip(upper=1.0)


def expected_points(con, ratings: Ratings, horizon: int = 5, start_gw: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    players, fixtures, events, teams = load(con)
    if players.empty:
        return pd.DataFrame(), pd.DataFrame()
    gw0 = start_gw or next_event(events)
    gws = list(range(gw0, gw0 + horizon))
    up = fixtures[(fixtures.event.isin(gws)) & (fixtures.finished == 0)]
    gp = team_games_played(fixtures)
    recent = recent_starts(con, fixtures)

    # average expected goals for each team vs league-average opposition (for scaling FPL xG)
    avg_for = {t: np.exp(ratings.mu + ratings.att.get(t, -0.15) + ratings.home_adv / 2) for t in ratings.teams}
    avg_against = {t: np.exp(ratings.mu + ratings.dfn.get(t, 0.15) + ratings.home_adv / 2) for t in ratings.teams}

    euro = europe_dates(con)

    def after_europe(team, kickoff) -> bool:
        k = pd.to_datetime(kickoff, errors="coerce", utc=True)
        if pd.isna(k):
            return False
        k = k.tz_localize(None).normalize()
        return any(pd.Timedelta(days=1) <= k - d <= pd.Timedelta(days=4) for d in euro.get(team, []))

    # per-team fixture list: (gw, opponent, home?, lam_for, lam_against, after_europe)
    team_fx: dict[str, list] = {}
    for _, f in up.iterrows():
        lh, la = ratings.lambdas(f.team_h, f.team_a)
        team_fx.setdefault(f.team_h, []).append((int(f.event), f.team_a, True, lh, la,
                                                 after_europe(f.team_h, f.kickoff)))
        team_fx.setdefault(f.team_a, []).append((int(f.event), f.team_h, False, la, lh,
                                                 after_europe(f.team_a, f.kickoff)))

    rows = []
    for _, p in players.iterrows():
        pos = p.position
        mins = p.minutes or 0.0
        starts = p.starts or 0.0
        played = gp.get(p.team, 0) or 1
        start_rate = min(starts / played, 1.0) if played else 0.0
        if p.id in recent.index:  # recent role matters more than the season average
            rr = recent.loc[p.id]
            start_rate = 0.65 * rr.recent_start_rate + 0.35 * start_rate
        chance = p.chance_next / 100 if pd.notna(p.chance_next) else None
        if p.status in ("i", "s", "u", "n"):   # injured / suspended / unavailable / not in squad
            avail = chance if chance is not None else 0.0
        else:
            avail = chance if chance is not None else 1.0
        p_start = start_rate * avail
        mins_per_start = min(mins / starts, 90) if starts else 0
        p_sub = max(avail - p_start, 0) * 0.5
        exp_mins = p_start * max(mins_per_start, 60) + p_sub * 20
        p60 = p_start * (0.92 if mins_per_start >= 75 else 0.7)
        p_any = min(p_start + p_sub, 1.0)

        g_prior, a_prior = PRIOR.get(pos, (0.1, 0.1))
        xg90 = ((p.xg or 0) + g_prior * PSEUDO_MIN / 90) / ((mins + PSEUDO_MIN) / 90)
        xa90 = ((p.xa or 0) + a_prior * PSEUDO_MIN / 90) / ((mins + PSEUDO_MIN) / 90)
        saves90 = (p.saves or 0) / max(mins, 1) * 90 if mins >= 180 else (3.0 if pos == "GK" else 0)
        bonus90 = (p.bonus or 0) / max(mins, 1) * 90 if mins >= 180 else 0.2
        dc90 = (p.defensive_contribution or 0) / max(mins, 1) * 90 if mins >= 180 else 0
        cards90 = ((p.yellow or 0) + 3 * (p.red or 0)) / max(mins, 1) * 90 if mins >= 180 else 0.1

        per_gw = {gw: 0.0 for gw in gws}
        n_euro = 0
        for gw, opp, home, lam_for, lam_against, euro_before in team_fx.get(p.team, []):
            f_att = lam_for / avg_for.get(p.team, lam_for)
            f_def = lam_against / avg_against.get(p.team, lam_against)
            rot = EUROPE_ROTATION if euro_before and start_rate > 0.5 else 1.0
            n_euro += bool(euro_before)
            m90 = exp_mins * rot / 90
            e_goals = xg90 * f_att * m90
            e_ast = xa90 * f_att * m90
            # clean sheet: opponent scores 0 while he's on (60+ mins needed)
            p_cs = np.exp(-lam_against)
            e_gc_pen = sum(poisson.pmf(k, lam_against) * (k // 2) for k in range(1, 11))
            p60_g, p_any_g = p60 * rot, p_any * rot
            pts = p60_g * 2 + (p_any_g - p60_g) * 1
            pts += e_goals * GOAL_PTS.get(pos, 4) + e_ast * 3
            pts += p60_g * p_cs * CS_PTS.get(pos, 0)
            if pos in ("GK", "DEF"):
                pts -= p60_g * e_gc_pen
            if pos == "GK":
                pts += m90 * (saves90 * f_def) / 3
            thr = DC_THRESHOLD.get(pos)
            if thr and dc90 > 0:
                pts += p_start * rot * 2 * (1 - poisson.cdf(thr - 1, dc90 * min(mins_per_start, 90) / 90))
            pts += m90 * bonus90 * (0.5 + 0.5 * f_att if pos in ("MID", "FWD") else 0.5 + 0.5 / max(f_def, 0.3))
            pts -= m90 * cards90
            per_gw[gw] += pts
        row = dict(id=p.id, player=p.web_name, team=p.team, position=pos, price=p.price,
                   selected_pct=p.selected_pct, status=p.status, chance_next=p.chance_next, news=p.news,
                   minutes=mins, start_rate=start_rate, xg90=xg90, xa90=xa90,
                   fpl_xg=p.xg, fpl_xa=p.xa, total_points=p.total_points, europe_gws=n_euro)
        for gw in gws:
            row[f"GW{gw}"] = per_gw[gw]
        row["xPts_next"] = per_gw[gws[0]]
        row[f"xPts_{horizon}gw"] = sum(per_gw.values())
        row["xPts_per_m"] = row[f"xPts_{horizon}gw"] / p.price if p.price else np.nan
        rows.append(row)
    xp = pd.DataFrame(rows).sort_values(f"xPts_{horizon}gw", ascending=False)

    # fixture ticker: per team per GW, attack lambda and CS probability
    tick = []
    for t, lst in team_fx.items():
        for gw, opp, home, lf, la, eu in lst:
            tick.append(dict(team=t, gw=gw, opponent=opp, home=home, xg_for=lf, xg_against=la, p_cs=np.exp(-la),
                             after_europe=eu))
    return xp, pd.DataFrame(tick)

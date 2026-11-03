"""Player prop pricing: anytime scorer, shots and shots on target."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .. import db
from .ratings import Ratings

SOURCE_PREF = ["understat", "fotmob", "api_football", "demo"]


def player_table(con, leagues=None, seasons=None) -> pd.DataFrame:
    """Merge player rows across sources (preferring Understat for xG, FotMob for big chances)."""
    df = db.read_sql(con, "SELECT * FROM players")
    if df.empty:
        return df
    if leagues:
        df = df[df.league.isin(leagues)]
    if seasons:
        df = df[df.season.isin(seasons)]
    df["src_rank"] = df.source.map({s: i for i, s in enumerate(SOURCE_PREF)}).fillna(9)
    df = df.sort_values("src_rank")
    df["pkey"] = df.league + "|" + df.season.astype(str) + "|" + df.team.fillna("") + "|" + \
        df.player.str.lower().str.replace(r"[^a-z]", "", regex=True)
    num = ["games", "minutes", "goals", "npg", "xg", "npxg", "assists", "xa", "shots", "sot", "key_passes",
           "big_chances_created", "big_chances_missed", "xg_chain", "xg_buildup", "yellow", "red"]
    agg = {c: "first" for c in ["league", "season", "player", "team", "team_name", "position", "source"]}
    agg.update({c: "first" for c in num})  # first non-null in preference order
    out = df.groupby("pkey").agg(agg).reset_index(drop=True)
    m90 = out.minutes / 90
    for c in ["goals", "xg", "npxg", "assists", "xa", "shots", "sot", "key_passes", "big_chances_created",
              "xg_chain"]:
        out[f"{c}_p90"] = np.where(m90 > 0, out[c] / m90, np.nan)
    out["xgi_p90"] = out.xg_p90.fillna(0) + out.xa_p90.fillna(0)
    out["g_minus_xg"] = out.goals - out.xg
    out["mins_per_game"] = out.minutes / out.games
    return out


# Per-90 priors that small samples are shrunk toward (a typical outfield regular), and the
# pseudo-minutes of weight they get. 270 = three full games: a player with 3 games is pulled
# halfway to typical, one with a season of minutes barely moves.
PRIOR_P90 = {"xg": 0.09, "xa": 0.07, "shots": 1.0, "sot": 0.33}
PSEUDO_MIN = 270
MAX_SOT_SHARE = 0.55   # even elite finishers rarely get more than ~half their shots on target
TYPICAL_SOT_SHARE = 0.35
SHARE_PSEUDO_SHOTS = 10


def player_rates(players: pd.DataFrame) -> pd.DataFrame:
    """One row per (team, player) with shrunk per-90 rates for pricing props.

    Rates come from the player's domestic-league totals when he has enough domestic minutes:
    European competition samples are tiny (3-6 games) and FotMob's European leaderboards report
    unreliable minutes. Falls back to all competitions combined for clubs we only see in Europe.
    """
    from .. import config
    if players.empty:
        return players
    cfg = config.leagues()
    df = players.copy()
    df["europe"] = df.league.map(lambda k: k in cfg and cfg[k].is_europe)
    df["pk"] = df.team.fillna("") + "|" + df.player.str.lower().str.replace(r"[^a-z]", "", regex=True)
    tot = ["minutes", "games", "goals", "xg", "xa", "shots", "sot"]
    rows = []
    for pk, g in df.groupby("pk"):
        dom = g[~g.europe]
        use = dom if dom.minutes.fillna(0).sum() >= PSEUDO_MIN else g
        t = {c: use[c].sum(min_count=1) for c in tot}
        mins = t["minutes"] or 0
        r = {"team": g.team.iloc[0], "player": g.player.iloc[0], "position": g.position.dropna().iloc[0]
             if g.position.notna().any() else None, "minutes": mins, "games": t["games"],
             "rate_source": "domestic" if use is dom else "all comps",
             "mins_per_game": (dom.minutes.sum() / dom.games.sum()) if len(dom) and dom.games.sum() else
             (mins / t["games"] if t["games"] else np.nan)}
        for c, prior in PRIOR_P90.items():
            val = t[c]
            r[f"{c}_p90"] = np.nan if pd.isna(val) and c in ("shots", "sot") else \
                ((0 if pd.isna(val) else val) + prior * PSEUDO_MIN / 90) / ((mins + PSEUDO_MIN) / 90)
        # shots on target = shot rate x on-target share, the share shrunk toward the typical 35%
        # with 10 pseudo-shots (a player with 4 shots and 3 on target isn't a 75% shooter)
        if pd.notna(r["shots_p90"]):
            sh_tot, sot_tot = t["shots"], t["sot"]
            if pd.notna(sh_tot) and pd.notna(sot_tot) and sh_tot > 0:
                share = (min(sot_tot, sh_tot) + TYPICAL_SOT_SHARE * SHARE_PSEUDO_SHOTS) / (sh_tot + SHARE_PSEUDO_SHOTS)
            else:
                share = TYPICAL_SOT_SHARE
            r["sot_share"] = min(share, MAX_SOT_SHARE)
            r["sot_p90"] = r["shots_p90"] * r["sot_share"]
        rows.append(r)
    return pd.DataFrame(rows)


def price_props(players: pd.DataFrame, fixtures: pd.DataFrame, ratings: dict[str, Ratings],
                min_minutes: float = 450) -> pd.DataFrame:
    """For each upcoming fixture, price props for regular players of both teams."""
    if players.empty or fixtures.empty:
        return pd.DataFrame()
    rates = player_rates(players)
    pl = rates[rates.minutes >= min_minutes]
    rows = []
    for _, f in fixtures.iterrows():
        rt = ratings.get(f.league)
        if rt is None:
            continue
        lh, la = rt.lambdas(f.home, f.away)
        for team, opp, lam, home in ((f.home, f.away, lh, True), (f.away, f.home, la, False)):
            base = np.exp(rt.mu + rt.att.get(team, -0.15) + rt.home_adv / 2)
            mult = lam / base
            squad = pl[pl.team == team]
            for _, p in squad.iterrows():
                exp_m = min(p.mins_per_game if pd.notna(p.mins_per_game) else 70, 90) / 90
                xg = p.xg_p90 * mult * exp_m
                xa = p.xa_p90 * mult * exp_m
                sh = p.shots_p90 * mult * exp_m if pd.notna(p.shots_p90) else np.nan
                if pd.notna(p.sot_p90):
                    sot = p.sot_p90 * mult * exp_m
                else:
                    sot = sh * 0.35 if pd.notna(sh) else np.nan   # typical on-target share
                p_score = 1 - np.exp(-xg)
                rows.append(dict(
                    date=f.date, league=f.league, match=f"{f.home_name or f.home} v {f.away_name or f.away}",
                    player=p.player, team=team, position=p.position, rate_source=p.rate_source,
                    exp_xg=xg, exp_xa=xa, p_anytime=p_score, fair_anytime=1 / p_score if p_score > 0 else np.nan,
                    p_score_or_assist=1 - np.exp(-(xg + xa)),
                    exp_shots=sh, p_1plus_shots=1 - poisson.pmf(0, sh) if pd.notna(sh) else np.nan,
                    p_2plus_shots=1 - poisson.cdf(1, sh) if pd.notna(sh) else np.nan,
                    exp_sot=sot, p_1plus_sot=1 - poisson.pmf(0, sot) if pd.notna(sot) else np.nan,
                    p_2plus_sot=1 - poisson.cdf(1, sot) if pd.notna(sot) else np.nan,
                ))
    return pd.DataFrame(rows).sort_values("p_anytime", ascending=False) if rows else pd.DataFrame()

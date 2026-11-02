"""Ratings for UEFA club competitions, where the two teams come from different leagues.

Every club gets a strength on one common scale:

  * clubs from our 13 domestic leagues: their domestic attack/defence rating (relative to
    their own league's average) + an offset for how strong that league is;
  * other clubs (Galatasaray, Red Star, ...): their ClubElo rating;
  * unknown clubs: a below-average default.

League offsets start from the league's average ClubElo and are then corrected by actual
European results (xG-weighted, time-decayed, shrunk toward the Elo prior), together with a
small per-club correction for clubs without a domestic rating. The result is an ordinary
`Ratings` object, so predictions, the match explorer and props all work unchanged.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .. import config
from .ratings import Ratings, _target

UNKNOWN_STRENGTH = -0.3


def _domestic_home(frame: pd.DataFrame, season: int) -> dict[str, str]:
    """team -> its domestic league key (current season, else last season)."""
    cfg = config.leagues()
    dom = frame[frame.league.map(lambda k: k in cfg and not cfg[k].is_europe)]
    out = {}
    for ssn in (season - 1, season):  # current season overrides
        cur = dom[dom.season == ssn]
        for col in ("home", "away"):
            out.update(dict(zip(cur[col], cur.league)))
    return out


def fit_europe(frame: pd.DataFrame, comp: str, domestic: dict[str, Ratings],
               elo: dict[str, float] | None = None, as_of=None, settings: dict | None = None) -> Ratings:
    s = {**config.model_settings(), **(settings or {})}
    as_of = pd.Timestamp(as_of or dt.date.today() + dt.timedelta(days=1))
    season = config.current_season(as_of.date())
    elo = elo or {}
    scale = s.get("elo_scale", 0.003)
    cfg = config.leagues()
    europe_keys = [k for k, v in cfg.items() if v.is_europe]

    comp_rows = frame[frame.league == comp]
    teams = sorted(set(comp_rows[comp_rows.season == season].home) | set(comp_rows[comp_rows.season == season].away))
    # European results from all UEFA competitions inform league strength
    played = frame[frame.league.isin(europe_keys) & (frame.status == "played") & (frame.date < as_of)
                   & (frame.season >= season - 1)].dropna(subset=["hg", "ag"])
    teams = sorted(set(teams) | set(played.home) | set(played.away))
    if not teams:
        raise ValueError(f"No teams for {comp}")
    home_league = _domestic_home(frame, season)

    # reference Elo = average over clubs in this season's European competitions
    elo_vals = [elo[t] for t in teams if t in elo]
    ref = float(np.mean(elo_vals)) if elo_vals else None

    # domestic relative ratings (centred on each league's average team)
    centre = {}
    for lk, r in domestic.items():
        cur = [t for t in r.teams if t in r.att]
        centre[lk] = (np.mean([r.att[t] for t in cur]), np.mean([r.dfn[t] for t in cur]), r.mu)
    groups = sorted({home_league[t] for t in teams if home_league.get(t) in domestic})
    gidx = {g: i for i, g in enumerate(groups)}
    loose = [t for t in teams if home_league.get(t) not in domestic]
    lidx = {t: i for i, t in enumerate(loose)}

    base_att, base_dfn, grp, basis = {}, {}, {}, {}
    prior_off = np.zeros(len(groups))
    for g in groups:
        lg_teams = [t for t, l in home_league.items() if l == g]
        lg_elo = [elo[t] for t in lg_teams if t in elo]
        if ref is not None and lg_elo:
            prior_off[gidx[g]] = (np.mean(lg_elo) - ref) * scale
    mean_mu = np.mean([c[2] for c in centre.values()]) if centre else 0.0
    for t in teams:
        g = home_league.get(t)
        if g in domestic and t in domestic[g].att:
            ca, cd, cmu = centre[g]
            # league scoring level (mu) is part of the league's character - split it evenly
            base_att[t] = domestic[g].att[t] - ca + (cmu - mean_mu) / 2
            base_dfn[t] = domestic[g].dfn[t] - cd + (cmu - mean_mu) / 2
            grp[t], basis[t] = gidx[g], f"domestic {g}"
        elif t in elo and ref is not None:
            st = (elo[t] - ref) * scale
            base_att[t], base_dfn[t], grp[t], basis[t] = st / 2, -st / 2, None, "elo"
        else:
            base_att[t], base_dfn[t], grp[t], basis[t] = UNKNOWN_STRENGTH / 2, -UNKNOWN_STRENGTH / 2, None, "unknown"

    nG, nL = len(groups), len(loose)

    def strengths(theta):
        off = prior_off + theta[2:2 + nG]
        res = theta[2 + nG:]
        att, dfn = {}, {}
        for t in teams:
            o = off[grp[t]] if grp[t] is not None else 0.0
            r = res[lidx[t]] if t in lidx else 0.0
            att[t] = base_att[t] + o / 2 + r / 2
            dfn[t] = base_dfn[t] - o / 2 - r / 2
        return att, dfn

    theta0 = np.r_[np.log(1.4), 0.15, np.zeros(nG + nL)]
    if len(played) >= 10:
        age = (as_of - played.date).dt.days.values
        w = np.exp(-np.log(2) * age / s.get("half_life_days", 240))
        yh, ya = _target(played, "h", s), _target(played, "a", s)
        H, A = played.home.values, played.away.values
        ridge = s.get("ridge", 2.0) * 1.5

        def nll(theta):
            att, dfn = strengths(theta)
            eh = theta[0] + theta[1] + np.array([att[h] + dfn[a] for h, a in zip(H, A)])
            ea = theta[0] + np.array([att[a] + dfn[h] for h, a in zip(H, A)])
            f = np.sum(w * (np.exp(eh) - yh * eh)) + np.sum(w * (np.exp(ea) - ya * ea))
            return f + ridge * np.sum(theta[2:] ** 2)

        theta = minimize(nll, theta0, method="L-BFGS-B").x
    else:
        theta = theta0
    att, dfn = strengths(theta)
    r = Ratings(league=comp, teams=teams, mu=float(theta[0]), home_adv=float(theta[1]), att=att, dfn=dfn,
                rho=-0.05, n_matches=len(played), fitted_at=str(as_of.date()), priors=basis)
    r.league_strength = pd.DataFrame({
        "league": groups, "elo_prior": prior_off, "from_results": theta[2:2 + nG],
        "strength": prior_off + theta[2:2 + nG]}).sort_values("strength", ascending=False)
    return r

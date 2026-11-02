"""Team attack/defence ratings fitted on time-decayed, xG-weighted results.

    log E[home goals] = mu + home_adv + att[home] + dfn[away]
    log E[away goals] = mu           + att[away] + dfn[home]

`dfn` is "defensive leakiness" (higher = concedes more). Targets blend xG and goals:
xG is far more predictive than goals over small samples, but goals carry a bit of
finishing/keeper signal. Ridge shrinkage toward a prior keeps early-season and
promoted-team ratings sensible.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

from .. import config
from .probs import dc_tau


@dataclass
class Ratings:
    league: str
    teams: list[str]
    mu: float
    home_adv: float
    att: dict[str, float]
    dfn: dict[str, float]
    rho: float = -0.05
    n_matches: int = 0
    fitted_at: str = ""
    priors: dict[str, str] = field(default_factory=dict)

    def lambdas(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        ah, dh = self.att.get(home, self._prior_att(home)), self.dfn.get(home, self._prior_dfn(home))
        aa, da = self.att.get(away, self._prior_att(away)), self.dfn.get(away, self._prior_dfn(away))
        ha = 0.0 if neutral else self.home_adv
        return float(np.exp(self.mu + ha + ah + da)), float(np.exp(self.mu + aa + dh))

    def _prior_att(self, team):
        return -0.15

    def _prior_dfn(self, team):
        return 0.15

    def table(self) -> pd.DataFrame:
        rows = []
        for t in self.teams:
            # average expected goals for/against vs a league-average opponent, home/away averaged
            gf = np.exp(self.mu + self.att[t] + self.home_adv / 2)
            ga = np.exp(self.mu + self.dfn[t] + self.home_adv / 2)
            rows.append(dict(team=t, attack=self.att[t], defence=-self.dfn[t],
                             exp_gf=gf, exp_ga=ga, exp_gd=gf - ga, prior=self.priors.get(t, "")))
        return pd.DataFrame(rows).sort_values("exp_gd", ascending=False).reset_index(drop=True)


def team_priors(history: pd.DataFrame, league: str, season: int, teams: list[str]) -> dict[str, str]:
    """'promoted' / 'relegated' / '' for each team, based on where it played last season.

    history: DataFrame[league, season, team] over all leagues in the DB.
    """
    cfg = config.leagues()
    if league not in cfg:
        return {}
    me = cfg[league]
    prev = history[history.season == season - 1]
    if prev[prev.league == league].empty:
        return {}  # no last-season data for this league: can't tell
    last_here = set(prev[prev.league == league].team)
    out = {}
    for t in teams:
        if t in last_here:
            out[t] = ""
            continue
        elsewhere = prev[prev.team == t].league.unique()
        tiers = [cfg[l].tier for l in elsewhere if l in cfg and cfg[l].country == me.country]
        out[t] = "relegated" if tiers and min(tiers) < me.tier else "promoted"
    return out


def _target(df: pd.DataFrame, side: str, s: dict) -> np.ndarray:
    goals = df[f"{side}g"].astype(float).values
    xg = df[f"{side}_xg"].astype(float).values
    kind = df["xg_kind"].values
    w_real, w_proxy = s.get("xg_weight", 0.7), s.get("proxy_xg_weight", 0.5)
    y = goals.copy()
    real = (kind == "real") & ~np.isnan(xg)
    prox = (kind == "proxy") & ~np.isnan(xg)
    y[real] = w_real * xg[real] + (1 - w_real) * goals[real]
    y[prox] = w_proxy * xg[prox] + (1 - w_proxy) * goals[prox]
    return y


def fit(frame: pd.DataFrame, league: str, as_of: dt.date | pd.Timestamp | None = None,
        settings: dict | None = None, history: pd.DataFrame | None = None,
        teams_now: list[str] | None = None) -> Ratings:
    """Fit ratings for `league` using played matches strictly before `as_of`."""
    s = {**config.model_settings(), **(settings or {})}
    as_of = pd.Timestamp(as_of or dt.date.today() + dt.timedelta(days=1))
    df = frame[(frame.league == league) & (frame.status == "played") & (frame.date < as_of)].dropna(
        subset=["hg", "ag"]).copy()
    if df.empty:
        raise ValueError(f"No played matches for {league} before {as_of.date()}")
    season_now = config.current_season(as_of.date())
    teams = sorted(set(df.home) | set(df.away) | set(teams_now or []))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi, ai = df.home.map(idx).values, df.away.map(idx).values
    age = (as_of - df.date).dt.days.values
    w = np.exp(-np.log(2) * age / s.get("half_life_days", 240))
    yh, ya = _target(df, "h", s), _target(df, "a", s)

    prior_kind = team_priors(history, league, season_now, teams) if history is not None else {}
    pa, pd_ = np.zeros(n), np.zeros(n)
    for t, k in prior_kind.items():
        if k == "promoted":
            pa[idx[t]], pd_[idx[t]] = s.get("promoted_prior", -0.2), -s.get("promoted_prior", -0.2)
        elif k == "relegated":
            pa[idx[t]], pd_[idx[t]] = s.get("relegated_prior", 0.2), -s.get("relegated_prior", 0.2)
    ridge = s.get("ridge", 2.0)

    def nll(theta):
        mu, ha = theta[0], theta[1]
        att, dfn = theta[2:2 + n], theta[2 + n:]
        eh = mu + ha + att[hi] + dfn[ai]
        ea = mu + att[ai] + dfn[hi]
        lh, la = np.exp(eh), np.exp(ea)
        f = np.sum(w * (lh - yh * eh)) + np.sum(w * (la - ya * ea))
        f += ridge * (np.sum((att - pa) ** 2) + np.sum((dfn - pd_) ** 2))
        rh, ra = w * (lh - yh), w * (la - ya)
        g = np.zeros_like(theta)
        g[0] = rh.sum() + ra.sum()
        g[1] = rh.sum()
        ga, gd = np.zeros(n), np.zeros(n)
        np.add.at(ga, hi, rh)
        np.add.at(ga, ai, ra)
        np.add.at(gd, ai, rh)
        np.add.at(gd, hi, ra)
        g[2:2 + n] = ga + 2 * ridge * (att - pa)
        g[2 + n:] = gd + 2 * ridge * (dfn - pd_)
        return f, g

    mean_goals = max(np.average(np.r_[yh, ya], weights=np.r_[w, w]), 0.3)
    theta0 = np.r_[np.log(mean_goals), 0.2, pa, pd_]
    res = minimize(nll, theta0, jac=True, method="L-BFGS-B")
    th = res.x
    att = dict(zip(teams, th[2:2 + n]))
    dfn = dict(zip(teams, th[2 + n:]))
    r = Ratings(league=league, teams=teams, mu=float(th[0]), home_adv=float(th[1]), att=att, dfn=dfn,
                n_matches=len(df), fitted_at=str(as_of.date()), priors=prior_kind)
    r.rho = fit_rho(df, r, w)
    return r


def fit_rho(df: pd.DataFrame, r: Ratings, w: np.ndarray) -> float:
    lam = np.array([r.lambdas(h, a) for h, a in zip(df.home, df.away)])
    hg, ag = df.hg.astype(int).values, df.ag.astype(int).values
    base = poisson.logpmf(hg, lam[:, 0]) + poisson.logpmf(ag, lam[:, 1])
    low = (hg <= 1) & (ag <= 1)

    def nll(rho):
        tau = np.array([dc_tau(x, y, l1, l2, rho) if lw else 1.0
                        for x, y, l1, l2, lw in zip(hg, ag, lam[:, 0], lam[:, 1], low)])
        if np.any(tau <= 0):
            return 1e9
        return -np.sum(w * (base + np.log(tau)))

    res = minimize_scalar(nll, bounds=(-0.2, 0.1), method="bounded")
    return float(res.x) if res.success else -0.05


def fit_all(frame: pd.DataFrame, leagues: list[str], as_of=None, settings=None) -> dict[str, Ratings]:
    history = pd.concat([frame[["league", "season", "home"]].rename(columns={"home": "team"}),
                         frame[["league", "season", "away"]].rename(columns={"away": "team"})]).drop_duplicates()
    out = {}
    season = config.current_season(pd.Timestamp(as_of).date() if as_of is not None else None)
    for lg in leagues:
        cur = frame[(frame.league == lg) & (frame.season == season)]
        teams_now = sorted(set(cur.home) | set(cur.away))
        try:
            out[lg] = fit(frame, lg, as_of, settings, history, teams_now)
        except ValueError:
            continue
    return out

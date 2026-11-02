"""Referee and cards model for bookings markets.

Expected cards in a match = average of (home cards for, away cards provoked) and
(away cards for, home cards provoked), scaled by the referee's strictness relative to the
league (shrunk toward 1 with 8 pseudo-games so a referee with 3 games isn't extreme).
Booking points = 10 per yellow, 25 per red (UK convention).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import nbinom

PSEUDO = 8
DISPERSION = 8.0   # cards are over-dispersed vs Poisson; negative binomial with this size


def _cards(frame: pd.DataFrame) -> pd.DataFrame:
    p = frame[(frame.status == "played")].dropna(subset=["h_yellow", "a_yellow"]).copy()
    p["h_cards"] = p.h_yellow + p.h_red.fillna(0)
    p["a_cards"] = p.a_yellow + p.a_red.fillna(0)
    p["cards"] = p.h_cards + p.a_cards
    p["bpts"] = 10 * (p.h_yellow + p.a_yellow) + 25 * (p.h_red.fillna(0) + p.a_red.fillna(0))
    return p


def referee_table(frame: pd.DataFrame) -> pd.DataFrame:
    p = _cards(frame)
    if "referee" not in p or p.referee.isna().all():
        return pd.DataFrame()
    league_avg = p.groupby("league").cards.mean()
    p = p.dropna(subset=["referee"])
    g = p.groupby(["league", "referee"])
    t = pd.DataFrame({"games": g.size(), "cards_pg": g.cards.mean(), "reds": g.apply(
        lambda x: (x.h_red.fillna(0) + x.a_red.fillna(0)).sum()), "booking_pts_pg": g.bpts.mean()}).reset_index()
    t["league_avg"] = t.league.map(league_avg)
    t["strictness"] = (t.cards_pg * t.games + t.league_avg * PSEUDO) / (t.league_avg * (t.games + PSEUDO))
    return t.sort_values("strictness", ascending=False)


def team_card_rates(frame: pd.DataFrame) -> pd.DataFrame:
    p = _cards(frame)
    rows = pd.concat([
        pd.DataFrame({"league": p.league, "team": p.home, "for": p.h_cards, "against": p.a_cards}),
        pd.DataFrame({"league": p.league, "team": p.away, "for": p.a_cards, "against": p.h_cards})])
    return rows.groupby(["league", "team"]).agg(cards_for=("for", "mean"), cards_against=("against", "mean"),
                                                games=("for", "size")).reset_index()


def expected_cards(fixtures: pd.DataFrame, frame: pd.DataFrame, season: int | None = None) -> pd.DataFrame:
    """Adds exp_cards, p_over_3.5 / 4.5 / 5.5 cards and ref strictness to each fixture."""
    if fixtures.empty:
        return fixtures
    f = frame if season is None else frame[frame.season >= season - 1]
    rates = team_card_rates(f).set_index(["league", "team"])
    refs = referee_table(f)
    strict = refs.set_index(["league", "referee"]).strictness.to_dict() if not refs.empty else {}
    lg_avg = _cards(f).groupby("league").cards.mean().to_dict()
    out = fixtures.copy()
    exp, sl = [], []
    for _, r in out.iterrows():
        avg = lg_avg.get(r.league, 4.0) / 2
        h = rates.loc[(r.league, r.home)] if (r.league, r.home) in rates.index else None
        a = rates.loc[(r.league, r.away)] if (r.league, r.away) in rates.index else None
        hc = np.nanmean([h.cards_for if h is not None else avg, a.cards_against if a is not None else avg])
        ac = np.nanmean([a.cards_for if a is not None else avg, h.cards_against if h is not None else avg])
        s = strict.get((r.league, r.get("referee")), 1.0) if pd.notna(r.get("referee")) else 1.0
        exp.append((hc + ac) * s)
        sl.append(s)
    out["exp_cards"] = exp
    out["ref_strictness"] = sl
    for line in (3.5, 4.5, 5.5):
        mu = out.exp_cards.values
        p = DISPERSION / (DISPERSION + mu)
        out[f"cards_O{line}"] = 1 - nbinom.cdf(int(line), DISPERSION, p)
    return out

import datetime as dt

import pytest

from footy import config, db, pipeline
from footy.demo import build
from footy.models import fpl, predict
from footy.models.data import match_frame
from footy.models.ratings import fit_all
from footy.pipeline import latest_elo
from footy.sources.base import MatchRecord


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("eu") / "demo.db"
    build(str(path), today=dt.date.today())
    con = db.connect(path)
    frame = match_frame(con)
    return con, frame, fit_all(frame, sorted(frame.league.unique()), elo=latest_elo(con))


def test_config_has_european_comps():
    assert config.resolve_leagues("europe") == ["UCL", "UEL", "UECL"]
    assert config.league("UCL").is_europe and not config.league("E0").is_europe
    assert "UCL" not in config.resolve_leagues("domestic")


def test_weaker_league_detected(demo):
    _, _, r = demo
    ls = r["UCL"].league_strength.set_index("league").strength
    assert ls["E0"] - ls["E1"] > 0.15     # truth: 0.35
    assert r["UCL"].priors["nordvik"] == "elo"


def test_european_fixtures_predicted(demo):
    con, frame, r = demo
    pred = predict.predict_fixtures(con, frame, r, 30)
    ucl = pred[pred.league == "UCL"]
    assert len(ucl) > 0 and ucl[["H", "D", "A"]].sum(axis=1).round(6).eq(1).all()
    assert ucl.mkt_H.isna().all() if "mkt_H" in ucl else True   # no free odds for Europe


def test_cross_competition_strength_is_consistent(demo):
    """A top E0 side should be favoured at home to a mid E1 side in Europe."""
    _, frame, r = demo
    e0 = r["E0"].table().team.iloc[0]
    e1 = r["E1"].table().team.iloc[12]
    if e0 in r["UCL"].att and e1 in r["UCL"].att:
        lh, la = r["UCL"].lambdas(e0, e1)
        assert lh > la


def test_fpl_europe_rotation_flag(demo):
    con, frame, r = demo
    xp, tick = fpl.expected_points(con, r["E0"], 6)
    assert "europe_gws" in xp and "after_europe" in tick


def test_european_names_link_to_domestic_teams(tmp_path):
    con = db.connect(tmp_path / "t.db")
    db.upsert_match(con, league="P1", season=2026, date="2026-09-01", home="Sp Lisbon", away="Porto",
                    home_goals=1, away_goals=1)
    db.upsert_match(con, league="E0", season=2026, date="2026-09-01", home="Arsenal", away="Chelsea",
                    home_goals=1, away_goals=0)
    recs = [MatchRecord(league="UCL", season=2026, date="2026-10-01", home="Arsenal", away="Sporting CP",
                        home_goals=2, away_goals=1, home_stats={"xg": 1.9}, away_stats={"xg": 0.8})]
    pipeline.store_matches(con, "fotmob", recs, backbone=True,
                           extra_known=pipeline._domestic_teams(con, [2025, 2026]))
    row = con.execute("SELECT home, away FROM matches WHERE league='UCL'").fetchone()
    assert tuple(row) == ("arsenal", "sp lisbon")

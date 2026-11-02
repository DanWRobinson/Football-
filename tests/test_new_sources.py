import datetime as dt

import pandas as pd
import pytest

from footy import db
from footy.demo import build
from footy.models import news, predict, referees
from footy.models.data import match_frame
from footy.models.fpl import expected_points, recent_starts
from footy.models.ratings import elo_priors, fit_all
from footy.pipeline import latest_elo
from footy.sources import clubelo, fotmob, understat


def test_clubelo_parse():
    text = ("Rank,Club,Country,Level,Elo,From,To\n1,Man City,ENG,1,2045.1,2026-10-20,2026-11-01\n"
            "None,Forest,ENG,1,1801.0,2026-10-20,2026-11-01\n3,Bayern,GER,1,1990.5,2026-10-20,2026-11-01\n")
    df = clubelo.parse(text)
    assert list(df.club) == ["Man City", "Forest", "Bayern"] and df.elo.iloc[0] == 2045.1
    assert int(df.level.iloc[2]) == 1


def test_elo_priors_centred():
    pri = elo_priors({"a": 1900, "b": 1800, "c": 1700, "d": 1800}, ["a", "b", "c", "d"], 0.003)
    assert pri["a"] == pytest.approx(0.3) and pri["c"] == pytest.approx(-0.3) and pri["b"] == 0
    assert elo_priors({"a": 1900}, ["a", "b", "c", "d", "e", "f"], 0.003) == {}


def test_fotmob_news_and_referee():
    payload = {"content": {
        "matchFacts": {"infoBox": {"Referee": {"text": "Michael Oliver", "country": "England"}}},
        "lineup": {"homeTeam": {"name": "Bolton", "unavailable": [
            {"id": 1, "name": "John Smith", "unavailability": {"type": "injury", "expectedReturn": "Late November"}}]},
            "awayTeam": {"name": "Wigan", "unavailable": [
                {"id": 2, "name": "Tom Jones", "unavailability": {"type": "suspension"}}]}}}}
    assert fotmob.extract_referee(payload) == "Michael Oliver"
    out = fotmob.extract_unavailable(payload)
    assert out == [("h", "John Smith", "out", "injury", "Late November"), ("a", "Tom Jones", "out", "suspension", None)]


def test_understat_shot_split():
    shots = {"h": [{"xG": "0.76", "situation": "Penalty"}, {"xG": "0.1", "situation": "OpenPlay"},
                   {"xG": "0.35", "situation": "FromCorner"}],
             "a": [{"xG": "0.05", "situation": "DirectFreekick"}]}
    h, a = understat.shot_metrics(shots)
    assert h == {"xg_open_play": 0.1, "xg_set_piece": 0.35, "xg_penalty": 0.76, "shots_xg30": 2.0}
    assert a["xg_set_piece"] == 0.05 and a["xg_open_play"] == 0


def test_player_name_matching():
    assert news._same_player("M. Salah", "Mohamed Salah")
    assert news._same_player("Salah", "Mohamed Salah")
    assert news._same_player("Kylian Mbappé", "Kylian Mbappe")
    assert not news._same_player("Ben White", "Kyle Walker")
    assert not news._same_player("J. Smith", "Tom Smith")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("n") / "demo.db"
    build(str(path), today=dt.date.today())
    con = db.connect(path)
    return con, match_frame(con)


def test_news_lowers_expected_goals(demo):
    con, frame = demo
    ratings = fit_all(frame, ["E0", "E1"], elo=latest_elo(con))
    with_news = predict.predict_fixtures(con, frame, ratings, 30)
    no_news = predict.predict_fixtures(con, frame, ratings, 30, apply_news=False)
    m = with_news.merge(no_news[["match_id", "xg_home"]], on="match_id", suffixes=("", "_raw"))
    hit = m[m.news_home.str.contains("attack")]
    assert len(hit) > 0 and (hit.xg_home < hit.xg_home_raw).all()
    assert (m[m.news_home == ""].xg_home <= m[m.news_home == ""].xg_home_raw + 1e-9).all()
    # Bet365 pricing works (demo has no b365 odds -> empty), best works
    assert predict.value_bets(with_news, 0.0, book="b365").empty
    assert not predict.value_bets(with_news, 0.0, book="best").empty


def test_elo_prior_used(demo):
    con, frame = demo
    r = fit_all(frame, ["E0"], elo=latest_elo(con))["E0"]
    assert sum("elo" in v for v in r.priors.values()) == 20


def test_referees_and_cards(demo):
    con, frame = demo
    rt = referees.referee_table(frame)
    assert len(rt[rt.league == "E0"]) == 12 and rt.strictness.between(0.5, 1.6).all()
    fx = frame[frame.status == "fixture"].head(5)
    out = referees.expected_cards(fx, frame)
    assert out.exp_cards.between(1, 10).all()
    assert (out["cards_O3.5"] >= out["cards_O4.5"]).all()


def test_recent_starts_used(demo):
    con, frame = demo
    fx = db.read_sql(con, "SELECT * FROM fpl_fixtures")
    assert recent_starts(con, fx).empty  # demo has no history -> falls back to season rates
    ratings = fit_all(frame, ["E0"])
    xp, _ = expected_points(con, ratings["E0"], 2)
    assert len(xp) == 280

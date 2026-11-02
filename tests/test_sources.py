import json
from pathlib import Path

from footy import config
from footy.sources import api_football, fotmob, football_data, understat

FX = Path(__file__).parent / "fixtures"


def test_football_data_parse():
    recs = football_data.parse_csv((FX / "E0_sample.csv").read_text(), config.league("E0"), 2025)
    assert len(recs) == 3
    r = recs[0]
    assert (r.date, r.home, r.away, r.home_goals, r.away_goals) == ("2025-08-15", "Liverpool", "Bournemouth", 4, 2)
    assert r.home_stats["shots"] == 19 and r.away_stats["sot"] == 3 and r.home_stats["corners"] == 6
    odds = {(m, s, b): p for m, s, b, p, _ in r.odds}
    assert odds[("1X2", "H", "pinnacle")] == 1.31
    assert odds[("1X2", "H", "pinnacle_close")] == 1.29
    assert odds[("OU2.5", "O", "avg")] == 1.39
    assert odds[("OU2.5", "U", "avg_close")] == 3.00


def test_football_data_season_code():
    assert football_data.season_code(2026) == "2627"
    assert football_data.season_code(2099) == "9900"


def test_understat_parse():
    data = json.loads((FX / "understat_league.json").read_text())
    matches, players = understat.parse_league(data, config.league("E0"), 2025)
    assert len(matches) == 4
    liv = matches[0]
    assert liv.home_stats["xg"] == "2.68" and liv.home_stats["deep"] == 12
    assert abs(liv.home_stats["ppda"] - 8.8) < 1e-9
    assert matches[3].home_goals is None and not matches[3].home_stats
    assert players[0].player == "Mohamed Salah" and players[0].stats["xa"] == "0.40"


def test_understat_embedded_fallback():
    payload = json.dumps([{"a": 1}]).encode("unicode_escape").decode()
    html = f"<script>var datesData = JSON.parse('{payload}');</script>"
    assert understat._embedded(html, "datesData") == [{"a": 1}]


def test_fotmob_match_stats():
    h, a = fotmob.extract_match_stats(json.loads((FX / "fotmob_match.json").read_text()))
    assert h["xg"] == 1.84 and a["xg"] == 0.67
    assert h["big_chances"] == 3 and a["big_chances_missed"] == 1
    assert h["xgot"] == 1.95 and h["sot"] == 7 and a["corners"] == 3 and h["possession"] == 58


def test_fotmob_league_matches(monkeypatch):
    src = fotmob.FotMob()
    monkeypatch.setattr(src, "league_payload", lambda lg, s: json.loads((FX / "fotmob_league.json").read_text()))
    monkeypatch.setattr(src, "match_payload", lambda mid, fin: json.loads((FX / "fotmob_match.json").read_text()))
    recs = src.matches(config.league("E2"), 2025)
    assert recs[0].home_goals == 2 and recs[0].away_goals == 0 and recs[0].home_stats["xg"] == 1.84
    assert recs[1].home_goals is None


def test_api_football_stats():
    payload = {"response": [
        {"team": {"name": "A"}, "statistics": [{"type": "expected_goals", "value": "1.21"},
                                                {"type": "Ball Possession", "value": "61%"},
                                                {"type": "Total Shots", "value": 14}]},
        {"team": {"name": "B"}, "statistics": [{"type": "expected_goals", "value": "0.70"},
                                                {"type": "Total Shots", "value": None}]}]}
    h, a = api_football.parse_statistics(payload)
    assert h == {"xg": 1.21, "possession": 61.0, "shots": 14.0}
    assert a == {"xg": 0.70}

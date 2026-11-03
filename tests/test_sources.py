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


def test_fotmob_tolerates_odd_shapes():
    payload = {"content": {"stats": [{"key": ["not", "a", "string"], "stats": [1, 2]},
                                     {"key": "expected_goals", "stats": ["1.2", "0.4"]},
                                     {"Referee": {"text": ["x"]}}]}}
    h, a = fotmob.extract_match_stats(payload)
    assert h == {"xg": 1.2} and a == {"xg": 0.4}
    assert fotmob.extract_referee(payload) is None


def test_dead_host_fails_fast(monkeypatch):
    import requests
    from footy.sources import http
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectTimeout("nope")
    monkeypatch.setattr(http.requests, "get", boom)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(http, "_host_failures", {})
    import pytest as _pt
    for _ in range(3):
        with _pt.raises(http.FetchError):
            http.get("https://blocked.example/x", ttl=0)
    assert len(calls) == 3   # first call burns the 3 strikes, later calls never touch the network


def test_belgian_aliases():
    from footy.names import canonical
    assert canonical("OH Leuven") == canonical("Oud-Heverlee Leuven")
    assert canonical("Sporting Charleroi") == canonical("Charleroi")


def test_fotmob_player_shots_per90_converted(monkeypatch):
    src = fotmob.FotMob()
    monkeypatch.setattr(src, "league_payload", lambda lg, s: {"x": "/stats/47/season/123/expected_goals.json"})

    def fake_get(url, **k):
        stat = url.rsplit("/", 1)[-1].replace(".json", "")
        val = {"expected_goals": 6.6, "total_scoring_att": 3.8, "ontarget_scoring_att": 1.8}.get(stat)
        if val is None:
            raise fotmob.FetchError("nope")
        return {"TopLists": [{"StatList": [{"ParticipantName": "Erling Haaland", "ParticiantId": 1, "TeamName": "Man City",
                                             "StatValue": val, "MinutesPlayed": 810, "MatchesPlayed": 9}]}]}
    monkeypatch.setattr(fotmob, "get", fake_get)
    rec = src.players(config.league("E0"), 2026)[0]
    assert rec.stats["xg"] == 6.6 and rec.stats["shots"] == 34.2 and rec.stats["sot"] == 16.2

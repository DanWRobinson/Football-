"""API-Football (api-sports.io) - optional paid source.

Set API_FOOTBALL_KEY to enable. Supplies match xG ("expected_goals" in fixture
statistics), shots, possession etc. for every league in the config, including the
National League. Per-fixture statistics cost one request each, so results are cached
forever once a match is finished and the pipeline skips fixtures it already has.
"""

from __future__ import annotations

import os

from ..config import League
from .base import MatchRecord, Source
from .http import get

BASE = "https://v3.football.api-sports.io"

STAT_MAP = {
    "expected_goals": "xg", "Total Shots": "shots", "Shots on Goal": "sot",
    "Shots insidebox": "shots_in_box", "Corner Kicks": "corners", "Ball Possession": "possession",
    "Fouls": "fouls", "Yellow Cards": "yellow", "Red Cards": "red", "goals_prevented": "goals_prevented",
}


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return None


def parse_statistics(payload: dict) -> tuple[dict, dict]:
    resp = payload.get("response") or []
    out = []
    for team in resp[:2]:
        d = {}
        for s in team.get("statistics", []):
            if s.get("type") in STAT_MAP and _num(s.get("value")) is not None:
                d[STAT_MAP[s["type"]]] = _num(s["value"])
        out.append(d)
    while len(out) < 2:
        out.append({})
    return out[0], out[1]


class APIFootball(Source):
    name = "api_football"
    requires_key = "API_FOOTBALL_KEY"

    def __init__(self):
        self.skip_ids: set[str] = set()
        self.max_stat_calls = int(os.environ.get("API_FOOTBALL_MAX_CALLS", "90"))

    def available(self):
        return bool(os.environ.get(self.requires_key))

    def supports(self, lg: League) -> bool:
        return bool(lg.api_football)

    def _get(self, path, params, ttl):
        return get(f"{BASE}/{path}", params=params, as_json=True, ttl=ttl,
                   headers={"x-apisports-key": os.environ[self.requires_key]})

    def _fixtures(self, lg, season):
        data = self._get("fixtures", {"league": lg.api_football, "season": season}, 3 * 3600)
        return data.get("response") or []

    def matches(self, lg, season, with_stats=True):
        out, calls = [], 0
        for f in self._fixtures(lg, season):
            fx, teams, goals = f["fixture"], f["teams"], f["goals"]
            finished = (fx.get("status") or {}).get("short") in {"FT", "AET", "PEN"}
            rec = MatchRecord(league=lg.key, season=season, date=fx["date"][:10], kickoff=fx["date"][11:16],
                              home=teams["home"]["name"], away=teams["away"]["name"],
                              home_goals=goals["home"] if finished else None,
                              away_goals=goals["away"] if finished else None,
                              source_id=str(fx["id"]))
            if with_stats and finished and rec.source_id not in self.skip_ids and calls < self.max_stat_calls:
                stats = self._get("fixtures/statistics", {"fixture": fx["id"]}, -1)
                rec.home_stats, rec.away_stats = parse_statistics(stats)
                calls += 1
            out.append(rec)
        return out

    def fixtures(self, lg):
        from ..config import current_season
        return [r for r in self.matches(lg, current_season(), with_stats=False) if r.home_goals is None]

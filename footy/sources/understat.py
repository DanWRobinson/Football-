"""Understat: match xG, team PPDA/deep completions and player xG/xA for the big 5.

Understat loads league data from `/getLeagueData/{league}/{season}` (JSON, needs the
X-Requested-With header). Older page versions embedded the same data as
`JSON.parse('...')` inside the HTML; we fall back to parsing that.
"""

from __future__ import annotations

import codecs
import json
import re

from ..config import League
from .base import MatchRecord, PlayerRecord, Source
from .http import FetchError, get

BASE = "https://understat.com"


def _embedded(html: str, var: str):
    m = re.search(rf"var\s+{var}\s*=\s*JSON\.parse\('(.*?)'\)", html, re.S)
    if not m:
        return None
    return json.loads(codecs.decode(m.group(1), "unicode_escape"))


def load_league(league: str, season: int) -> dict:
    try:
        data = get(f"{BASE}/getLeagueData/{league}/{season}", as_json=True, ttl=6 * 3600,
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "Referer": f"{BASE}/league/{league}/{season}"})
        if isinstance(data, dict) and data.get("dates"):
            return data
    except (FetchError, ValueError):
        pass
    html = get(f"{BASE}/league/{league}/{season}", ttl=6 * 3600)
    data = {k: _embedded(html, v) for k, v in
            (("dates", "datesData"), ("teams", "teamsData"), ("players", "playersData"))}
    if not data["dates"]:
        raise FetchError(f"Understat: no data parsed for {league} {season}")
    return data


def parse_league(data: dict, lg: League, season: int) -> tuple[list[MatchRecord], list[PlayerRecord]]:
    # per-team-per-match extras (ppda, deep, npxG) keyed by (team title, datetime)
    extras: dict[tuple[str, str], dict] = {}
    teams = data.get("teams") or {}
    for t in (teams.values() if isinstance(teams, dict) else teams):
        for h in t.get("history", []):
            ppda = h.get("ppda") or {}
            ppda_v = (float(ppda["att"]) / float(ppda["def"])) if ppda.get("def") else None
            extras[(t["title"], h["date"])] = {
                "npxg": h.get("npxG"), "deep": h.get("deep"), "ppda": ppda_v,
                "understat_xpts": h.get("xpts"),
            }

    matches = []
    for m in data.get("dates") or []:
        dtm = m.get("datetime", "")
        h, a = m["h"]["title"], m["a"]["title"]
        played = bool(m.get("isResult"))
        hs = {"xg": m["xG"]["h"]} if played and m.get("xG", {}).get("h") is not None else {}
        as_ = {"xg": m["xG"]["a"]} if played and m.get("xG", {}).get("a") is not None else {}
        if played:
            hs.update({k: v for k, v in extras.get((h, dtm), {}).items() if v is not None})
            as_.update({k: v for k, v in extras.get((a, dtm), {}).items() if v is not None})
        matches.append(MatchRecord(
            league=lg.key, season=season, date=dtm[:10], kickoff=dtm[11:16] or None,
            home=h, away=a,
            home_goals=int(m["goals"]["h"]) if played else None,
            away_goals=int(m["goals"]["a"]) if played else None,
            home_stats=hs, away_stats=as_, source_id=str(m.get("id")),
        ))

    players = []
    for p in data.get("players") or []:
        team = p.get("team_title", "")
        # players who moved mid-season are listed "Team A,Team B" - attribute to the latest
        team = team.split(",")[-1].strip()
        players.append(PlayerRecord(
            league=lg.key, season=season, player_id=str(p["id"]), player=p["player_name"],
            team=team, position=p.get("position"),
            stats={
                "games": p.get("games"), "minutes": p.get("time"), "goals": p.get("goals"),
                "npg": p.get("npg"), "xg": p.get("xG"), "npxg": p.get("npxG"),
                "assists": p.get("assists"), "xa": p.get("xA"), "shots": p.get("shots"),
                "key_passes": p.get("key_passes"), "xg_chain": p.get("xGChain"),
                "xg_buildup": p.get("xGBuildup"), "yellow": p.get("yellow_cards"),
                "red": p.get("red_cards"),
            },
        ))
    return matches, players


class Understat(Source):
    name = "understat"

    def supports(self, lg: League) -> bool:
        return bool(lg.understat)

    def _load(self, lg, season):
        return parse_league(load_league(lg.understat, season), lg, season)

    def matches(self, lg, season):
        return self._load(lg, season)[0]

    def players(self, lg, season):
        return self._load(lg, season)[1]

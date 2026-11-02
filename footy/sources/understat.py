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


SET_PIECE = {"FromCorner", "SetPiece", "DirectFreekick"}


def load_shots(match_id: str) -> dict:
    try:
        data = get(f"{BASE}/getMatchData/{match_id}", as_json=True, ttl=-1,
                   headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/match/{match_id}"})
        if isinstance(data, dict) and data.get("shots"):
            return data["shots"]
    except (FetchError, ValueError):
        pass
    html = get(f"{BASE}/match/{match_id}", ttl=-1)
    shots = _embedded(html, "shotsData")
    if not shots:
        raise FetchError(f"Understat: no shots for match {match_id}")
    return shots


def shot_metrics(shots: dict) -> tuple[dict, dict]:
    """Split each side's xG into open play / set piece / penalty, plus box-entry quality."""
    out = []
    for side in ("h", "a"):
        xs = shots.get(side) or []
        d = {"xg_open_play": 0.0, "xg_set_piece": 0.0, "xg_penalty": 0.0, "shots_xg30": 0.0}
        for s in xs:
            xg = float(s.get("xG") or 0)
            sit = s.get("situation")
            if sit == "Penalty":
                d["xg_penalty"] += xg
            elif sit in SET_PIECE:
                d["xg_set_piece"] += xg
            else:
                d["xg_open_play"] += xg
            if xg >= 0.3:
                d["shots_xg30"] += 1
        out.append({k: round(v, 3) for k, v in d.items()})
    return out[0], out[1]


class Understat(Source):
    name = "understat"

    def __init__(self):
        import os
        self.skip_ids: set[str] = set()   # matches that already have shot data
        self.max_shot_matches = int(os.environ.get("UNDERSTAT_MAX_MATCHES", "150"))

    def supports(self, lg: League) -> bool:
        return bool(lg.understat)

    def _load(self, lg, season):
        return parse_league(load_league(lg.understat, season), lg, season)

    def matches(self, lg, season):
        recs = self._load(lg, season)[0]
        fetched = 0
        for r in recs:
            if r.home_goals is None or not r.source_id or r.source_id in self.skip_ids:
                continue
            if fetched >= self.max_shot_matches:
                break
            try:
                h, a = shot_metrics(load_shots(r.source_id))
                r.home_stats.update(h)
                r.away_stats.update(a)
                fetched += 1
            except FetchError:
                continue
        return recs

    def players(self, lg, season):
        return self._load(lg, season)[1]

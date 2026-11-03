"""FotMob: match xG, xGOT and big chances (incl. Championship, League One/Two, Eredivisie,
Portugal, Belgium, Scotland), plus player big-chances-created leaderboards.

FotMob's private API changes from time to time and may require an `x-mas` request
header. If requests start failing with 401/403, open fotmob.com in a browser, copy the
`x-mas` header from any /api/ request in dev tools and set it as FOTMOB_XMAS in your
environment / .env. Parsing is deliberately defensive: it searches the JSON for the
structures it needs rather than relying on fixed paths, and falls back to the
`__NEXT_DATA__` blob embedded in the public HTML pages.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re

from ..config import League
from .base import MatchRecord, PlayerRecord, Source
from .http import FetchError, get

BASE = "https://www.fotmob.com"

# FotMob stat key -> our metric
STAT_KEYS = {
    "expected_goals": "xg",
    "expected_goals_non_penalty": "npxg",
    "expected_goals_on_target": "xgot",
    "expected_goals_open_play": "xg_open_play",
    "expected_goals_set_play": "xg_set_piece",
    "big_chance": "big_chances",
    "big_chance_missed_title": "big_chances_missed",
    "total_shots": "shots",
    "ShotsOnTarget": "sot",
    "shots_inside_box": "shots_in_box",
    "touches_opp_box": "touches_opp_box",
    "BallPossesion": "possession",
    "corners": "corners",
    "accurate_passes": "passes_completed",
}

PLAYER_STATS = {
    "expected_goals": "xg",
    "expected_assists": "xa",
    "big_chance_created": "big_chances_created",
    "big_chance_missed": "big_chances_missed",
    "goals": "goals",
    "goal_assist": "assists",
    "ontarget_scoring_att": "sot",
    "total_scoring_att": "shots",
}


def _headers() -> dict:
    h = {"Referer": BASE + "/"}
    if os.environ.get("FOTMOB_XMAS"):
        h["x-mas"] = os.environ["FOTMOB_XMAS"]
    return h


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _next_data(html: str):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"\s*(-?[\d.]+)", str(v))  # "54%" or "12 (40%)"
    return float(m.group(1)) if m else None


def extract_match_stats(payload) -> tuple[dict, dict]:
    """Find every {key: <stat>, stats: [home, away]} node and map known keys."""
    home, away = {}, {}
    for node in _walk(payload):
        key = node.get("key")
        st = node.get("stats")
        if isinstance(key, str) and key in STAT_KEYS and isinstance(st, list) and len(st) == 2:
            metric = STAT_KEYS[key]
            h, a = _num(st[0]), _num(st[1])
            if h is not None and metric not in home:
                home[metric] = h
            if a is not None and metric not in away:
                away[metric] = a
    return home, away


def extract_referee(payload) -> str | None:
    for node in _walk(payload):
        ref = node.get("Referee") or node.get("referee")
        if isinstance(ref, dict) and isinstance(ref.get("text"), str) and ref["text"].strip():
            return ref["text"].strip()
    return None


def extract_unavailable(payload) -> list[tuple]:
    """Injured/suspended players listed in FotMob's line-up section: (side, name, status, reason, return)."""
    out = []
    for node in _walk(payload):
        lineup = node.get("lineup")
        if not isinstance(lineup, dict):
            continue
        for side, key in (("h", "homeTeam"), ("a", "awayTeam")):
            team = lineup.get(key)
            if not isinstance(team, dict):
                continue
            for p in _walk(team.get("unavailable") or []):
                un = p.get("unavailability")
                if isinstance(un, dict) and p.get("name"):
                    kind = str(un.get("type") or "injury").lower()
                    status = "doubt" if "doubt" in kind or "question" in kind else "out"
                    out.append((side, str(p["name"]), status, kind, un.get("expectedReturn")))
        if out:
            break
    return out


def extract_league_matches(payload) -> list[dict]:
    for node in _walk(payload):
        if isinstance(node.get("allMatches"), list):
            return node["allMatches"]
    return []


def season_param(season: int) -> str:
    return f"{season}/{season + 1}"


def _utc_to_date(s: str) -> tuple[str, str | None]:
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.date().isoformat(), d.strftime("%H:%M")
    except (ValueError, AttributeError):
        return str(s)[:10], None


def _within_days(date: str, days: int) -> bool:
    try:
        d = dt.date.fromisoformat(date)
    except ValueError:
        return False
    return 0 <= (d - dt.date.today()).days <= days


class FotMob(Source):
    name = "fotmob"

    def __init__(self, max_matches: int | None = None):
        # fetching per-match detail is the slow bit; the pipeline passes ids it already has
        self.skip_ids: set[str] = set()
        self.max_matches = max_matches or int(os.environ.get("FOTMOB_MAX_MATCHES", "400"))

    def supports(self, lg: League) -> bool:
        return bool(lg.fotmob)

    def league_payload(self, lg: League, season: int):
        try:
            return get(f"{BASE}/api/leagues", params={"id": lg.fotmob, "season": season_param(season)},
                       headers=_headers(), as_json=True, ttl=3 * 3600)
        except (FetchError, ValueError):
            html = get(f"{BASE}/leagues/{lg.fotmob}/fixtures", params={"season": season_param(season)},
                       ttl=3 * 3600)
            nd = _next_data(html)
            if not nd:
                raise
            return nd

    def match_payload(self, match_id: str, finished: bool):
        ttl = -1 if finished else 3600
        try:
            return get(f"{BASE}/api/matchDetails", params={"matchId": match_id},
                       headers=_headers(), as_json=True, ttl=ttl)
        except (FetchError, ValueError):
            nd = _next_data(get(f"{BASE}/match/{match_id}", ttl=ttl))
            if not nd:
                raise
            return nd

    def _records(self, lg: League, season: int, with_stats: bool, with_news: bool = False) -> list[MatchRecord]:
        out, fetched = [], 0
        for m in extract_league_matches(self.league_payload(lg, season)):
            if not isinstance(m, dict):
                continue
            status = m.get("status") or {}
            finished = bool(status.get("finished")) and not status.get("cancelled")
            date, ko = _utc_to_date(status.get("utcTime") or m.get("utcTime", ""))
            home = m.get("home") if isinstance(m.get("home"), dict) else {}
            away = m.get("away") if isinstance(m.get("away"), dict) else {}
            if not isinstance(home.get("name"), str) or not isinstance(away.get("name"), str):
                continue
            rec = MatchRecord(league=lg.key, season=season, date=date, kickoff=ko,
                              home=home.get("name", ""), away=away.get("name", ""),
                              source_id=str(m.get("id")))
            if finished:
                hs, as_ = home.get("score"), away.get("score")
                if hs is None and status.get("scoreStr"):
                    parts = re.findall(r"\d+", status["scoreStr"])
                    hs, as_ = (parts + [None, None])[:2]
                rec.home_goals = int(hs) if hs is not None else None
                rec.away_goals = int(as_) if as_ is not None else None
                if with_stats and rec.source_id not in self.skip_ids and fetched < self.max_matches:
                    try:
                        payload = self.match_payload(rec.source_id, True)
                        rec.home_stats, rec.away_stats = extract_match_stats(payload)
                        rec.referee = extract_referee(payload)
                        fetched += 1
                    except FetchError:
                        pass
                    except Exception:  # unexpected page shape: skip this match, keep the season
                        fetched += 1
            elif with_news and _within_days(date, 8):
                # upcoming fixture: team news (injuries/suspensions) and referee appointment
                try:
                    payload = self.match_payload(rec.source_id, False)
                    rec.unavailable = extract_unavailable(payload)
                    rec.referee = extract_referee(payload)
                except Exception:
                    pass
            out.append(rec)
        return out

    def matches(self, lg, season):
        return self._records(lg, season, with_stats=True)

    def fixtures(self, lg):
        from ..config import current_season
        return [r for r in self._records(lg, current_season(), with_stats=False, with_news=True)
                if r.home_goals is None]

    def players(self, lg: League, season: int) -> list[PlayerRecord]:
        payload = self.league_payload(lg, season)
        blob = json.dumps(payload)
        m = re.search(rf"/stats/{lg.fotmob}/season/(\d+)", blob)
        if not m:
            return []
        sid = m.group(1)
        merged: dict[str, PlayerRecord] = {}
        for stat, col in PLAYER_STATS.items():
            try:
                data = get(f"https://data.fotmob.com/stats/{lg.fotmob}/season/{sid}/{stat}.json",
                           as_json=True, ttl=12 * 3600)
            except (FetchError, ValueError):
                continue
            for node in _walk(data):
                if isinstance(node.get("ParticipantName"), str) and "StatValue" in node:
                    pid = str(node.get("ParticiantId") or node.get("ParticipantId") or node["ParticipantName"])
                    rec = merged.setdefault(pid, PlayerRecord(
                        league=lg.key, season=season, player_id=pid,
                        player=node["ParticipantName"], team=node.get("TeamName", "")))
                    rec.stats[col] = _num(node["StatValue"])
                    if node.get("MinutesPlayed") is not None:
                        rec.stats["minutes"] = _num(node["MinutesPlayed"])
                    if node.get("MatchesPlayed") is not None:
                        rec.stats["games"] = _num(node["MatchesPlayed"])
        return list(merged.values())

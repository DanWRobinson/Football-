"""Official Fantasy Premier League API (players, prices, FPL xG/xA, fixtures)."""

from __future__ import annotations

from .http import get

BASE = "https://fantasy.premierleague.com/api"
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def fetch() -> tuple[dict, list]:
    boot = get(f"{BASE}/bootstrap-static/", as_json=True, ttl=2 * 3600)
    fixtures = get(f"{BASE}/fixtures/", as_json=True, ttl=2 * 3600)
    return boot, fixtures


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(boot: dict, fixtures: list):
    from ..names import canonical

    teams = {t["id"]: t for t in boot["teams"]}
    team_rows = [(t["id"], t["name"], t["short_name"], canonical(t["name"])) for t in boot["teams"]]
    players = []
    for e in boot["elements"]:
        t = teams.get(e["team"], {})
        players.append(dict(
            id=e["id"], web_name=e.get("web_name"), first_name=e.get("first_name"),
            second_name=e.get("second_name"), team_id=e["team"], team=canonical(t.get("name", "")),
            position=POSITIONS.get(e.get("element_type")), price=_f(e.get("now_cost", 0)) / 10,
            status=e.get("status"), chance_next=_f(e.get("chance_of_playing_next_round")),
            selected_pct=_f(e.get("selected_by_percent")), minutes=_f(e.get("minutes")),
            starts=_f(e.get("starts")), total_points=_f(e.get("total_points")),
            goals=_f(e.get("goals_scored")), assists=_f(e.get("assists")),
            clean_sheets=_f(e.get("clean_sheets")), goals_conceded=_f(e.get("goals_conceded")),
            saves=_f(e.get("saves")), bonus=_f(e.get("bonus")), bps=_f(e.get("bps")),
            yellow=_f(e.get("yellow_cards")), red=_f(e.get("red_cards")),
            xg=_f(e.get("expected_goals")), xa=_f(e.get("expected_assists")),
            xgi=_f(e.get("expected_goal_involvements")), xgc=_f(e.get("expected_goals_conceded")),
            defensive_contribution=_f(e.get("defensive_contribution")),
            penalties_order=_f(e.get("penalties_order")), form=_f(e.get("form")),
            news=e.get("news"),
        ))
    fx = []
    for f in fixtures:
        fx.append(dict(
            id=f["id"], event=f.get("event"), kickoff=f.get("kickoff_time"),
            finished=int(bool(f.get("finished"))), team_h_id=f["team_h"], team_a_id=f["team_a"],
            team_h=canonical(teams[f["team_h"]]["name"]), team_a=canonical(teams[f["team_a"]]["name"]),
            team_h_difficulty=f.get("team_h_difficulty"), team_a_difficulty=f.get("team_a_difficulty"),
            team_h_score=f.get("team_h_score"), team_a_score=f.get("team_a_score"),
        ))
    events = [(ev["id"], ev["name"], ev.get("deadline_time"), int(bool(ev.get("finished"))),
               int(bool(ev.get("is_current"))), int(bool(ev.get("is_next")))) for ev in boot["events"]]
    return team_rows, players, fx, events


def fetch_history(player_ids: list[int]) -> list[dict]:
    """Match-by-match rows for each player (one request per player; cached 12h)."""
    rows = []
    for pid in player_ids:
        try:
            data = get(f"{BASE}/element-summary/{pid}/", as_json=True, ttl=12 * 3600)
        except Exception:
            continue
        for h in data.get("history", []):
            rows.append(dict(
                player_id=pid, gw=h.get("round"), fixture=h.get("fixture"), opponent_id=h.get("opponent_team"),
                was_home=int(bool(h.get("was_home"))), kickoff=h.get("kickoff_time"),
                minutes=_f(h.get("minutes")), starts=_f(h.get("starts")), total_points=_f(h.get("total_points")),
                goals=_f(h.get("goals_scored")), assists=_f(h.get("assists")), xg=_f(h.get("expected_goals")),
                xa=_f(h.get("expected_assists")), bonus=_f(h.get("bonus")),
                defensive_contribution=_f(h.get("defensive_contribution")), saves=_f(h.get("saves")),
            ))
    return rows

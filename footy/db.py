"""SQLite storage.

Design: a narrow `matches` backbone (one row per fixture) plus a long-format
`team_match_stats` table so any source can contribute any metric (xG, big chances,
PPDA, possession, ...) without schema changes. When several sources supply the same
metric we keep all of them and pick by per-league priority at read time.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from . import config
from .names import canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id   TEXT PRIMARY KEY,
    league     TEXT NOT NULL,
    season     INTEGER NOT NULL,
    date       TEXT NOT NULL,          -- ISO yyyy-mm-dd
    kickoff    TEXT,                   -- HH:MM local/UK where known
    home       TEXT NOT NULL,          -- canonical key
    away       TEXT NOT NULL,
    home_name  TEXT,                   -- display name
    away_name  TEXT,
    home_goals INTEGER,
    away_goals INTEGER,
    status     TEXT NOT NULL DEFAULT 'fixture',   -- 'played' | 'fixture'
    updated_at TEXT,
    referee    TEXT
);
CREATE INDEX IF NOT EXISTS ix_matches_league ON matches(league, season, date);

CREATE TABLE IF NOT EXISTS team_match_stats (
    match_id TEXT NOT NULL,
    side     TEXT NOT NULL,            -- 'h' | 'a'
    metric   TEXT NOT NULL,            -- xg, npxg, shots, sot, big_chances, big_chances_missed, corners, ...
    value    REAL,
    source   TEXT NOT NULL,
    PRIMARY KEY (match_id, side, metric, source)
);
CREATE INDEX IF NOT EXISTS ix_tms_metric ON team_match_stats(metric);

CREATE TABLE IF NOT EXISTS odds (
    match_id  TEXT NOT NULL,
    market    TEXT NOT NULL,           -- 1X2 | OU2.5 | AH
    selection TEXT NOT NULL,           -- H D A | O U | H A
    book      TEXT NOT NULL,           -- avg max pinnacle b365 ... (suffix _close for closing)
    price     REAL,
    line      REAL,
    PRIMARY KEY (match_id, market, selection, book)
);

CREATE TABLE IF NOT EXISTS players (
    league   TEXT NOT NULL,
    season   INTEGER NOT NULL,
    source   TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player   TEXT,
    team     TEXT,                     -- canonical key
    team_name TEXT,
    position TEXT,
    games    REAL, minutes REAL,
    goals    REAL, npg REAL, xg REAL, npxg REAL,
    assists  REAL, xa REAL,
    shots    REAL, sot REAL, key_passes REAL,
    big_chances_created REAL, big_chances_missed REAL,
    xg_chain REAL, xg_buildup REAL,
    yellow   REAL, red REAL,
    updated_at TEXT,
    PRIMARY KEY (league, season, source, player_id)
);

CREATE TABLE IF NOT EXISTS fpl_players (
    id INTEGER PRIMARY KEY, web_name TEXT, first_name TEXT, second_name TEXT,
    team_id INTEGER, team TEXT, position TEXT, price REAL, status TEXT,
    chance_next REAL, selected_pct REAL, minutes REAL, starts REAL, total_points REAL,
    goals REAL, assists REAL, clean_sheets REAL, goals_conceded REAL, saves REAL,
    bonus REAL, bps REAL, yellow REAL, red REAL,
    xg REAL, xa REAL, xgi REAL, xgc REAL, defensive_contribution REAL,
    penalties_order REAL, form REAL, news TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS fpl_fixtures (
    id INTEGER PRIMARY KEY, event INTEGER, kickoff TEXT, finished INTEGER,
    team_h_id INTEGER, team_a_id INTEGER, team_h TEXT, team_a TEXT,
    team_h_difficulty INTEGER, team_a_difficulty INTEGER,
    team_h_score INTEGER, team_a_score INTEGER
);

CREATE TABLE IF NOT EXISTS fpl_teams (
    id INTEGER PRIMARY KEY, name TEXT, short_name TEXT, team TEXT
);

CREATE TABLE IF NOT EXISTS fpl_events (
    id INTEGER PRIMARY KEY, name TEXT, deadline TEXT, finished INTEGER,
    is_current INTEGER, is_next INTEGER
);

CREATE TABLE IF NOT EXISTS elo (
    date TEXT, club TEXT, country TEXT, level INTEGER, elo REAL,
    league TEXT, team TEXT,                 -- matched league/canonical team (NULL if unmatched)
    PRIMARY KEY (date, club, country, level)
);

CREATE TABLE IF NOT EXISTS fpl_history (
    player_id INTEGER, gw INTEGER, fixture INTEGER, opponent_id INTEGER, was_home INTEGER, kickoff TEXT,
    minutes REAL, starts REAL, total_points REAL, goals REAL, assists REAL, xg REAL, xa REAL,
    bonus REAL, defensive_contribution REAL, saves REAL,
    PRIMARY KEY (player_id, fixture)
);

CREATE TABLE IF NOT EXISTS availability (
    league TEXT, team TEXT, player TEXT, status TEXT,   -- status: out | doubt
    reason TEXT, expected_return TEXT, source TEXT, match_id TEXT, updated_at TEXT,
    PRIMARY KEY (league, team, player, source)
);

CREATE TABLE IF NOT EXISTS team_ext (
    source TEXT, team TEXT, ext_id TEXT, PRIMARY KEY (source, team)
);

CREATE TABLE IF NOT EXISTS source_map (
    source TEXT, source_id TEXT, match_id TEXT, has_stats INTEGER,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS source_log (
    source TEXT, league TEXT, season INTEGER, ok INTEGER, rows INTEGER,
    message TEXT, at TEXT
);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


MIGRATIONS = [("matches", "referee", "TEXT")]


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:  # databases created by older versions
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return con


@contextmanager
def session(path: Path | str | None = None):
    con = connect(path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def make_match_id(league: str, date: str, home: str, away: str) -> str:
    return f"{league}|{date}|{canonical(home)}|{canonical(away)}"


# ----------------------------------------------------------------------------- writes

def upsert_match(con, *, league, season, date, home, away, home_goals=None, away_goals=None,
                 kickoff=None, status=None, match_id=None) -> str:
    hk, ak = canonical(home), canonical(away)
    mid = match_id or f"{league}|{date}|{hk}|{ak}"
    if status is None:
        status = "played" if home_goals is not None and away_goals is not None else "fixture"
    con.execute(
        """INSERT INTO matches(match_id, league, season, date, kickoff, home, away, home_name, away_name,
                               home_goals, away_goals, status, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET
             kickoff=COALESCE(excluded.kickoff, matches.kickoff),
             home_goals=COALESCE(excluded.home_goals, matches.home_goals),
             away_goals=COALESCE(excluded.away_goals, matches.away_goals),
             status=CASE WHEN excluded.status='played' THEN 'played' ELSE matches.status END,
             updated_at=excluded.updated_at""",
        (mid, league, int(season), date, kickoff, hk, ak, str(home), str(away),
         _int(home_goals), _int(away_goals), status, now_iso()),
    )
    return mid


def put_stats(con, match_id: str, source: str, home: dict, away: dict) -> int:
    rows = []
    for side, d in (("h", home), ("a", away)):
        for metric, value in d.items():
            v = _float(value)
            if v is not None:
                rows.append((match_id, side, metric, v, source))
    con.executemany(
        "INSERT OR REPLACE INTO team_match_stats(match_id, side, metric, value, source) VALUES(?,?,?,?,?)",
        rows,
    )
    return len(rows)


def put_odds(con, match_id: str, rows: list[tuple]) -> None:
    """rows: (market, selection, book, price, line)"""
    con.executemany(
        "INSERT OR REPLACE INTO odds(match_id, market, selection, book, price, line) VALUES(?,?,?,?,?,?)",
        [(match_id, *r) for r in rows if _float(r[3]) and _float(r[3]) > 1.0],
    )


def log_source(con, source, league, season, ok, rows=0, message=""):
    con.execute("INSERT INTO source_log VALUES(?,?,?,?,?,?,?)",
                (source, league, season, int(bool(ok)), rows, str(message)[:500], now_iso()))


def _float(v):
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _int(v):
    f = _float(v)
    return None if f is None else int(round(f))


# ----------------------------------------------------------------------------- reads

def read_sql(con, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def matches(con, leagues: list[str] | None = None, seasons: list[int] | None = None,
            status: str | None = None) -> pd.DataFrame:
    q, p = "SELECT * FROM matches WHERE 1=1", []
    if leagues:
        q += f" AND league IN ({','.join('?' * len(leagues))})"
        p += leagues
    if seasons:
        q += f" AND season IN ({','.join('?' * len(seasons))})"
        p += [int(s) for s in seasons]
    if status:
        q += " AND status=?"
        p.append(status)
    df = read_sql(con, q + " ORDER BY date, match_id", p)
    df["date"] = pd.to_datetime(df["date"])
    return df


def stats_wide(con, match_ids: list[str] | None = None) -> pd.DataFrame:
    """Long stats -> one row per (match_id, side, source) with metric columns."""
    q = "SELECT * FROM team_match_stats"
    df = read_sql(con, q)
    if match_ids is not None:
        df = df[df.match_id.isin(set(match_ids))]
    if df.empty:
        return pd.DataFrame(columns=["match_id", "side", "source"])
    return df.pivot_table(index=["match_id", "side", "source"], columns="metric",
                          values="value", aggfunc="last").reset_index()


def odds_wide(con, match_ids: list[str] | None = None) -> pd.DataFrame:
    df = read_sql(con, "SELECT * FROM odds")
    if match_ids is not None:
        df = df[df.match_id.isin(set(match_ids))]
    if df.empty:
        return pd.DataFrame(columns=["match_id"])
    df["col"] = df.market + "_" + df.selection + "_" + df.book
    return df.pivot_table(index="match_id", columns="col", values="price", aggfunc="last").reset_index()

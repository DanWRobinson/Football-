"""Fetch from every configured source and merge into the database."""

from __future__ import annotations

import datetime as dt
import logging
import traceback

import pandas as pd

from . import config, db
from .names import best_match, canonical
from .sources import REGISTRY
from .sources import fpl as fpl_src
from .sources.base import MatchRecord, PlayerRecord

log = logging.getLogger("footy")


def _known_teams(con, league: str, season: int) -> list[str]:
    rows = con.execute("SELECT DISTINCT home FROM matches WHERE league=? AND season=? "
                       "UNION SELECT DISTINCT away FROM matches WHERE league=? AND season=?",
                       (league, season, league, season)).fetchall()
    return [r[0] for r in rows]


def _resolve(name: str, known: list[str]) -> str:
    if not known:
        return canonical(name)
    return best_match(name, known) or canonical(name)


def _find_match(con, league, season, home, away, date: str, window: int = 3) -> str | None:
    d = dt.date.fromisoformat(date)
    lo, hi = (d - dt.timedelta(days=window)).isoformat(), (d + dt.timedelta(days=window)).isoformat()
    row = con.execute(
        "SELECT match_id FROM matches WHERE league=? AND season=? AND home=? AND away=? "
        "AND date BETWEEN ? AND ? ORDER BY abs(julianday(date)-julianday(?)) LIMIT 1",
        (league, season, home, away, lo, hi, date)).fetchone()
    return row[0] if row else None


def store_matches(con, source: str, recs: list[MatchRecord], backbone: bool) -> dict:
    """Write records. Backbone sources create rows; others attach to existing rows
    (creating rows only for fixtures, or when the league-season has no backbone)."""
    stats = {"rows": 0, "attached": 0, "created": 0, "unmatched": []}
    known_cache: dict[tuple, list[str]] = {}
    for r in recs:
        if not r.home or not r.away or not r.date:
            continue
        kk = (r.league, r.season)
        if kk not in known_cache:
            known_cache[kk] = _known_teams(con, *kk)
        known = known_cache[kk]
        hk, ak = _resolve(r.home, known), _resolve(r.away, known)
        mid = _find_match(con, r.league, r.season, hk, ak, r.date, window=0 if backbone else 3)
        if mid is None:
            if not backbone and known and r.home_goals is not None:
                stats["unmatched"].append(f"{r.date} {r.home} v {r.away}")
                continue
            mid = db.upsert_match(con, league=r.league, season=r.season, date=r.date, kickoff=r.kickoff,
                                  home=hk, away=ak, home_goals=r.home_goals, away_goals=r.away_goals,
                                  match_id=f"{r.league}|{r.date}|{hk}|{ak}")
            con.execute("UPDATE matches SET home_name=?, away_name=? WHERE match_id=?", (r.home, r.away, mid))
            stats["created"] += 1
            for k in (hk, ak):
                if k not in known:
                    known.append(k)
        else:
            if r.home_goals is not None:
                con.execute("UPDATE matches SET home_goals=COALESCE(home_goals, ?), "
                            "away_goals=COALESCE(away_goals, ?), status='played' WHERE match_id=?",
                            (r.home_goals, r.away_goals, mid))
            stats["attached"] += 1
        if r.home_stats or r.away_stats:
            stats["rows"] += db.put_stats(con, mid, source, r.home_stats, r.away_stats)
        if r.odds:
            db.put_odds(con, mid, r.odds)
        if r.source_id:
            con.execute("INSERT OR REPLACE INTO source_map VALUES(?,?,?,?)",
                        (source, r.source_id, mid, int(bool(r.home_stats))))
    return stats


def store_players(con, source: str, recs: list[PlayerRecord]) -> int:
    known_cache: dict[tuple, list[str]] = {}
    cols = ["games", "minutes", "goals", "npg", "xg", "npxg", "assists", "xa", "shots", "sot",
            "key_passes", "big_chances_created", "big_chances_missed", "xg_chain", "xg_buildup",
            "yellow", "red"]
    n = 0
    for p in recs:
        kk = (p.league, p.season)
        if kk not in known_cache:
            known_cache[kk] = _known_teams(con, *kk)
        team = _resolve(p.team, known_cache[kk])
        vals = [db._float(p.stats.get(c)) for c in cols]
        con.execute(
            f"""INSERT INTO players(league, season, source, player_id, player, team, team_name, position,
                    {', '.join(cols)}, updated_at)
                VALUES({','.join('?' * (9 + len(cols)))})
                ON CONFLICT(league, season, source, player_id) DO UPDATE SET
                    player=excluded.player, team=excluded.team, team_name=excluded.team_name,
                    position=COALESCE(excluded.position, players.position),
                    {', '.join(f'{c}=COALESCE(excluded.{c}, players.{c})' for c in cols)},
                    updated_at=excluded.updated_at""",
            (p.league, p.season, source, p.player_id, p.player, team, p.team, p.position, *vals, db.now_iso()))
        n += 1
    return n


def cleanup(con) -> None:
    """Drop stale fixtures (postponed/re-dated) and fixtures superseded by results."""
    cutoff = (dt.date.today() - dt.timedelta(days=4)).isoformat()
    stale = [r[0] for r in con.execute(
        "SELECT match_id FROM matches WHERE status='fixture' AND date < ?", (cutoff,))]
    dup = [r[0] for r in con.execute(
        """SELECT f.match_id FROM matches f JOIN matches p
           ON f.league=p.league AND f.season=p.season AND f.home=p.home AND f.away=p.away
           WHERE f.status='fixture' AND p.status='played' AND f.match_id<>p.match_id""")]
    for mid in set(stale + dup):
        for t in ("team_match_stats", "odds"):
            con.execute(f"DELETE FROM {t} WHERE match_id=?", (mid,))
        con.execute("DELETE FROM matches WHERE match_id=?", (mid,))


def _skip_ids(con, source: str) -> set[str]:
    return {r[0] for r in con.execute("SELECT source_id FROM source_map WHERE source=? AND has_stats=1",
                                      (source,))}


def update(leagues: list[str], seasons: list[int], sources: list[str] | None = None,
           players: bool = True, echo=print) -> None:
    cfg = config.leagues()
    sources = sources or ["football_data", "understat", "fotmob", "api_football"]
    with db.session() as con:
        for key in leagues:
            lg = cfg[key]
            order = ["football_data"] + [s for s in lg.xg_sources if s in sources]
            order = [s for s in dict.fromkeys(order) if s in sources]
            for sname in order:
                src = REGISTRY[sname]()
                if not src.available():
                    echo(f"  [{key}] {sname}: skipped (set {src.requires_key} to enable)")
                    continue
                if not src.supports(lg):
                    continue
                if hasattr(src, "skip_ids"):
                    src.skip_ids = _skip_ids(con, sname)
                backbone = sname == "football_data"
                for season in seasons:
                    try:
                        recs = src.matches(lg, season)
                        st = store_matches(con, sname, recs, backbone=backbone)
                        msg = (f"{len(recs)} matches, {st['attached']} attached, {st['created']} new, "
                               f"{len(st['unmatched'])} unmatched")
                        if st["unmatched"]:
                            msg += " e.g. " + "; ".join(st["unmatched"][:3])
                        db.log_source(con, sname, key, season, True, len(recs), msg)
                        echo(f"  [{key} {config.season_label(season)}] {sname}: {msg}")
                        if players and sname != "football_data":
                            prs = src.players(lg, season)
                            if prs:
                                echo(f"  [{key} {config.season_label(season)}] {sname}: "
                                     f"{store_players(con, sname, prs)} players")
                    except Exception as e:  # keep going: one broken source shouldn't stop the rest
                        db.log_source(con, sname, key, season, False, 0, repr(e))
                        echo(f"  [{key} {config.season_label(season)}] {sname}: FAILED {e}")
                        log.debug(traceback.format_exc())
                    con.commit()
                if backbone:
                    try:
                        fx = src.fixtures(lg)
                        st = store_matches(con, sname, fx, backbone=True)
                        echo(f"  [{key}] {sname}: {len(fx)} upcoming fixtures with odds")
                    except Exception as e:
                        echo(f"  [{key}] {sname}: fixtures FAILED {e}")
                else:
                    try:
                        fx = src.fixtures(lg) if sname != "understat" else []
                        if fx:
                            store_matches(con, sname, fx, backbone=False)
                    except Exception:
                        pass
            con.commit()
        cleanup(con)


def update_fpl(echo=print) -> None:
    boot, fixtures = fpl_src.fetch()
    teams, players, fx, events = fpl_src.parse(boot, fixtures)
    with db.session() as con:
        # align FPL team keys with the PL teams in the match database
        known = _known_teams(con, "E0", config.current_season())
        remap = {t[3]: _resolve(t[1], known) for t in teams}
        con.execute("DELETE FROM fpl_teams")
        con.executemany("INSERT INTO fpl_teams VALUES(?,?,?,?)",
                        [(i, n, s, remap[k]) for i, n, s, k in teams])
        con.execute("DELETE FROM fpl_players")
        for p in players:
            p["team"] = remap.get(p["team"], p["team"])
            p["updated_at"] = db.now_iso()
            con.execute(f"INSERT INTO fpl_players({','.join(p)}) VALUES({','.join('?' * len(p))})",
                        list(p.values()))
        con.execute("DELETE FROM fpl_fixtures")
        for f in fx:
            f["team_h"], f["team_a"] = remap.get(f["team_h"], f["team_h"]), remap.get(f["team_a"], f["team_a"])
            con.execute(f"INSERT INTO fpl_fixtures({','.join(f)}) VALUES({','.join('?' * len(f))})",
                        list(f.values()))
        con.execute("DELETE FROM fpl_events")
        con.executemany("INSERT INTO fpl_events VALUES(?,?,?,?,?,?)", events)
    echo(f"  FPL: {len(players)} players, {len(fx)} fixtures")


def coverage(con) -> pd.DataFrame:
    """Per league-season: matches and how many have each key metric."""
    m = db.read_sql(con, "SELECT league, season, match_id, status FROM matches")
    if m.empty:
        return pd.DataFrame()
    s = db.read_sql(con, "SELECT match_id, metric, source FROM team_match_stats WHERE side='h'")
    out = m.groupby(["league", "season"]).agg(
        played=("status", lambda x: (x == "played").sum()),
        fixtures=("status", lambda x: (x == "fixture").sum())).reset_index()
    for metric in ("xg", "big_chances", "shots", "sot", "corners"):
        ids = s[s.metric == metric].drop_duplicates("match_id")
        ids = ids.merge(m[["match_id", "league", "season"]], on="match_id")
        cnt = ids.groupby(["league", "season"]).size().rename(f"with_{metric}")
        out = out.merge(cnt, on=["league", "season"], how="left")
        srcs = ids.groupby(["league", "season"]).source.agg(lambda x: ",".join(sorted(set(x))))
        if metric == "xg":
            out = out.merge(srcs.rename("xg_sources"), on=["league", "season"], how="left")
    o = db.read_sql(con, "SELECT DISTINCT match_id FROM odds").merge(m, on="match_id")
    out = out.merge(o.groupby(["league", "season"]).size().rename("with_odds"), on=["league", "season"], how="left")
    return out.fillna(0)

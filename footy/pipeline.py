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


def _domestic_teams(con, seasons: list[int]) -> list[str]:
    europe = [k for k, v in config.leagues().items() if v.is_europe]
    q = (f"SELECT DISTINCT home FROM matches WHERE season IN ({','.join('?' * len(seasons))}) "
         f"AND league NOT IN ({','.join('?' * len(europe))})") if europe else \
        f"SELECT DISTINCT home FROM matches WHERE season IN ({','.join('?' * len(seasons))})"
    return [r[0] for r in con.execute(q, [*seasons, *europe])]


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


def store_matches(con, source: str, recs: list[MatchRecord], backbone: bool,
                  extra_known: list[str] | None = None) -> dict:
    """Write records. Backbone sources create rows; others attach to existing rows
    (creating rows only for fixtures, or when the league-season has no backbone).

    extra_known: team keys from other competitions to resolve names against (used for
    European games so 'Sporting CP' maps onto the Primeira Liga team 'sp lisbon')."""
    stats = {"rows": 0, "attached": 0, "created": 0, "unmatched": []}
    known_cache: dict[tuple, list[str]] = {}
    for r in recs:
        if not r.home or not r.away or not r.date:
            continue
        kk = (r.league, r.season)
        if kk not in known_cache:
            known_cache[kk] = _known_teams(con, *kk)
            if extra_known:
                known_cache[kk] = list(dict.fromkeys(known_cache[kk] + list(extra_known)))
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
        if r.referee:
            con.execute("UPDATE matches SET referee=? WHERE match_id=?", (r.referee, mid))
        for side, player, status, reason, ret in r.unavailable:
            con.execute("INSERT OR REPLACE INTO availability VALUES(?,?,?,?,?,?,?,?,?)",
                        (r.league, hk if side == "h" else ak, player, status, reason, ret, source, mid,
                         db.now_iso()))
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


def recanonicalise(con) -> int:
    """Re-key teams stored under an old canonical name after the alias table improved.

    Only moves a row when its display name now maps to a team that already exists in the same
    league-season (so fuzzy-resolved names are left alone). Duplicate matches are merged: stats
    and odds move to the surviving row.
    """
    from .names import canonical as canon
    moved = 0
    teams = {}
    for lg, ssn, t in con.execute("SELECT league, season, home FROM matches UNION SELECT league, season, away FROM matches"):
        teams.setdefault((lg, ssn), set()).add(t)
    rows = con.execute("SELECT match_id, league, season, date, home, away, home_name, away_name, "
                       "home_goals, away_goals, status FROM matches").fetchall()
    for mid, lg, ssn, date, h, a, hn, an, hg, ag, st in rows:
        known = teams.get((lg, ssn), set())
        nh = canon(hn) if hn else h
        na = canon(an) if an else a
        nh = nh if nh != h and nh in known else h
        na = na if na != a and na in known else a
        if (nh, na) == (h, a):
            continue
        new_id = f"{lg}|{date}|{nh}|{na}"
        exists = con.execute("SELECT 1 FROM matches WHERE match_id=?", (new_id,)).fetchone()
        for t in ("team_match_stats", "odds", "source_map", "availability"):
            con.execute(f"UPDATE OR IGNORE {t} SET match_id=? WHERE match_id=?", (new_id, mid))
            con.execute(f"DELETE FROM {t} WHERE match_id=?", (mid,))
        if exists:
            con.execute("UPDATE matches SET home_goals=COALESCE(home_goals, ?), away_goals=COALESCE(away_goals, ?), "
                        "status=CASE WHEN ?='played' THEN 'played' ELSE status END WHERE match_id=?",
                        (hg, ag, st, new_id))
            con.execute("DELETE FROM matches WHERE match_id=?", (mid,))
        else:
            con.execute("UPDATE matches SET match_id=?, home=?, away=? WHERE match_id=?", (new_id, nh, na, mid))
        moved += 1
    return moved


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
    if source == "understat":  # league data is one request; skip matches whose shot data we already have
        return {r[0] for r in con.execute(
            """SELECT DISTINCT sm.source_id FROM source_map sm JOIN team_match_stats t
               ON t.match_id = sm.match_id AND t.source = 'understat' AND t.metric = 'xg_open_play'
               WHERE sm.source = 'understat'""")}
    return {r[0] for r in con.execute("SELECT source_id FROM source_map WHERE source=? AND has_stats=1",
                                      (source,))}


def update(leagues: list[str], seasons: list[int], sources: list[str] | None = None,
           players: bool = True, echo=print) -> None:
    import os
    import time
    t0 = time.time()
    # optional time budget (seconds): past it, the slow per-match sources are skipped for this run so
    # the job always finishes; football-data results/odds, FPL and ClubElo still update
    budget = float(os.environ.get("FOOTY_TIME_BUDGET", "0") or 0)
    cfg = config.leagues()
    sources = sources or ["football_data", "understat", "fotmob", "api_football"]
    with db.session() as con:
        # domestic leagues first, so European games can be linked to domestic teams
        leagues = sorted(leagues, key=lambda k: cfg[k].is_europe)
        for key in leagues:
            lg = cfg[key]
            order = ["football_data"] + [s for s in lg.xg_sources if s in sources]
            order = [s for s in dict.fromkeys(order) if s in sources]
            # the backbone is football-data where it exists, otherwise the first usable xG source
            primary = next((s for s in order if REGISTRY[s]().supports(lg) and REGISTRY[s]().available()), None)
            extra = _domestic_teams(con, [max(seasons) - 1, max(seasons)]) if lg.is_europe else None
            for sname in order:
                if budget and sname != "football_data" and time.time() - t0 > budget:
                    echo(f"  [{key}] {sname}: skipped - time budget used up (resumes next run)")
                    continue
                src = REGISTRY[sname]()
                if not src.available():
                    echo(f"  [{key}] {sname}: skipped (set {src.requires_key} to enable)")
                    continue
                if not src.supports(lg):
                    continue
                if hasattr(src, "skip_ids"):
                    src.skip_ids = _skip_ids(con, sname)
                backbone = sname == primary
                for season in seasons:
                    try:
                        recs = src.matches(lg, season)
                        st = store_matches(con, sname, recs, backbone=backbone, extra_known=extra)
                        msg = (f"{len(recs)} matches, {st['attached']} attached, {st['created']} new, "
                               f"{len(st['unmatched'])} unmatched")
                        if st["unmatched"]:
                            msg += " e.g. " + "; ".join(st["unmatched"][:3])
                        db.log_source(con, sname, key, season, True, len(recs), msg)
                        echo(f"  [{key} {config.season_label(season)}] {sname}: {msg} "
                             f"({(time.time() - t0) / 60:.0f} min)")
                        if players and sname != "football_data":
                            try:
                                prs = src.players(lg, season)
                                if prs:
                                    echo(f"  [{key} {config.season_label(season)}] {sname}: "
                                         f"{store_players(con, sname, prs)} players")
                            except Exception as e:
                                echo(f"  [{key} {config.season_label(season)}] {sname}: players FAILED {e}")
                    except Exception as e:  # keep going: one broken source shouldn't stop the rest
                        db.log_source(con, sname, key, season, False, 0, repr(e))
                        echo(f"  [{key} {config.season_label(season)}] {sname}: FAILED {e}")
                        log.debug(traceback.format_exc())
                    con.commit()
                if sname == "football_data":
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
                            con.execute("DELETE FROM availability WHERE league=? AND source=?", (key, sname))
                            store_matches(con, sname, fx, backbone=backbone, extra_known=extra)
                            n_out = sum(len(r.unavailable) for r in fx)
                            if n_out:
                                echo(f"  [{key}] {sname}: team news - {n_out} players out/doubtful")
                    except Exception as e:
                        echo(f"  [{key}] {sname}: fixtures/team news FAILED {e}")
            con.commit()
        n = recanonicalise(con)
        if n:
            echo(f"  merged/re-keyed {n} matches after team-name fixes")
        cleanup(con)
    try:
        update_elo(echo=echo)
    except Exception as e:
        echo(f"  ClubElo: FAILED {e}")


def update_elo(echo=print, date: dt.date | None = None) -> None:
    from .sources import clubelo
    df = clubelo.fetch(date)
    date_s = (date or dt.date.today()).isoformat()
    cfg = config.leagues()
    with db.session() as con:
        matched = 0
        rows = []
        for key, lg in cfg.items():
            code = clubelo.COUNTRY.get(lg.country)
            season = config.current_season()
            known = _known_teams(con, key, season)
            if not code or not known:
                continue
            cand = df[(df.country == code) & (df.level == lg.tier)]
            for _, r in cand.iterrows():
                team = best_match(r.club, known)
                rows.append((date_s, r.club, r.country, int(r.level), float(r.elo), key if team else None, team))
                matched += bool(team)
        # European competitions: clubs from outside our 13 leagues get matched against all of ClubElo
        domestic = {r[6] for r in rows if r[6]}
        cands = {canonical(c): (c, ctry, lvl, e) for c, ctry, lvl, e in
                 zip(df.club, df.country, df.level, df.elo)}
        for key, lg in cfg.items():
            if not lg.is_europe:
                continue
            for team in _known_teams(con, key, config.current_season()):
                if team in domestic:
                    continue
                hit = best_match(team, list(cands))
                if hit:
                    c, ctry, lvl, e = cands[hit]
                    rows.append((date_s, c, ctry, int(lvl) if pd.notna(lvl) else 0, float(e), key, team))
                    matched += 1
        con.execute("DELETE FROM elo WHERE date=?", (date_s,))
        con.executemany("INSERT OR REPLACE INTO elo VALUES(?,?,?,?,?,?,?)", rows)
    echo(f"  ClubElo: {matched} clubs matched to league teams")


def latest_elo(con) -> pd.DataFrame:
    return db.read_sql(con, "SELECT * FROM elo WHERE team IS NOT NULL AND date=(SELECT MAX(date) FROM elo)")


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
        # team news from FPL flags (Premier League)
        con.execute("DELETE FROM availability WHERE source='fpl'")
        n_news = 0
        for p in players:
            chance = p["chance_next"]
            status = None
            if p["status"] in ("i", "s", "u", "n") and (chance is None or chance <= 25):
                status = "out"
            elif chance is not None and chance <= 75:
                status = "doubt"
            if status:
                con.execute("INSERT OR REPLACE INTO availability VALUES(?,?,?,?,?,?,?,?,?)",
                            ("E0", p["team"], p["web_name"], status, p["news"] or p["status"], None, "fpl",
                             None, db.now_iso()))
                n_news += 1
    echo(f"  FPL: {len(players)} players, {len(fx)} fixtures, {n_news} flagged injured/doubtful")
    # match-by-match history for anyone who has played (recent minutes beat season averages)
    ids = [p["id"] for p in players if (p["minutes"] or 0) > 0]
    hist = fpl_src.fetch_history(ids)
    with db.session() as con:
        con.execute("DELETE FROM fpl_history")
        for h in hist:
            con.execute(f"INSERT OR REPLACE INTO fpl_history({','.join(h)}) VALUES({','.join('?' * len(h))})",
                        list(h.values()))
    echo(f"  FPL: match history for {len(ids)} players ({len(hist)} rows)")


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
    if "xg_sources" in out:
        out["xg_sources"] = out.xg_sources.fillna("")
    return out.fillna(0)

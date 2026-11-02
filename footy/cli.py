"""Command line interface: `python -m footy <command>`."""

from __future__ import annotations

import argparse
import logging
import math
import os
import subprocess
import sys

import pandas as pd

from . import config, db


def _seasons(args) -> list[int]:
    if args.seasons:
        return [int(s) for s in args.seasons]
    cur = config.current_season()
    return [cur - 1, cur]   # last season gives the ratings a base early in the campaign


def cmd_update(args):
    leagues = config.resolve_leagues(args.leagues)
    seasons = _seasons(args)
    from . import pipeline
    print(f"Updating {len(leagues)} leagues for seasons {', '.join(map(config.season_label, seasons))}")
    pipeline.update(leagues, seasons, sources=args.sources.split(",") if args.sources else None,
                    players=not args.no_players)
    if not args.no_fpl and ("E0" in leagues):
        try:
            pipeline.update_fpl()
        except Exception as e:
            print(f"  FPL: FAILED {e}")
    cmd_status(args)


def cmd_fpl(args):
    from . import pipeline
    pipeline.update_fpl()


def cmd_status(args):
    from .pipeline import coverage
    with db.session(args.db) as con:
        cov = coverage(con)
    if cov.empty:
        print("Database is empty. Run: python -m footy update")
        return
    cov["league"] = cov.league.map(lambda k: config.leagues()[k].name if k in config.leagues() else k)
    cov["season"] = cov.season.map(config.season_label)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(cov.to_string(index=False))


def cmd_doctor(args):
    """Probe each source for one league so you can see what works from your network."""
    from .sources import REGISTRY
    from .sources import fpl
    season = config.current_season()
    probes = [("football_data", "E0"), ("football_data", "EC"), ("understat", "E0"),
              ("fotmob", "E2"), ("api_football", "E1")]
    for sname, lk in probes:
        src = REGISTRY[sname]()
        if not src.available():
            print(f"{sname:14s} {lk:4s} SKIPPED (no {src.requires_key})")
            continue
        if hasattr(src, "max_matches"):
            src.max_matches = 2
        if hasattr(src, "max_stat_calls"):
            src.max_stat_calls = 2
        try:
            recs = src.matches(config.league(lk), season)
            with_xg = sum(1 for r in recs if "xg" in r.home_stats)
            with_bc = sum(1 for r in recs if "big_chances" in r.home_stats)
            print(f"{sname:14s} {lk:4s} OK  {len(recs)} matches, {with_xg} with xG, {with_bc} with big chances")
        except Exception as e:
            print(f"{sname:14s} {lk:4s} FAIL {e}")
    try:
        boot, fx = fpl.fetch()
        print(f"{'fpl':14s} E0   OK  {len(boot['elements'])} players, {len(fx)} fixtures")
    except Exception as e:
        print(f"{'fpl':14s} E0   FAIL {e}")


def _load(args, leagues=None):
    from .models.data import match_frame
    from .models.ratings import fit_all
    con = db.connect(args.db)
    cur = config.current_season()
    frame = match_frame(con, leagues, [cur - 2, cur - 1, cur])
    return con, frame, fit_all(frame, leagues or sorted(frame.league.unique()))


def cmd_ratings(args):
    leagues = config.resolve_leagues(args.leagues)
    con, frame, ratings = _load(args, leagues)
    from .models.data import team_names
    names = team_names(con)
    cur = config.current_season()
    for lk, r in ratings.items():
        t = r.table()
        t["team"] = t.team.map(lambda k: names.get(k, k))
        frame = frame.assign(home=frame.home.map(lambda k: names.get(k, k)),
                             away=frame.away.map(lambda k: names.get(k, k)))
        now = frame[(frame.league == lk) & (frame.season == cur)]
        t = t[t.team.isin(set(now.home) | set(now.away))]
        print(f"\n== {config.leagues().get(lk, lk).name if lk in config.leagues() else lk} "
              f"(home adv x{math.exp(r.home_adv):.2f}, rho {r.rho:.3f}, "
              f"{r.n_matches} matches) ==")
        print(t.round(2).to_string(index=False))


def cmd_predict(args):
    from .models.predict import predict_fixtures, value_bets
    leagues = config.resolve_leagues(args.leagues)
    con, frame, ratings = _load(args, leagues)
    pred = predict_fixtures(con, frame, ratings, days_ahead=args.days)
    if pred.empty:
        print("No upcoming fixtures in the database for those leagues.")
        return
    from .models.data import team_names
    names = team_names(con)
    pred["home_name"], pred["away_name"] = pred.home.map(names), pred.away.map(names)
    pred["home"], pred["away"] = pred.home_name, pred.away_name
    pred["date"] = pred.date.dt.strftime("%a %d %b")
    cols = ["date", "league", "home", "away", "xg_home", "xg_away", "H", "D", "A", "O2.5", "BTTS",
            "mkt_H", "mkt_D", "mkt_A"]
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(pred[[c for c in cols if c in pred]].round(2).to_string(index=False))
        vb = value_bets(pred, args.min_edge)
        print(f"\nValue (edge >= {args.min_edge:.0%}):")
        if not vb.empty:
            vb["date"] = pd.to_datetime(vb.date).dt.strftime("%a %d %b")
        print(vb.round(3).to_string(index=False) if not vb.empty else "  none")


def cmd_backtest(args):
    from .models.backtest import run
    leagues = config.resolve_leagues(args.leagues)
    season = int(args.season) if args.season else config.current_season() - 1
    con = db.connect(args.db)
    from .models.data import match_frame
    frame = match_frame(con, leagues, [season - 2, season - 1, season])
    for lk in leagues:
        bt, summ = run(con, frame, lk, season, args.min_edge)
        print(f"{lk} {config.season_label(season)}: " +
              (", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in summ.items())
               if summ else "not enough data"))


def cmd_demo(args):
    from .demo import build
    path = build(args.out)
    print(f"Demo database (fictional teams) written to {path}. Explore it with:\n"
          f"  FOOTY_DB={path} python -m footy dashboard")


def cmd_dashboard(args):
    env = dict(os.environ)
    if args.db:
        env["FOOTY_DB"] = str(args.db)
    app = config.ROOT / "app" / "dashboard.py"
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), *args.extra], env=env))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="footy", description=__doc__)
    ap.add_argument("--db", default=None, help="SQLite path (default data/footy.db or $FOOTY_DB)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def leagues_arg(p):
        p.add_argument("-l", "--leagues", default="all", help="all | england | big5 | E0,E1,SP1 ...")

    p = sub.add_parser("update", help="fetch data from all sources")
    leagues_arg(p)
    p.add_argument("-s", "--seasons", nargs="*", help="season start years, e.g. 2025 2026")
    p.add_argument("--sources", help="comma list: football_data,understat,fotmob,api_football")
    p.add_argument("--no-players", action="store_true")
    p.add_argument("--no-fpl", action="store_true")
    p.set_defaults(func=cmd_update)

    sub.add_parser("fpl", help="refresh FPL data only").set_defaults(func=cmd_fpl)
    sub.add_parser("status", help="data coverage per league").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="test which sources work from your network").set_defaults(func=cmd_doctor)

    p = sub.add_parser("ratings", help="team attack/defence ratings")
    leagues_arg(p)
    p.set_defaults(func=cmd_ratings)

    p = sub.add_parser("predict", help="upcoming fixture probabilities and value bets")
    leagues_arg(p)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--min-edge", type=float, default=0.05)
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("backtest", help="walk-forward backtest vs closing odds")
    leagues_arg(p)
    p.add_argument("--season", help="season start year (default: last completed)")
    p.add_argument("--min-edge", type=float, default=0.05)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("demo", help="build a demo database with fictional teams")
    p.add_argument("--out", default=str(config.DATA_DIR / "demo.db"))
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("dashboard", help="launch the Streamlit dashboard")
    p.add_argument("extra", nargs="*")
    p.set_defaults(func=cmd_dashboard)

    args = ap.parse_args(argv)
    if args.db:
        from pathlib import Path
        config.DB_PATH = Path(args.db)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()

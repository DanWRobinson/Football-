"""Command line interface: `python -m footy <command>`."""

from __future__ import annotations

import argparse
import logging
import math
import os
import subprocess
import sys
from pathlib import Path

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


def cmd_elo(args):
    from . import pipeline
    pipeline.update_elo()


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
        from .sources import clubelo
        e = clubelo.fetch()
        print(f"{'clubelo':14s} all  OK  {len(e)} clubs")
    except Exception as ex:
        print(f"{'clubelo':14s} all  FAIL {ex}")
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
    from .pipeline import latest_elo
    return con, frame, fit_all(frame, leagues or sorted(frame.league.unique()), elo=latest_elo(con))


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
            "mkt_H", "mkt_D", "mkt_A", "exp_cards"]
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(pred[[c for c in cols if c in pred]].round(2).to_string(index=False))
        vb = value_bets(pred, args.min_edge, book=args.book)
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


def cmd_report(args):
    from .report import build
    print(f"Report written to {build(args.out, args.days, args.min_edge)}")


def cmd_tickets(args):
    from .tickets import alerts, credits as cr, lfc
    cfg = cr.load(args.config)
    act = args.action
    if act == "check":
        r = alerts.check(cfg)
        print(f"{r['fixtures']} fixtures, {r['events']} sale dates found, {r['sent']} alerts sent"
              + (f", {len(r['errors'])} fetch problems" if r["errors"] else ""))
        if r["errors"] and not r["fixtures"]:
            print("Couldn't read the ticket listing: " + r["errors"][0])
    elif act == "watch":
        alerts.watch(args.every, cfg)
    elif act == "credits":
        rows = pd.DataFrame(cr.levels(cfg))
        if rows.empty:
            print(f"No credits set. Edit {cr.CONFIG}")
            return
        rows["games_needed"] = rows.games_needed.map(lambda v: "" if v is None or pd.isna(v) else int(v))
        print(rows.drop(columns="key").to_string(index=False))
        as_of = (cfg.get("credits") or {}).get("as_of")
        print(f"\nBalances are your My Account figures on {as_of} plus games logged since. "
              "My Account is always right if they differ.")
    elif act == "list":
        state = alerts.load_state()
        bal = cr.balances(cfg)
        now = pd.Timestamp.now(tz=lfc.LONDON)
        evs = [e for e in alerts.events_from_state(state) if e.when > now or args.all]
        if not evs:
            print("No upcoming sale dates saved. Run: python -m footy tickets check")
        for e, who in alerts._groups(evs):
            elig = {"eligible": "YOU QUALIFY", "not_eligible": "not enough credits",
                    "higher_stage": "you're in an earlier stage", "unknown": ""}[cr.eligibility(e, bal)]
            print(f"{alerts._fmt_when(e):26s} {e.title:42s} {who:38s} {elig}")
        for slug, f in state.get("fixtures", {}).items():
            if f.get("exchange") not in (None, "unknown"):
                print(f"Ticket Exchange {f['name']}: {f['exchange']}")
    elif act == "doctor":
        now = pd.Timestamp.now(tz=lfc.LONDON).to_pydatetime()
        target = args.url or lfc.LISTING_URL
        html = open(target, encoding="utf-8").read() if os.path.exists(target) else lfc.fetch(target, 0)
        fixtures = lfc.fixtures_from_listing(html)
        print(f"{len(fixtures)} fixture links")
        for f in fixtures[:30]:
            print(f"  {f.kickoff:%a %d %b %H:%M}  {f.name}  -> {f.url}")
        text = lfc.page_text(html)
        print(f"competition guess: {lfc.competition(text)[1]}, Ticket Exchange: {lfc.exchange_status(text)}")
        for e in lfc.sale_events(text, target, now):
            print(f"  {alerts._fmt_when(e):26s} {e.title}\n      \"{e.text[:160]}\"")
        if args.show_text:
            print("\n--- page text ---\n" + text)
    elif act == "test-alert":
        ok = alerts.notify("LFC ticket alerts are working", "You'll get sale reminders here.",
                           lfc.LISTING_URL)
        print("Sent to ntfy." if ok else "Not sent: set NTFY_TOPIC (see README, 'LFC tickets').")
    elif act == "ics":
        path = alerts.write_ics(alerts.events_from_state(alerts.load_state()), cfg, Path(args.out))
        print(f"Calendar written to {path}")


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
    sub.add_parser("elo", help="refresh ClubElo ratings only").set_defaults(func=cmd_elo)
    sub.add_parser("status", help="data coverage per league").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="test which sources work from your network").set_defaults(func=cmd_doctor)

    p = sub.add_parser("ratings", help="team attack/defence ratings")
    leagues_arg(p)
    p.set_defaults(func=cmd_ratings)

    p = sub.add_parser("predict", help="upcoming fixture probabilities and value bets")
    leagues_arg(p)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--min-edge", type=float, default=0.05)
    p.add_argument("--book", default="b365", choices=["b365", "best"],
                   help="price value bets at Bet365 (default) or best available")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("backtest", help="walk-forward backtest vs closing odds")
    leagues_arg(p)
    p.add_argument("--season", help="season start year (default: last completed)")
    p.add_argument("--min-edge", type=float, default=0.05)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("demo", help="build a demo database with fictional teams")
    p.add_argument("--out", default=str(config.DATA_DIR / "demo.db"))
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("report", help="build the static HTML report (for phone/iPad)")
    p.add_argument("--out", default="site/index.html")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--min-edge", type=float, default=0.05)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("tickets", help="Liverpool FC ticket sale alerts, credits, Ticket Exchange")
    p.add_argument("action", nargs="?", default="list",
                   choices=["check", "list", "credits", "watch", "doctor", "test-alert", "ics"],
                   help="check: scan + alert | list: saved sale dates | credits | watch: loop for "
                        "Ticket Exchange | doctor: show what a page parses to | test-alert | ics")
    p.add_argument("--config", default=None, help="tickets YAML (default config/tickets.yaml)")
    p.add_argument("--every", type=int, default=180, help="watch: seconds between checks (min 120)")
    p.add_argument("--url", help="doctor: page URL or saved .html file (default the availability page)")
    p.add_argument("--show-text", action="store_true", help="doctor: print the extracted page text")
    p.add_argument("--all", action="store_true", help="list: include past dates")
    p.add_argument("--out", default="site/lfc-tickets.ics", help="ics: output path")
    p.set_defaults(func=cmd_tickets)

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

"""Synthetic demo database with FICTIONAL teams and players.

Lets you explore the dashboard and test the models without any network access.
Never mix this with real data: it writes to data/demo.db by default.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from . import db

PREFIX = ["Ashford", "Brackwater", "Calder", "Dunmore", "Eastcliff", "Fernley", "Glenholm", "Harrowgate",
          "Ivybridge", "Kestrel", "Larchmont", "Marlow", "Northgate", "Oakhurst", "Penrose", "Queensbury",
          "Redfield", "Stonebury", "Thornby", "Underwood", "Valemount", "Westbrook", "Yarrow", "Zennor"]
SUFFIX = ["Albion", "Town", "City", "United", "Rovers", "Athletic", "Wanderers", "County"]
FIRST = ["Alex", "Ben", "Callum", "Dev", "Eli", "Finn", "Gabe", "Harry", "Isaac", "Jay", "Kai", "Leo",
         "Max", "Noah", "Owen", "Rio", "Sam", "Theo", "Will", "Zac"]
LAST = ["Archer", "Bishop", "Carter", "Dawson", "Ellis", "Foster", "Grant", "Hughes", "Irwin", "Jones",
        "Knight", "Lowe", "Mason", "Norris", "Okafor", "Price", "Reid", "Shaw", "Turner", "Walsh"]


def _teams(rng, n, offset):
    return [f"{PREFIX[(i + offset) % len(PREFIX)]} {SUFFIX[(i * 3 + offset) % len(SUFFIX)]}" for i in range(n)]


def build(path="data/demo.db", seed=7, today: dt.date | None = None) -> str:
    rng = np.random.default_rng(seed)
    today = today or dt.date.today()
    season = today.year if today.month >= 7 else today.year - 1
    con = db.connect(path)
    for t in ("matches", "team_match_stats", "odds", "players", "fpl_players", "fpl_fixtures",
              "fpl_teams", "fpl_events", "source_map", "source_log"):
        con.execute(f"DELETE FROM {t}")

    for league, n, offset, real_xg in (("E0", 20, 0, True), ("E1", 24, 4, False)):
        teams = _teams(rng, n, offset)
        att = rng.normal(0, 0.22, n)
        dfn = rng.normal(0, 0.22, n)
        mu, ha = np.log(1.35), 0.22
        for ssn in (season - 1, season):
            start = dt.date(ssn, 8, 9)
            pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
            rng.shuffle(pairs)
            per_round = n // 2
            for k, (i, j) in enumerate(pairs):
                date = start + dt.timedelta(days=7 * (k // per_round) + int(rng.integers(0, 3)))
                lh = np.exp(mu + ha + att[i] + dfn[j])
                la = np.exp(mu + att[j] + dfn[i])
                played = date < today
                if not played and date > today + dt.timedelta(days=45):
                    continue
                hg = ag = None
                hs, as_ = {}, {}
                if played:
                    # chance-level simulation: shots with varying quality -> xG and goals
                    for side, lam, d in (("h", lh, hs), ("a", la, as_)):
                        nshots = rng.poisson(lam / 0.11)
                        q = rng.beta(0.9, 7.0, nshots)
                        q *= lam / max(q.sum(), 1e-6) if nshots else 1
                        q = np.clip(q, 0.01, 0.95)
                        goals = int((rng.random(nshots) < q).sum())
                        sot = int(min(nshots, goals + rng.binomial(max(nshots - goals, 0), 0.25)))
                        d.update(shots=nshots, sot=sot, corners=int(rng.poisson(lam * 3.6)),
                                 yellow=int(rng.poisson(1.8)), red=int(rng.random() < 0.05),
                                 big_chances=int((q > 0.35).sum()), big_chances_missed=int((q > 0.35).sum() - min(goals, (q > 0.35).sum())))
                        d["_goals"] = goals
                        d["_xg"] = round(float(q.sum()), 2)
                    hg, ag = hs.pop("_goals"), as_.pop("_goals")
                    hx, ax = hs.pop("_xg"), as_.pop("_xg")
                mid = db.upsert_match(con, league=league, season=ssn, date=date.isoformat(), home=teams[i],
                                      away=teams[j], home_goals=hg, away_goals=ag, kickoff="15:00")
                if played:
                    db.put_stats(con, mid, "football_data", hs, as_)
                    if real_xg:
                        db.put_stats(con, mid, "demo", {"xg": hx}, {"xg": ax})
                # bookmaker odds: true probs + noise + 5% margin
                from .models.probs import markets, score_matrix
                mk = markets(score_matrix(lh, la, -0.05))
                noise = rng.normal(0, 0.03, 3)
                p = np.clip(np.array([mk["H"], mk["D"], mk["A"]]) * np.exp(noise), 0.02, None)
                p /= p.sum()
                o = 1 / (p * 1.05)
                po = np.array([mk["O2.5"], mk["U2.5"]]) * np.exp(rng.normal(0, 0.03, 2))
                po /= po.sum()
                oo = 1 / (po * 1.06)
                rows = [("1X2", s, "avg", float(x), None) for s, x in zip("HDA", o)]
                rows += [("1X2", s, "max", float(x * 1.04), None) for s, x in zip("HDA", o)]
                rows += [("1X2", s, "pinnacle", float(x * 1.025), None) for s, x in zip("HDA", o)]
                rows += [("OU2.5", s, "avg", float(x), 2.5) for s, x in zip("OU", oo)]
                rows += [("OU2.5", s, "max", float(x * 1.03), 2.5) for s, x in zip("OU", oo)]
                db.put_odds(con, mid, rows)

        # player season stats for current season
        from .names import canonical
        for ti, t in enumerate(teams):
            team_lam = np.exp(mu + att[ti] + ha / 2)
            shares = rng.dirichlet(np.ones(11) * 0.6)
            for k in range(14):
                pos = "GK" if k == 0 else "DEF" if k < 5 else "MID" if k < 10 else "FWD"
                games = int(rng.integers(3, 12))
                mins = games * int(rng.integers(55, 90))
                share = shares[k % 11] * (0.2 if pos in ("GK", "DEF") else 1.0)
                xg = team_lam * games * share * 0.9
                db_row = dict(games=games, minutes=mins, xg=round(xg, 2), npxg=round(xg * 0.9, 2),
                              goals=int(rng.poisson(xg)), xa=round(team_lam * games * share * 0.6, 2),
                              assists=int(rng.poisson(team_lam * games * share * 0.6)),
                              shots=int(rng.poisson(xg / 0.11 + 1)), key_passes=int(rng.poisson(games * share * 8)),
                              big_chances_created=int(rng.poisson(games * share * 1.2)))
                db_row["sot"] = int(db_row["shots"] * 0.36)
                name = f"{FIRST[(ti + k) % 20]} {LAST[(ti * 7 + k) % 20]}"
                con.execute(
                    f"INSERT OR REPLACE INTO players(league, season, source, player_id, player, team, team_name, position, "
                    f"{', '.join(db_row)}, updated_at) VALUES({','.join('?' * (8 + len(db_row)))}, ?)",
                    (league, season, "demo", f"{league}-{ti}-{k}", name, canonical(t), t, pos, *db_row.values(),
                     db.now_iso()))
                if league == "E0":
                    con.execute(
                        "INSERT INTO fpl_players(id, web_name, team_id, team, position, price, status, chance_next, "
                        "selected_pct, minutes, starts, total_points, goals, assists, saves, bonus, xg, xa, "
                        "defensive_contribution, yellow, red) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ti * 100 + k, name.split()[1], ti + 1, canonical(t), pos,
                         round(4.0 + 8 * share + rng.random(), 1), "a", None, float(rng.uniform(0.1, 30)),
                         mins, games, games * 4, db_row["goals"], db_row["assists"],
                         games * 3 if pos == "GK" else 0, int(games * share * 3), db_row["xg"], db_row["xa"],
                         games * (9 if pos == "DEF" else 7 if pos == "MID" else 2), 1, 0))
        if league == "E0":
            con.executemany("INSERT INTO fpl_teams VALUES(?,?,?,?)",
                            [(i + 1, t, t[:3].upper(), canonical(t)) for i, t in enumerate(teams)])
            fx = db.read_sql(con, "SELECT * FROM matches WHERE league='E0' AND season=? ORDER BY date", (season,))
            gw_of = {}
            for i, d in enumerate(sorted(fx.date.unique())):
                gw_of[d] = (dt.date.fromisoformat(d) - dt.date(season, 8, 9)).days // 7 + 1
            idx = {canonical(t): i + 1 for i, t in enumerate(teams)}
            for k, r in fx.iterrows():
                con.execute("INSERT INTO fpl_fixtures(id, event, kickoff, finished, team_h_id, team_a_id, team_h, team_a) "
                            "VALUES(?,?,?,?,?,?,?,?)", (k + 1, gw_of[r.date], r.date, int(r.status == "played"),
                                                        idx[r.home], idx[r.away], r.home, r.away))
            max_gw = max(gw_of.values())
            nxt = (today - dt.date(season, 8, 9)).days // 7 + 1
            con.executemany("INSERT INTO fpl_events VALUES(?,?,?,?,?,?)",
                            [(g, f"Gameweek {g}", None, int(g < nxt), int(g == nxt - 1), int(g == nxt))
                             for g in range(1, max(max_gw, nxt) + 1)])
    con.commit()
    con.close()
    return path

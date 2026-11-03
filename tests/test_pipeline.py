import json
from pathlib import Path

from footy import config, db, pipeline
from footy.models.data import match_frame
from footy.sources import football_data, understat

FX = Path(__file__).parent / "fixtures"


def test_sources_merge_onto_backbone(tmp_path):
    con = db.connect(tmp_path / "t.db")
    lg = config.league("E0")
    fd = football_data.parse_csv((FX / "E0_sample.csv").read_text(), lg, 2025)
    st = pipeline.store_matches(con, "football_data", fd, backbone=True)
    assert st["created"] == 3

    us, players = understat.parse_league(json.loads((FX / "understat_league.json").read_text()), lg, 2025)
    st = pipeline.store_matches(con, "understat", us, backbone=False)
    # 3 played matches attach despite different names (Newcastle United, Nottingham Forest);
    # the unplayed future fixture is created
    assert st["attached"] == 3 and st["unmatched"] == [] and st["created"] == 1
    pipeline.store_players(con, "understat", players)
    con.commit()

    f = match_frame(con, ["E0"])
    played = f[f.status == "played"]
    assert len(played) == 3
    assert (played.xg_source == "understat").all()
    villa = played[played.home == "aston villa"].iloc[0]
    assert villa.away == "newcastle" and villa.h_xg == 0.42 and villa.h_shots == 3
    # odds survived
    od = db.odds_wide(con)
    assert "1X2_H_pinnacle_close" in od.columns
    p = db.read_sql(con, "SELECT * FROM players")
    assert p.team.iloc[0] == "liverpool"


def test_cleanup_removes_superseded_fixtures(tmp_path):
    con = db.connect(tmp_path / "t.db")
    db.upsert_match(con, league="E0", season=2025, date="2025-09-01", home="A", away="B")
    db.upsert_match(con, league="E0", season=2025, date="2025-09-03", home="A", away="B", home_goals=1, away_goals=0)
    pipeline.cleanup(con)
    rows = con.execute("SELECT status FROM matches").fetchall()
    assert [r[0] for r in rows] == ["played"]


def test_recanonicalise_merges_old_keys(tmp_path):
    con = db.connect(tmp_path / "t.db")
    # old run stored the club under a stale key; new run stored it under the fixed alias
    con.execute("INSERT INTO matches(match_id, league, season, date, home, away, home_name, away_name, "
                "home_goals, away_goals, status) VALUES('B1|2026-08-09|anderlecht|raal la louviere', 'B1', 2026, "
                "'2026-08-09', 'anderlecht', 'raal la louviere', 'Anderlecht', 'RAAL La Louviere', 2, 0, 'played')")
    db.put_stats(con, "B1|2026-08-09|anderlecht|raal la louviere", "football_data", {"shots": 12}, {"shots": 5})
    db.upsert_match(con, league="B1", season=2026, date="2026-08-09", home="Anderlecht", away="La Louviere",
                    home_goals=2, away_goals=0)
    db.upsert_match(con, league="B1", season=2026, date="2026-08-16", home="La Louviere", away="Gent")
    assert pipeline.recanonicalise(con) == 1
    rows = con.execute("SELECT match_id FROM matches ORDER BY match_id").fetchall()
    assert [r[0] for r in rows] == ["B1|2026-08-09|anderlecht|la louviere", "B1|2026-08-16|la louviere|gent"]
    assert con.execute("SELECT COUNT(*) FROM team_match_stats WHERE match_id LIKE '%|la louviere'").fetchone()[0] == 2

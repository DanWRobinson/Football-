import pandas as pd
import pytest

from footy import db, track


def test_settle_rules():
    assert track._settle("H", 2, 1) == 1 and track._settle("D", 2, 1) == -1
    assert track._settle("O2.5", 2, 1) == 1 and track._settle("U2.5", 2, 1) == -1
    assert track._settle("AH-0.75", 1, 0) == 0.5          # half won
    assert track._settle("AH-1", 1, 0) is None             # refund
    assert track._settle("AHA+0.25", 0, 0) == 0.5          # away +0.25 on a draw: half won
    assert track._settle("AH-0.25", 1, 1) == -0.5          # half lost


def test_scores_only_pre_kickoff_snapshots(tmp_path):
    con = db.connect(tmp_path / "t.db")
    con.executescript(track.SCHEMA)
    db.upsert_match(con, league="E0", season=2026, date="2026-11-08", home="Arsenal", away="Hull",
                    home_goals=2, away_goals=0, kickoff="15:00")
    mid = "E0|2026-11-08|arsenal|hull"
    # a pre-match snapshot, and one taken after kick-off that must be ignored
    for run_at, ph in (("2026-11-08T05:17", 0.70), ("2026-11-08T16:47", 0.99)):
        con.execute("INSERT INTO pred_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_at, mid, "E0", "2026-11-08", "15:00", ph, 0.2, 0.1, 0.6, 0.65, 0.22, 0.13, 0.58))
        con.execute("INSERT INTO value_log VALUES(?,?,?,?,?,?,?,?,?)",
                    (run_at, mid, "H", "best", 1.70, ph, 0.67, 0.65, "Sky Bet"))
    s = track.score(con)
    assert s["games"] == 1
    assert s["model_ll"] == pytest.approx(-__import__("math").log(0.70))     # the 05:17 snapshot, not 16:47
    b = s["bets_adj"]
    assert len(b) == 1 and b.pnl.iloc[0] == pytest.approx(0.70)              # won at 1.70
    assert "Live track record" in track.section(con)

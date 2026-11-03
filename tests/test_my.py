import pandas as pd

from footy import my


def test_best_xi_is_legal_and_maximal():
    rows = [(1, "GK", 4), (2, "GK", 1)] + [(10 + i, "DEF", v) for i, v in enumerate([6, 5, 1, 1, 1])] + \
           [(20 + i, "MID", v) for i, v in enumerate([5, 5, 5, 5, 0.5])] + [(30 + i, "FWD", v) for i, v in enumerate([4, 3, 0.2])]
    sq = pd.DataFrame(rows, columns=["id", "position", "xPts_next"])
    xi = my.best_xi(sq)
    pos = sq[sq.id.isin(xi)].position.value_counts()
    assert len(xi) == 11 and pos["GK"] == 1 and 3 <= pos["DEF"] <= 5 and 2 <= pos["MID"] <= 5 and 1 <= pos["FWD"] <= 3
    assert {1, 10, 11, 20, 21, 22, 23, 30, 31}.issubset(xi)   # obvious picks
    assert 2 not in xi and 32 not in xi


def test_config_loads():
    cfg = my.load()
    assert my.favourite() == "liverpool" and len(cfg["fpl"]["squad"]) == 15


def test_uses_screenshot_until_deadline_then_fpl(tmp_path, monkeypatch):
    from footy import db
    con = db.connect(tmp_path / "t.db")
    con.execute("INSERT INTO fpl_teams VALUES(1,'Arsenal','ARS','arsenal')")
    con.execute("INSERT INTO fpl_players(id, web_name, team_id, team, position) VALUES(4,'Gabriel',1,'arsenal','DEF')")
    con.execute("INSERT INTO fpl_players(id, web_name, team_id, team, position) VALUES(5,'Saka',1,'arsenal','MID')")
    monkeypatch.setattr(my, "load", lambda: {"fpl": {"entry_id": 1, "bank": 2.1, "gameweek": 10,
                                                      "squad": [{"name": "Gabriel"}]}})
    calls = []
    monkeypatch.setattr(my, "_from_entry", lambda con, e: (calls.append(e), (pd.DataFrame([{"id": 5}]), 0.5))[1])
    con.execute("INSERT INTO fpl_events VALUES(9,'GW9',NULL,1,1,0)")      # GW10 deadline not passed yet
    sq, bank = my.squad(con)
    assert list(sq.id) == [4] and bank == 2.1 and not calls
    con.execute("INSERT INTO fpl_events VALUES(10,'GW10',NULL,0,1,0)")    # GW10 now current
    con.execute("UPDATE fpl_events SET is_current=0 WHERE id=9")
    sq, bank = my.squad(con)
    assert list(sq.id) == [5] and bank == 0.5 and calls == [1]

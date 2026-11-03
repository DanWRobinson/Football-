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

import pandas as pd
import pytest

from footy import fpl_extra as fx


def _xp(n_per_pos=8):
    rows, pid = [], 1
    for pos, base in (("GK", 3), ("DEF", 4), ("MID", 5), ("FWD", 5.5)):
        for i in range(n_per_pos):
            rows.append(dict(id=pid, player=f"{pos}{i}", team=f"T{pid % 10}", position=pos, price=4.0 + i * 0.8,
                             status="a", start_rate=1.0, chance_next=None, GW10=base + i * 0.5, GW11=base + i * 0.4))
            pid += 1
    return pd.DataFrame(rows)


def test_chips_available_by_half():
    used = [("wildcard", 8)]
    avail, end = fx.chips_available(used, 10)
    assert end == 19 and set(avail) == {"freehit", "bboost", "3xc"}
    avail2, end2 = fx.chips_available(used, 21)
    assert end2 == 38 and "wildcard" in avail2            # second-half set is fresh


def test_free_transfer_estimate():
    b = {"history": {"chips": [{"name": "wildcard", "event": 8}], "current": [
        {"event": e, "event_transfers": t} for e, t in [(1, 5), (2, 0), (3, 0), (4, 1), (5, 0), (6, 0), (7, 0), (8, 9), (9, 1)]]}}
    # GW2 1->2, GW3 ->3, GW4 used1 ->3, GW5 4, GW6 5, GW7 5 (cap), GW8 wildcard keeps ->5, GW9 used1 ->5
    assert fx.free_transfers(b, 10) == 5
    assert fx.free_transfers(None, 10) is None


def test_free_hit_respects_rules():
    xp = _xp()
    pts = fx.free_hit_xi(xp, "GW10", budget=100)
    assert pts > 0
    assert fx.free_hit_xi(xp, "GW10", budget=80) < pts     # tighter budget cannot score more
    assert fx.free_hit_xi(xp, "GW10", budget=60) != fx.free_hit_xi(xp, "GW10", budget=60)   # impossible budget -> nan


def test_transfer_plan_prefers_free_moves():
    xp = _xp()
    squad = list(xp.groupby("position").head(2).id) + list(xp[xp.position == "DEF"].iloc[2:5].id) + \
        list(xp[xp.position == "MID"].iloc[2:5].id) + [xp[xp.position == "FWD"].iloc[2].id]
    plan = fx.transfer_plan(xp, squad, bank=5.0, horizon_col="GW10", ft=1)
    names = [o["plan"] for o in plan["options"]]
    assert names[0] == "Roll the transfer" and "1 transfer" in names
    assert plan["best"] in names


def test_rivals_threats_and_differentials():
    xp = _xp()
    mine = [1, 9, 17, 25]
    b = {"leagues": [{"name": "Mates", "results": [
        {"entry": 1, "entry_name": "Me", "player_name": "Dan", "rank": 2, "total": 500, "event_total": 50},
        {"entry": 2, "entry_name": "R1", "player_name": "A", "rank": 1, "total": 520, "event_total": 60},
        {"entry": 3, "entry_name": "R2", "player_name": "B", "rank": 3, "total": 480, "event_total": 40}],
        "picks": {"2": {"picks": [{"element": 30, "is_captain": True}, {"element": 9}]},
                  "3": {"picks": [{"element": 30, "is_captain": True}, {"element": 2}]}}}]}
    out = fx.rivals(b, xp, mine, "GW10", 1)[0]
    assert out["captains"][0]["player"] == "FWD5" and out["captains"][0]["share"] == 1.0
    assert [t["player"] for t in out["threats"]] == ["FWD5", "GK1"]   # owned by >= half of rivals, not by you
    assert {d["player"] for d in out["diffs"]} == {"GK0", "MID0", "FWD0"}   # DEF0 (id 9) half-owned
    assert list(out["table"].gap) == [0, 20, -20]

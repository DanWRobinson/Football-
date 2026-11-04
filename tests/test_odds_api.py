import datetime as dt
import json
from pathlib import Path

import numpy as np

from footy import db, pipeline
from footy.models import predict
from footy.models.probs import score_matrix
from footy.sources import odds_api

FX = Path(__file__).parent / "fixtures"


def test_parse_real_payload():
    ev = odds_api.parse(json.loads((FX / "odds_api_epl.json").read_text()))
    e = ev[0]
    assert (e["home"], e["away"], e["date"]) == ("Everton", "Coventry City", "2026-11-06")
    sels = {(m, s) for m, s, b, p, l in e["rows"] if b == "oa:paddypower"}
    assert {("1X2", "H"), ("1X2", "D"), ("1X2", "A")} <= sels
    assert all(p > 1 for *_, p, _ in e["rows"])


def test_spreads_stored_as_home_line():
    events = [{"home_team": "A", "away_team": "B", "commence_time": "2026-11-07T15:00:00Z", "bookmakers": [
        {"key": "williamhill", "markets": [{"key": "spreads", "outcomes": [
            {"name": "A", "price": 1.9, "point": -0.75}, {"name": "B", "price": 1.95, "point": 0.75}]}]}]}]
    rows = odds_api.parse(events)[0]["rows"]
    assert ("AH-0.75", "H", "oa:williamhill", 1.9, -0.75) in rows
    assert ("AH-0.75", "A", "oa:williamhill", 1.95, -0.75) in rows   # keyed and stored by the home line


def test_junk_exchange_prices_ignored():
    prices = {("1X2", "H"): {"oa:betfair_ex_uk": 1.15, "oa:skybet": 1.33, "oa:smarkets": 1.36},
              ("1X2", "D"): {"oa:betfair_ex_uk": 1.16, "oa:skybet": 4.0, "oa:smarkets": 5.0},
              ("1X2", "A"): {"oa:betfair_ex_uk": 1.14, "oa:skybet": 6.0, "oa:smarkets": 9.5}}
    p, src = predict._sharp_probs(prices, "1X2", ["H", "D", "A"])
    assert src == "exchange" and 0.7 < p[0] < 0.73       # smarkets used, betfair junk ignored
    best, who = predict._best(prices, "1X2", "A", p[2])
    assert who == "Smarkets" and 9.0 < best < 9.5         # net of 2% commission, not the junk 1.14


def test_quarter_line_prob_matches_page_logic():
    M = score_matrix(1.6, 1.1, -0.05)
    assert abs(predict.ah_prob(M, -0.5) - (M[np.tril_indices(11, -1)].sum())) < 1e-9   # -0.5 = win
    assert predict.ah_prob(M, 0) > predict.ah_prob(M, -0.25) > predict.ah_prob(M, -0.5)
    assert abs(predict.ah_prob(M, -0.25, "H") + predict.ah_prob(M, 0.25, "A") - 1) < 1e-9


def test_budget_plan(tmp_path):
    con = db.connect(tmp_path / "t.db")
    today = dt.date.today().isoformat()
    for lg in ("E0", "SP1", "E3"):
        db.upsert_match(con, league=lg, season=2026, date=today, home=f"{lg}a", away=f"{lg}b")
    plan = pipeline.odds_api_plan(con, ["E0", "SP1", "E3"], remaining=490)
    assert [p[0] for p in plan][:1] == ["E0"] and all(p[0] != "E3" for p in plan)   # League Two isn't covered
    assert pipeline.odds_api_plan(con, ["E0"], remaining=11) == []                    # keep the reserve

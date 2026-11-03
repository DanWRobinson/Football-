import datetime as dt

import numpy as np
import pandas as pd
import pytest

from footy import db
from footy.demo import build
from footy.models import backtest, form, fpl, predict, props
from footy.models.data import calibrate_proxy, match_frame
from footy.models.probs import markets, score_matrix, xpts_from_xg
from footy.models.ratings import fit, fit_all, team_priors
from footy.models.value import devig, edge, kelly


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("d") / "demo.db"
    build(str(path), today=dt.date(2026, 11, 2))
    con = db.connect(path)
    frame = match_frame(con)
    return con, frame


def test_score_matrix_markets_consistent():
    M = score_matrix(1.6, 1.1, -0.05)
    mk = markets(M)
    assert abs(M.sum() - 1) < 1e-9
    assert abs(mk["H"] + mk["D"] + mk["A"] - 1) < 1e-9
    assert abs(mk["O2.5"] + mk["U2.5"] - 1) < 1e-9
    assert mk["H"] > mk["A"]
    assert abs(mk["H_exp"] - 1.6) < 0.05


def test_xpts():
    h, a = xpts_from_xg(2.5, 0.3)
    assert h > 2.3 and a < 0.4


def test_devig_power_and_multiplicative():
    prices = [1.9, 3.6, 4.2]
    for m in ("power", "multiplicative"):
        p = devig(prices, m)
        assert abs(p.sum() - 1) < 1e-9
        assert np.all(p < 1 / np.array(prices))


def test_edge_kelly():
    assert edge(0.5, 2.2) == pytest.approx(0.1)
    assert kelly(0.5, 2.2, fraction=1, cap=1) == pytest.approx(0.1 / 1.2)
    assert kelly(0.3, 2.0) == 0


def test_ratings_recover_known_strengths():
    rng = np.random.default_rng(0)
    teams = [f"t{i}" for i in range(16)]
    att = dict(zip(teams, np.linspace(-0.4, 0.4, 16)))
    dfn = dict(zip(teams, np.linspace(0.3, -0.3, 16)))
    rows, d0 = [], dt.date(2025, 8, 1)
    for rnd in range(4):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                lh = np.exp(0.25 + 0.2 + att[h] + dfn[a])
                la = np.exp(0.25 + att[a] + dfn[h])
                rows.append(dict(match_id=f"{rnd}{h}{a}", league="X", season=2025,
                                 date=pd.Timestamp(d0 + dt.timedelta(days=rnd)), home=h, away=a,
                                 hg=rng.poisson(lh), ag=rng.poisson(la), h_xg=lh * rng.lognormal(0, 0.2),
                                 a_xg=la * rng.lognormal(0, 0.2), xg_kind="real", status="played"))
    frame = pd.DataFrame(rows)
    r = fit(frame, "X", as_of=pd.Timestamp("2025-08-10"), settings={"ridge": 0.5, "half_life_days": 10000})
    est = np.array([r.att[t] for t in teams])
    assert np.corrcoef(est, list(att.values()))[0, 1] > 0.95
    est_d = np.array([r.dfn[t] for t in teams])
    assert np.corrcoef(est_d, list(dfn.values()))[0, 1] > 0.95
    assert 0.1 < r.home_adv < 0.3


def test_team_priors():
    hist = pd.DataFrame([
        ("E0", 2025, "a"), ("E0", 2025, "b"), ("E1", 2025, "c"), ("E0", 2026, "a"), ("E0", 2026, "c"),
        ("E1", 2026, "b"), ("E1", 2026, "z")], columns=["league", "season", "team"])
    assert team_priors(hist, "E0", 2026, ["a", "c"]) == {"a": "", "c": "promoted"}
    assert team_priors(hist, "E1", 2026, ["b", "z"]) == {"b": "relegated", "z": "promoted"}


def test_proxy_calibration_on_demo(demo):
    _, frame = demo
    a, b = calibrate_proxy(frame)
    assert 0.1 < a < 0.5 and 0 <= b < 0.15
    e1 = frame[(frame.league == "E1") & (frame.status == "played")]
    assert (e1.xg_kind == "proxy").all()
    assert (frame[(frame.league == "E0") & (frame.status == "played")].xg_kind == "real").all()


def test_tables_predictions_fpl_props(demo):
    con, frame = demo
    cur = frame[(frame.league == "E0") & (frame.season == 2026)]
    t = form.league_table(cur)
    assert len(t) == 20 and t.Pts.sum() == pytest.approx(
        (cur.status == "played").sum() * 3 - ((cur.hg == cur.ag) & (cur.status == "played")).sum())
    assert t.xPts.notna().all()

    ratings = fit_all(frame, ["E0", "E1"], as_of=pd.Timestamp("2026-11-02"))
    assert set(ratings) == {"E0", "E1"}
    xp, tick = fpl.expected_points(con, ratings["E0"], horizon=3)
    assert len(xp) == 20 * 14 and xp["xPts_3gw"].between(0, 40).all()
    assert not tick.empty

    pt = props.player_table(con, ["E0"], [2026])
    assert (pt.xg_p90 >= 0).all()

    bt, summ = backtest.run(con, frame, "E0", 2025)
    assert summ["matches"] > 200 and 0.8 < summ["model_logloss"] < 1.2


def test_static_report(tmp_path, monkeypatch):
    from footy import config, report
    path = tmp_path / "demo.db"
    build(str(path), today=dt.date.today())
    monkeypatch.setattr(config, "DB_PATH", path)
    out = report.build(tmp_path / "site" / "index.html")
    page = out.read_text()
    for tab in ("Value", "Match", "Fixtures", "Tables", "Regression", "FPL", "Players", "Data"):
        assert f">{tab}</button>" in page
    assert 'id="footy-data"' in page and 'data-f="q"' in page and "mxL" in page
    assert "Premier League" in page and "<table>" in page


def test_prop_rates_use_domestic_and_shrink_small_samples():
    import pandas as pd
    from footy.models.props import player_rates
    rows = pd.DataFrame([
        # a full-back with a lucky Champions League sample but an ordinary domestic record
        dict(league="UCL", team="psv", player="Dest", position=None, minutes=270, games=3, goals=1, xg=0.3, xa=0.2, shots=3.9, sot=3.0),
        dict(league="N1", team="psv", player="Dest", position=None, minutes=880, games=10, goals=2, xg=0.9, xa=0.8, shots=9.0, sot=2.0),
        # a striker with lots of shots
        dict(league="E0", team="man city", player="Haaland", position=None, minutes=810, games=9, goals=9, xg=6.6, xa=0.8, shots=34.2, sot=16.2),
    ])
    r = player_rates(rows).set_index("player")
    assert r.loc["Dest", "rate_source"] == "domestic"
    assert r.loc["Dest", "sot_p90"] < 0.3                     # not the 1.0/90 the UCL sample implied
    assert r.loc["Haaland", "shots_p90"] > 2.5 and 0.35 < r.loc["Haaland", "sot_share"] <= 0.55

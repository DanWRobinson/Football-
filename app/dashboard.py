"""footy dashboard - run with `python -m footy dashboard`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from footy import config, db  # noqa: E402
from footy.models import backtest, form, fpl, predict, props  # noqa: E402
from footy.models.data import match_frame, team_names  # noqa: E402
from footy.models.probs import markets, score_matrix  # noqa: E402
from footy.models.ratings import fit_all  # noqa: E402
from footy.models.value import devig, edge, kelly  # noqa: E402
from footy.models import referees  # noqa: E402
from footy.pipeline import coverage, latest_elo  # noqa: E402

from footy.my import app_name  # noqa: E402

st.set_page_config(page_title=app_name(), page_icon="⚽", layout="wide")

DB_PATH = Path(os.environ.get("FOOTY_DB", config.DB_PATH))
LEAGUES = config.leagues()


def lg_name(k):
    return LEAGUES[k].name if k in LEAGUES else k


def db_version():
    return DB_PATH.stat().st_mtime if DB_PATH.exists() else 0


@st.cache_data(show_spinner="Loading matches…")
def load_frame(_v, seasons):
    con = db.connect(DB_PATH)
    f = match_frame(con, None, list(seasons))
    names = team_names(con)
    return f, names


@st.cache_resource(show_spinner="Fitting team ratings…")
def load_ratings(_v, seasons, half_life, xg_weight, ridge):
    f, _ = load_frame(_v, seasons)
    s = {"half_life_days": half_life, "xg_weight": xg_weight, "ridge": ridge}
    return fit_all(f, sorted(f.league.unique()), settings=s, elo=latest_elo(db.connect(DB_PATH)))


def pct(x):
    return "" if pd.isna(x) else f"{x:.0%}"


# ----------------------------------------------------------------------------- sidebar
if not DB_PATH.exists():
    st.error(f"No database at `{DB_PATH}`. Run `python -m footy update` (or `python -m footy demo`).")
    st.stop()

v = db_version()
cur = config.current_season()
seasons = (cur - 2, cur - 1, cur)
frame, names = load_frame(v, seasons)
if frame.empty:
    st.warning("Database has no matches yet. Run `python -m footy update`.")
    st.stop()


def nm(k):
    return names.get(k, k)


with st.sidebar:
    st.title(f"⚽ {app_name()}")
    if "demo" in DB_PATH.name:
        st.warning("DEMO DATA - fictional teams")
    page = st.radio("Page", ["Data coverage", "League tables", "Team form", "Predictions & value",
                             "Match explorer", "FPL planner", "Players & props", "Referees & cards", "Backtest"])
    avail = [k for k in LEAGUES if k in set(frame.league)] or sorted(frame.league.unique())
    with st.expander("Model settings"):
        half_life = st.slider("Time-decay half life (days)", 60, 720, config.model_settings().get("half_life_days", 240), 30)
        xg_weight = st.slider("Weight on xG vs goals", 0.0, 1.0, config.model_settings().get("xg_weight", 0.7), 0.05)
        ridge = st.slider("Shrinkage", 0.0, 10.0, float(config.model_settings().get("ridge", 2.0)), 0.5)
    st.caption(f"DB: `{DB_PATH.name}` · proxy xG = {frame.attrs.get('proxy_coef', (0, 0))[0]:.3f}×SoT + "
               f"{frame.attrs.get('proxy_coef', (0, 0))[1]:.3f}×off-target")

ratings = load_ratings(v, seasons, half_life, xg_weight, ridge)


def league_picker(key="lg", multi=False, default=None):
    if multi:
        return st.multiselect("Leagues", avail, default=default or avail, format_func=lg_name, key=key)
    return st.selectbox("League", avail, format_func=lg_name, key=key)


def season_picker(key="ssn"):
    opts = sorted(frame.season.unique(), reverse=True)
    return st.selectbox("Season", opts, format_func=config.season_label, key=key)


# ----------------------------------------------------------------------------- pages
if page == "Data coverage":
    st.header("Data coverage")
    st.write("What each league has in the database. `xg_sources` shows where real xG came from; "
             "leagues without it use a shots-based proxy xG calibrated on leagues that have both.")
    con = db.connect(DB_PATH)
    cov = coverage(con)
    cov.insert(0, "name", cov.league.map(lg_name))
    cov["season"] = cov.season.map(config.season_label)
    st.dataframe(cov, hide_index=True, use_container_width=True)
    log = db.read_sql(con, "SELECT * FROM source_log ORDER BY at DESC LIMIT 200")
    if not log.empty:
        with st.expander("Recent source log"):
            st.dataframe(log, hide_index=True, use_container_width=True)

elif page == "League tables":
    st.header("League table vs expected")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        lk = league_picker()
    with c2:
        ssn = season_picker()
    with c3:
        venue = st.selectbox("Venue", ["all", "home", "away"])
    with c4:
        last_n = st.selectbox("Games", [None, 5, 10], format_func=lambda x: "All" if x is None else f"Last {x}")
    f = frame[(frame.league == lk) & (frame.season == ssn)]
    if LEAGUES.get(lk) and LEAGUES[lk].is_europe and lk in ratings:
        ls = getattr(ratings[lk], "league_strength", None)
        if ls is not None and not ls.empty:
            st.subheader("League strength (from ClubElo + European results)")
            st.caption("Log goal-strength offset vs the average club in European competition: +0.20 means a "
                       "league-average team scores ~10% more and concedes ~10% fewer than a European-average one.")
            st.dataframe(ls.assign(league=ls.league.map(lg_name)).round(3), hide_index=True)
        st.subheader("Model ratings")
        rtab = ratings[lk].table()
        rtab["team"] = rtab.team.map(nm)
        st.dataframe(rtab.round(2), hide_index=True, use_container_width=True)
        st.subheader("Results table")
    t = form.league_table(f, venue if venue != "all" else None, last_n)
    if t.empty:
        st.info("No played matches yet.")
    else:
        t["team"] = t.team.map(nm)
        share = t.real_xg_share.mean()
        st.caption(("Real xG" if share > 0.9 else "Proxy xG (shots-based)" if share < 0.1 else
                    f"Mixed real/proxy xG ({share:.0%} real)") + " · xPts = points expected from each match's xG")
        cols = ["Pos", "team", "P", "W", "D", "L", "GF", "GA", "GD", "Pts", "xG", "xGA", "xGD", "xPts", "xPos",
                "Pts-xPts", "G-xG", "GA-xGA", "xG/90", "xGA/90", "BC/90", "BCA/90", "Shots", "SoT", "Corners"]
        show = t[[c for c in cols if c in t]].copy()
        show = show.dropna(axis=1, how="all")
        st.dataframe(show.round(2), hide_index=True, use_container_width=True, height=min(40 + 35 * len(show), 900))

        c1, c2 = st.columns(2)
        with c1:
            fig = px.scatter(t, x="xG/90", y="xGA/90", text="team", title="Chance quality: created vs conceded")
            fig.update_yaxes(autorange="reversed")
            fig.update_traces(textposition="top center")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            fl = form.regression_flags(t, min_games=5)
            st.subheader("Regression candidates")
            st.dataframe(fl[["team", "Pts", "xPts", "luck_score", "G-xG", "GA-xGA", "verdict"]].round(2),
                         hide_index=True, use_container_width=True)

elif page == "Team form":
    st.header("Team form")
    c1, c2, c3 = st.columns(3)
    with c1:
        lk = league_picker()
    f = frame[frame.league == lk]
    teams = sorted(set(f[f.season == f.season.max()].home), key=nm)
    with c2:
        team = st.selectbox("Team", teams, format_func=nm)
    with c3:
        window = st.slider("Rolling window", 3, 12, 6)
    r = form.rolling(f, team, window)
    if r.empty:
        st.info("No matches.")
    else:
        fig = go.Figure()
        fig.add_scatter(x=r.date, y=r.xg_for_roll, name="xG for", line=dict(width=3))
        fig.add_scatter(x=r.date, y=r.xg_against_roll, name="xG against", line=dict(width=3))
        fig.add_scatter(x=r.date, y=r.gf_roll, name="Goals for", line=dict(dash="dot"))
        fig.add_scatter(x=r.date, y=r.ga_roll, name="Goals against", line=dict(dash="dot"))
        fig.update_layout(title=f"{nm(team)} - {window}-game rolling averages", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        last = r.tail(10).copy()
        last["opponent"] = last.opponent.map(nm)
        cols = ["date", "venue", "opponent", "gf", "ga", "xg_for", "xg_against", "xpts", "big_chances_for",
                "big_chances_against", "shots_for", "shots_against", "sot_for", "corners_for", "xg_kind"]
        st.subheader("Last 10")
        st.dataframe(last[[c for c in cols if c in last]].dropna(axis=1, how="all").assign(date=last.date.dt.date).round(2).iloc[::-1],
                     hide_index=True, use_container_width=True)
        rt = ratings.get(lk)
        if rt and team in rt.att:
            c1, c2, c3 = st.columns(3)
            tbl = rt.table()
            row = tbl[tbl.team == team].iloc[0]
            c1.metric("Model attack (exp. goals v avg)", f"{row.exp_gf:.2f}")
            c2.metric("Model defence (exp. conceded v avg)", f"{row.exp_ga:.2f}")
            c3.metric("Rank by exp. GD", f"{tbl.index[tbl.team == team][0] + 1}")

elif page == "Predictions & value":
    st.header("Upcoming fixtures & value")
    c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
    with c1:
        lks = league_picker(multi=True, key="lgs")
    with c2:
        days = st.number_input("Days ahead", 1, 30, 7)
    with c3:
        min_edge = st.number_input("Min edge", 0.0, 0.5, 0.05, 0.01)
    with c4:
        max_price = st.number_input("Max odds", 1.5, 50.0, 8.0, 0.5)
    with c5:
        book = st.selectbox("Price at", ["b365", "best"],
                            format_func=lambda b: "Bet365" if b == "b365" else "Best available")
    con = db.connect(DB_PATH)
    pred = predict.predict_fixtures(con, frame, ratings, days, lks)
    if pred.empty:
        st.info("No upcoming fixtures in range. football-data publishes fixtures+odds for the coming week; "
                "run `python -m footy update` closer to matchday.")
    else:
        pred["home_name"] = pred.home.map(nm)
        pred["away_name"] = pred.away.map(nm)
        vb = predict.value_bets(pred, min_edge, max_price, book=book)
        st.subheader(f"Value bets ({len(vb)})")
        st.caption("Edge = model probability × best price − 1. Market probabilities are margin-removed "
                   "(Pinnacle if available, else average). Large edges usually mean the model is missing "
                   "something (injuries, rotation) - check the Backtest page for how the model does in each league.")
        if not vb.empty:
            vb["league"] = vb.league.map(lg_name)
            st.dataframe(vb.style.format({"model_p": "{:.1%}", "market_p": "{:.1%}", "fair_odds": "{:.2f}",
                                          "price": "{:.2f}", "edge": "{:+.1%}", "stake_pct": "{:.1%}"}),
                         hide_index=True, use_container_width=True)
        st.subheader("All fixtures")
        show = pred.assign(league=pred.league.map(lg_name), match=pred.home_name + " v " + pred.away_name)
        cols = ["date", "kickoff", "league", "match", "xg_home", "xg_away", "H", "D", "A", "mkt_H", "mkt_D",
                "mkt_A", "b365_H", "b365_D", "b365_A", "O2.5", "mkt_O2.5", "BTTS", "H_CS", "A_CS", "exp_cards",
                "referee", "news_home", "news_away"]
        show = show[[c for c in cols if c in show]].dropna(axis=1, how="all")
        fmt = {c: "{:.0%}" for c in show.columns if c in ("H", "D", "A", "mkt_H", "mkt_D", "mkt_A", "O2.5",
                                                         "mkt_O2.5", "BTTS", "H_CS", "A_CS")}
        fmt.update({c: "{:.2f}" for c in ("xg_home", "xg_away", "b365_H", "b365_D", "b365_A") if c in show})
        fmt.update({"exp_cards": "{:.1f}"} if "exp_cards" in show else {})
        st.dataframe(show.style.format(fmt, na_rep=""), hide_index=True, use_container_width=True)
        st.caption("Expected goals already include team-news adjustments (injuries/suspensions from FPL and "
                   "FotMob). news_* columns show who's out and the effect.")

elif page == "Match explorer":
    st.header("Match explorer")
    c1, c2, c3 = st.columns(3)
    with c1:
        lk = league_picker()
    rt = ratings.get(lk)
    cur_teams = sorted(set(frame[(frame.league == lk) & (frame.season == frame.season.max())].home), key=nm)
    with c2:
        h = st.selectbox("Home", cur_teams, format_func=nm)
    with c3:
        a = st.selectbox("Away", [t for t in cur_teams if t != h], format_func=nm)
    if rt:
        lh, la = rt.lambdas(h, a)
        c1, c2 = st.columns([1, 1])
        with c1:
            lh = st.number_input("Home expected goals (override for team news)", 0.1, 5.0, round(lh, 2), 0.05)
        with c2:
            la = st.number_input("Away expected goals", 0.1, 5.0, round(la, 2), 0.05)
        M = score_matrix(lh, la, rt.rho)
        mk = markets(M)
        cols = st.columns(6)
        for col, (k, lab) in zip(cols, [("H", nm(h)), ("D", "Draw"), ("A", nm(a)), ("O2.5", "Over 2.5"),
                                        ("BTTS", "BTTS"), ("H_CS", f"{nm(h)} clean sheet")]):
            col.metric(lab, f"{mk[k]:.0%}")
            col.caption(f"fair odds {1 / mk[k]:.2f}")
        c1, c2 = st.columns([3, 2])
        with c1:
            fig = px.imshow(M[:6, :6], text_auto=".1%", labels=dict(x=f"{nm(a)} goals", y=f"{nm(h)} goals"),
                            color_continuous_scale="Blues", title="Scoreline probabilities")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.subheader("Check a price")
            mkt = st.selectbox("Market", ["H", "D", "A", "O1.5", "O2.5", "O3.5", "U2.5", "BTTS", "H_CS", "A_CS",
                                          "AH_H-1.5", "AH_H-0.5", "AH_H+0.5", "AH_H+1.5"])
            price = st.number_input("Bookmaker odds (decimal)", 1.01, 100.0, 2.0, 0.01)
            e = edge(mk[mkt], price)
            st.metric("Model probability", f"{mk[mkt]:.1%}", f"edge {e:+.1%}")
            st.write(f"Quarter-Kelly stake: **{kelly(mk[mkt], price):.1%}** of bankroll")
            st.write("Most likely scores: " + ", ".join(f"{s} ({p:.0%})" for s, p in mk["top_scores"]))
        h2h = frame[(frame.league == lk) & (((frame.home == h) & (frame.away == a)) |
                                            ((frame.home == a) & (frame.away == h))) & (frame.status == "played")]
        if not h2h.empty:
            st.subheader("Recent meetings")
            hh = h2h[["date", "home", "away", "hg", "ag", "h_xg", "a_xg"]].copy()
            hh["home"], hh["away"] = hh.home.map(nm), hh.away.map(nm)
            st.dataframe(hh.sort_values("date", ascending=False).assign(date=lambda d: d.date.dt.date).round(2), hide_index=True)

elif page == "FPL planner":
    st.header("FPL planner")
    con = db.connect(DB_PATH)
    rt = ratings.get("E0")
    if rt is None:
        st.info("Needs Premier League data (`python -m footy update -l E0`).")
        st.stop()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        horizon = st.slider("Gameweeks ahead", 1, 8, 5)
    with c2:
        pos = st.multiselect("Position", ["GK", "DEF", "MID", "FWD"], default=["GK", "DEF", "MID", "FWD"])
    with c3:
        max_price = st.slider("Max price (£m)", 3.5, 15.5, 15.5, 0.5)
    with c4:
        min_start = st.slider("Min start rate", 0.0, 1.0, 0.5, 0.05)
    xp, tick = fpl.expected_points(con, rt, horizon)
    if xp.empty:
        st.info("No FPL data. Run `python -m footy fpl`.")
        st.stop()
    xp["team"] = xp.team.map(nm)
    view = xp[xp.position.isin(pos) & (xp.price <= max_price) & (xp.start_rate >= min_start)]
    gw_cols = [c for c in xp.columns if c.startswith("GW")]
    cols = ["player", "team", "position", "price", "selected_pct", "start_rate", "xg90", "xa90", *gw_cols,
            "xPts_next", f"xPts_{horizon}gw", "xPts_per_m", "europe_gws", "total_points", "news"]
    st.dataframe(view[cols].round(2), hide_index=True, use_container_width=True, height=600)
    st.caption("xPts built from FPL's own xG/xA per 90 (shrunk toward positional priors), scaled by each fixture's "
               "expected goals from the team model; clean sheets from the opponent's expected goals; "
               "defensive contribution, saves and bonus from season rates.")
    if not tick.empty:
        st.subheader("Fixture ticker")
        metric = st.radio("Show", ["Attack (team expected goals)", "Clean sheet probability"], horizontal=True)
        col = "xg_for" if metric.startswith("Attack") else "p_cs"
        tick["label"] = (tick.opponent.map(nm).str[:3].str.upper() + np.where(tick.home, " (H)", " (a)")
                         + np.where(tick.after_europe, " ★", ""))
        tick["team"] = tick.team.map(nm)
        piv = tick.pivot_table(index="team", columns="gw", values=col, aggfunc="sum")
        lab = tick.pivot_table(index="team", columns="gw", values="label", aggfunc=lambda x: " + ".join(x))
        piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]
        lab = lab.reindex(piv.index)
        fig = go.Figure(go.Heatmap(z=piv.values, x=[f"GW{c}" for c in piv.columns], y=piv.index,
                                   text=lab.values, texttemplate="%{text}<br>%{z:.2f}", colorscale="RdYlGn"))
        fig.update_layout(height=40 + 28 * len(piv), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("★ = team plays in Europe 1-4 days before - rotation risk (regular starters' start "
                   "probability cut by 7% in that gameweek). `europe_gws` in the table counts these.")

elif page == "Players & props":
    st.header("Players & props")
    con = db.connect(DB_PATH)
    c1, c2, c3 = st.columns(3)
    with c1:
        lk = league_picker()
    with c2:
        ssn = season_picker()
    with c3:
        min_mins = st.slider("Min minutes", 0, 3000, 450, 90)
    pt = props.player_table(con, [lk], [int(ssn)])
    if pt.empty:
        st.info("No player data for this league. Understat covers the big 5; FotMob covers the rest "
                "(big chances created, xG, xA) when reachable; API-Football adds more with a key.")
    else:
        pt = pt[pt.minutes >= min_mins]
        pt["team"] = pt.team.map(nm)
        tab1, tab2 = st.tabs(["Leaderboards", "Props for upcoming fixtures"])
        with tab1:
            cols = ["player", "team", "position", "games", "minutes", "goals", "xg", "npxg", "g_minus_xg", "assists",
                    "xa", "xg_p90", "xa_p90", "xgi_p90", "shots_p90", "sot_p90", "key_passes_p90",
                    "big_chances_created", "big_chances_created_p90", "big_chances_missed", "xg_chain_p90", "source"]
            show = pt[[c for c in cols if c in pt]].dropna(axis=1, how="all")
            st.dataframe(show.sort_values(show.columns[6] if "xg" in show else "goals", ascending=False).round(2),
                         hide_index=True, use_container_width=True, height=600)
        with tab2:
            today = pd.Timestamp.today().normalize()
            fx = frame[(frame.league == lk) & (frame.status == "fixture") & (frame.date >= today) &
                       (frame.date <= today + pd.Timedelta(days=10))].copy()
            fx["home_name"], fx["away_name"] = fx.home.map(nm), fx.away.map(nm)
            pr = props.price_props(props.player_table(con, [lk], [int(ssn)]), fx, ratings, min_mins)
            if pr.empty:
                st.info("No upcoming fixtures with player data.")
            else:
                pr["team"] = pr.team.map(nm)
                m = st.selectbox("Match", ["All"] + sorted(pr.match.unique()))
                if m != "All":
                    pr = pr[pr.match == m]
                fmt = {c: "{:.0%}" for c in pr.columns if c.startswith("p_")}
                fmt.update({c: "{:.2f}" for c in ("exp_xg", "exp_xa", "exp_shots", "exp_sot", "fair_anytime")})
                st.dataframe(pr.style.format(fmt, na_rep=""), hide_index=True, use_container_width=True, height=600)
                st.caption("Anytime scorer = 1 − e^(−expected xG), with player xG/90 scaled by the fixture's "
                           "expected team goals and typical minutes. Compare `fair_anytime` with the bookmaker price.")

elif page == "Referees & cards":
    st.header("Referees & cards")
    c1, c2 = st.columns(2)
    with c1:
        lk = league_picker()
    with c2:
        ssn = season_picker()
    f = frame[(frame.league == lk) & (frame.season >= int(ssn) - 1)]
    rt = referees.referee_table(f)
    if rt.empty:
        st.info("No referee data for this league. football-data records referees for the Premier League; "
                "FotMob adds them for other leagues once its match details are fetched.")
    else:
        st.caption("Strictness = cards per game vs league average, shrunk toward 1.00 for referees with few games.")
        st.dataframe(rt[rt.league == lk].drop(columns="league").round(2), hide_index=True,
                     use_container_width=True)
    tc = referees.team_card_rates(frame[(frame.league == lk) & (frame.season == int(ssn))])
    if not tc.empty:
        tc["team"] = tc.team.map(nm)
        st.subheader("Team card rates (this season)")
        st.dataframe(tc.drop(columns="league").sort_values("cards_for", ascending=False).round(2),
                     hide_index=True, use_container_width=True)

elif page == "Backtest":
    st.header("Backtest")
    st.write("Walk-forward: the model is refitted every week using only matches before that week, then "
             "scored against results and margin-free closing odds. If the model's log loss is not close to the "
             "market's, treat its 'value' flags in that league with suspicion.")
    c1, c2, c3 = st.columns(3)
    with c1:
        lk = league_picker()
    with c2:
        ssn = season_picker()
    with c3:
        me = st.number_input("Min edge to bet", 0.0, 0.5, 0.05, 0.01)
    if st.button("Run backtest"):
        con = db.connect(DB_PATH)
        s = {"half_life_days": half_life, "xg_weight": xg_weight, "ridge": ridge}
        with st.spinner("Refitting week by week…"):
            bt, summ = backtest.run(con, frame, lk, int(ssn), me, s)
        if not summ:
            st.info("Not enough data.")
        else:
            cols = st.columns(5)
            cols[0].metric("Matches", summ["matches"])
            cols[1].metric("Model log loss", f"{summ['model_logloss']:.4f}")
            if "market_logloss" in summ:
                cols[2].metric("Market log loss", f"{summ['market_logloss']:.4f}",
                               f"{summ['model_logloss_same_games'] - summ['market_logloss']:+.4f} model vs market",
                               delta_color="inverse")
            cols[3].metric("Bets", summ["bets"])
            if summ.get("bets"):
                cols[4].metric("ROI", f"{summ['roi']:+.1%}", f"{summ['profit_units']:+.1f} units")
            bt["cal_bin"] = (bt.p_H * 10).round() / 10
            cal = bt.groupby("cal_bin").agg(pred=("p_H", "mean"), actual=("result", lambda x: (x == "H").mean()),
                                            n=("p_H", "size")).reset_index()
            fig = px.scatter(cal, x="pred", y="actual", size="n", title="Calibration (home win)")
            fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1, line=dict(dash="dot"))
            st.plotly_chart(fig, use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.caption("Models give probabilities, not certainties. Bet only what you can afford to lose - "
                   "BeGambleAware.org · 0808 8020 133")

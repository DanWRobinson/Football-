"""FPL extras: chip planner, multi-week transfer planner, mini-league rivals, price watch.

Inputs are the expected-points table from models/fpl.py (one column per gameweek), your squad
(my.py) and the bundle fetched from FPL's public API in pipeline.update_fpl (entry, history with
chips and transfers, private mini-league standings and rivals' latest picks).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import db
from .my import LIMITS, best_xi, load as my_load

CHIP_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
HALF_END = 19                 # 2025/26 rules: one set of chips for GW1-19, another for GW20-38
BENCH_FODDER = 4 * 4.2        # rough cost of four cheap bench players on a Free Hit
MAX_FT = 5


def bundle(con) -> dict | None:
    row = con.execute("SELECT json FROM fpl_json WHERE key='bundle'").fetchone()
    return json.loads(row[0]) if row else None


def meta(con) -> dict:
    row = con.execute("SELECT json FROM fpl_json WHERE key='meta'").fetchone()
    return json.loads(row[0]) if row else {}


# ------------------------------------------------------------------ chips

def chips_used(b: dict | None) -> list[tuple[str, int]]:
    if b and (b.get("history") or {}).get("chips") is not None:
        return [(c["name"], int(c["event"])) for c in b["history"]["chips"]]
    cfg = (my_load().get("fpl") or {}).get("chips_used") or {}
    return [(k, int(v)) for k, v in cfg.items()]


def chips_available(used: list[tuple[str, int]], next_gw: int) -> tuple[list[str], int]:
    lo, hi = (1, HALF_END) if next_gw <= HALF_END else (HALF_END + 1, 38)
    spent = {n for n, ev in used if lo <= ev <= hi}
    return [c for c in CHIP_NAMES if c not in spent], hi


def free_hit_xi(xp: pd.DataFrame, col: str, budget: float) -> float:
    """Best legal XI (any players) within budget for one gameweek, captain doubled. Exact (MILP)."""
    from scipy.optimize import Bounds, LinearConstraint, milp
    p = xp[xp[col].notna() & (xp[col] > 0)].reset_index(drop=True)
    if len(p) < 11:
        return float("nan")
    n = len(p)
    pts = p[col].values
    A, lo, hi = [np.ones(n)], [11], [11]
    for pos, (mn, mx) in LIMITS.items():
        A.append((p.position == pos).values.astype(float))
        lo.append(mn)
        hi.append(mx)
    A.append(p.price.values)
    lo.append(0)
    hi.append(max(budget - BENCH_FODDER, 40))
    for t in p.team.unique():
        A.append((p.team == t).values.astype(float))
        lo.append(0)
        hi.append(3)
    res = milp(-pts, constraints=LinearConstraint(np.array(A), lo, hi), integrality=np.ones(n),
               bounds=Bounds(0, 1))
    if not res.success:
        return float("nan")
    chosen = res.x > 0.5
    return float(pts[chosen].sum() + pts[chosen].max())


def chip_planner(xp: pd.DataFrame, squad_ids: list[int], gws: list[int], fixtures: pd.DataFrame,
                 budget: float, available: list[str], half_end: int) -> pd.DataFrame:
    sq = xp[xp.id.isin(squad_ids)]
    team_of = xp.set_index("team").index
    rows = []
    for g in gws:
        col = f"GW{g}"
        if col not in xp or g > half_end:
            continue
        fx = fixtures[fixtures.event == g]
        counts = pd.concat([fx.team_h, fx.team_a]).value_counts()
        dgw = int((counts >= 2).sum())
        blank = 20 - int(counts.gt(0).sum())
        xi = best_xi(sq, col)
        starters = sq[sq.id.isin(xi)]
        bench = sq[~sq.id.isin(xi)]
        mine = float(starters[col].sum() + starters[col].max())
        top = starters.sort_values(col, ascending=False).iloc[0]
        row = {"gw": g, "dgw": dgw, "blank": blank, "your_xi": mine}
        if "bboost" in available:
            row["bench_boost"] = float(bench[col].sum())
        if "3xc" in available:
            row["triple_captain"] = float(top[col])
            row["tc_player"] = top.player
        if "freehit" in available:
            fh = free_hit_xi(xp, col, budget)
            row["free_hit"] = fh - mine if fh == fh else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ transfers

def free_transfers(b: dict | None, next_gw: int) -> int | None:
    """Estimate banked free transfers from FPL history (1 per GW, max 5, chips keep them)."""
    if not b or not (b.get("history") or {}).get("current"):
        return None
    chips = {int(c["event"]): c["name"] for c in b["history"].get("chips") or []}
    ft = 0
    for h in sorted(b["history"]["current"], key=lambda x: x["event"]):
        ev = int(h["event"])
        if ev >= next_gw:
            break
        if ev == 1:
            ft = 1      # GW1 changes are unlimited; you start GW2 with 1
            continue
        if chips.get(ev) in ("wildcard", "freehit"):
            ft = min(ft + 1, MAX_FT)
            continue
        ft = min(max(ft - int(h.get("event_transfers") or 0), 0) + 1, MAX_FT)
    return max(ft, 1)


def transfer_plan(xp: pd.DataFrame, squad_ids: list[int], bank: float, horizon_col: str, ft: int | None) -> dict:
    """Compare rolling, one transfer and two transfers over the horizon (hits charged at -4).
    Gains are in best-XI points, so upgrading a bench player you'd never start counts for little."""
    from .my import best_moves
    cols = [c for c in xp.columns if c.startswith("GW")]
    sq = xp[xp.id.isin(squad_ids)].copy()
    m1 = best_moves(sq, xp, bank, cols)
    one = m1.iloc[0].to_dict() if not m1.empty else None
    two = None
    if one:
        for _, first in m1.head(6).iterrows():   # try the best few first moves, then the best follow-up to each
            sq2 = pd.concat([sq[sq.id != first.out_id], xp[xp.id == first.in_id]])
            m2 = best_moves(sq2, xp, bank + first.out_price - first.in_price, cols, exclude={first.out_id})
            if m2.empty:
                continue
            g = first.gain + m2.iloc[0].gain
            if two is None or g > two["gain"]:
                two = {"moves": [first.to_dict(), m2.iloc[0].to_dict()], "gain": float(g)}
    ft = ft or 1
    opts = [{"plan": "Roll the transfer", "gain": 0.0, "hit": 0, "detail": f"{min(ft + 1, MAX_FT)} free transfers next week"}]
    if one:
        opts.append({"plan": "1 transfer", "gain": float(one["gain"]), "hit": 0, "detail": f"{one['out']} → {one['inn']}"})
    if two:
        opts.append({"plan": "2 transfers", "gain": two["gain"], "hit": 0 if ft >= 2 else 4,
                     "detail": " + ".join(f"{m['out']} → {m['inn']}" for m in two["moves"])})
    for o in opts:
        o["net"] = o["gain"] - o["hit"]
    # projections are noisy: a free move must beat rolling by 1pt, and a plan with a hit must beat the
    # best free plan by 2pts, before we recommend it
    free = [o for o in opts if o["hit"] == 0]
    best = max(free, key=lambda o: o["net"] - (0 if o["plan"] == "Roll the transfer" else 1.0))
    for o in opts:
        if o["hit"] and o["net"] >= best["net"] + 2.0:
            best = o
    return {"ft": ft, "options": opts, "best": best["plan"]}


# ------------------------------------------------------------------ rivals

def rivals(b: dict | None, xp: pd.DataFrame, squad_ids: list[int], next_col: str, entry_id: int) -> list[dict]:
    out = []
    if not b:
        return out
    names = xp.set_index("id")
    for lg in b.get("leagues") or []:
        res = pd.DataFrame(lg.get("results") or [])
        if res.empty:
            continue
        me = res[res.entry == entry_id]
        my_total = float(me.total.iloc[0]) if len(me) else None
        res["gap"] = res.total - my_total if my_total is not None else np.nan
        own, caps = {}, {}
        for picks in (lg.get("picks") or {}).values():
            for p in picks.get("picks") or []:
                own[p["element"]] = own.get(p["element"], 0) + 1
                if p.get("is_captain"):
                    caps[p["element"]] = caps.get(p["element"], 0) + 1
        n = max(len(lg.get("picks") or {}), 1)

        def info(pid):
            if pid not in names.index:
                return None
            r = names.loc[pid]
            return {"player": r.player, "team": r.team, "next": float(r.get(next_col, 0) or 0)}
        diffs = [dict(**info(pid), owned=own.get(pid, 0) / n) for pid in squad_ids
                 if info(pid) and own.get(pid, 0) / n <= 0.3]
        threats = [dict(**info(pid), owned=c / n) for pid, c in own.items()
                   if pid not in squad_ids and c / n >= 0.5 and info(pid)]
        captains = [dict(**info(pid), share=c / n) for pid, c in sorted(caps.items(), key=lambda x: -x[1])[:3] if info(pid)]
        out.append({"name": lg.get("name"), "table": res, "rivals": n, "diffs": sorted(diffs, key=lambda d: -d["next"]),
                    "threats": sorted(threats, key=lambda d: -d["next"]), "captains": captains})
    return out


# ------------------------------------------------------------------ price watch

def price_watch(con, squad_ids: list[int]) -> pd.DataFrame:
    p = db.read_sql(con, "SELECT id, web_name AS player, team, position, price, selected_pct, transfers_in_event, "
                         "transfers_out_event, cost_change_event FROM fpl_players")
    total = meta(con).get("total_players")
    if p.empty or not total or "transfers_in_event" not in p or p.transfers_in_event.isna().all():
        return pd.DataFrame()
    p["owners"] = (p.selected_pct.fillna(0) / 100 * total).clip(lower=1000)
    p["net"] = p.transfers_in_event.fillna(0) - p.transfers_out_event.fillna(0)
    p["pressure"] = p.net / p.owners
    # rough thresholds: the real FPL algorithm is secret; heavy net transfers relative to ownership move prices
    p["outlook"] = np.select([p.pressure > 0.06, p.pressure > 0.03, p.pressure < -0.04, p.pressure < -0.02],
                             ["Rise likely", "Rise possible", "Fall likely", "Fall possible"], "")
    p["mine"] = p.id.isin(squad_ids)
    return p

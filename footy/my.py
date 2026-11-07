"""Personal settings (config/my.yaml): favourite team and FPL squad analysis."""

from __future__ import annotations

import itertools
import unicodedata
from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

from . import config, db
from .names import canonical

PATH = config.ROOT / "config" / "my.yaml"
LIMITS = {"GK": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}


@lru_cache(maxsize=1)
def load() -> dict:
    if not PATH.exists():
        return {}
    with open(PATH) as fh:
        return yaml.safe_load(fh) or {}


def app_name() -> str:
    return str(load().get("app_name") or "BetWise")


def favourite() -> str | None:
    t = load().get("favourite_team")
    return canonical(t) if t else None


def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c)).lower().strip()


def _next_label(con) -> dict[int, str]:
    """team_id -> 'MUN (A)' for its next unfinished fixture."""
    fx = db.read_sql(con, "SELECT * FROM fpl_fixtures WHERE finished=0 ORDER BY event, kickoff")
    teams = db.read_sql(con, "SELECT id, short_name FROM fpl_teams").set_index("id").short_name.to_dict()
    out = {}
    for r in fx.itertuples():
        out.setdefault(r.team_h_id, f"{teams.get(r.team_a_id, '?')} (H)")
        out.setdefault(r.team_a_id, f"{teams.get(r.team_h_id, '?')} (A)")
    return out


def squad(con) -> tuple[pd.DataFrame, float]:
    """Resolve the squad to FPL player ids. Returns (rows with id/captain/vice/bench, bank)."""
    cfg = load().get("fpl") or {}
    bank = float(cfg.get("bank") or 0)
    if cfg.get("entry_id") and (not cfg.get("squad") or _deadline_passed(con, cfg.get("gameweek"))):
        try:
            return _from_entry(con, int(cfg["entry_id"]))
        except Exception:  # noqa: BLE001 - fall back to the list in the file
            pass
    players = db.read_sql(con, "SELECT id, web_name, team_id FROM fpl_players")
    if players.empty or not cfg.get("squad"):
        return pd.DataFrame(), bank
    nxt = _next_label(con)
    players["key"] = players.web_name.map(_plain)
    rows = []
    for i, s in enumerate(cfg["squad"]):
        cand = players[players.key == _plain(s["name"])]
        if len(cand) > 1 and s.get("next"):
            cand = cand[cand.team_id.map(nxt).str.upper() == str(s["next"]).upper()] if not cand.empty else cand
        if cand.empty:
            continue
        rows.append(dict(id=int(cand.id.iloc[0]), captain=bool(s.get("captain")), vice=bool(s.get("vice")),
                         bench=int(s.get("bench") or 0), order=i))
    return pd.DataFrame(rows), bank


def _deadline_passed(con, gameweek) -> bool:
    """True once FPL's picks for `gameweek` are public (its deadline has passed)."""
    if not gameweek:
        return True
    ev = db.read_sql(con, "SELECT id FROM fpl_events WHERE is_current=1 OR finished=1 ORDER BY id DESC LIMIT 1")
    return not ev.empty and int(ev.id.iloc[0]) >= int(gameweek)


def _from_entry(con, entry: int) -> tuple[pd.DataFrame, float]:
    from .sources.http import get
    ev = db.read_sql(con, "SELECT id FROM fpl_events WHERE is_current=1 OR finished=1 ORDER BY id DESC LIMIT 1")
    gw = int(ev.id.iloc[0])
    data = get(f"https://fantasy.premierleague.com/api/entry/{entry}/event/{gw}/picks/", as_json=True, ttl=3600)
    rows = [dict(id=p["element"], captain=p.get("is_captain", False), vice=p.get("is_vice_captain", False),
                 bench=max(p["position"] - 11, 0), order=p["position"]) for p in data["picks"]]
    bank = (data.get("entry_history") or {}).get("bank", 0) / 10
    df = pd.DataFrame(rows)
    df.attrs["source"] = "fpl"
    return df, bank


def best_xi(sq: pd.DataFrame, col: str = "xPts_next") -> set[int]:
    """Highest-scoring legal XI (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD)."""
    best, best_ids = -1e9, set()
    by_pos = {p: sq[sq.position == p].sort_values(col, ascending=False) for p in LIMITS}
    for d, m, f in itertools.product(range(3, 6), range(2, 6), range(1, 4)):
        if d + m + f != 10:
            continue
        picks = [by_pos["GK"].head(1), by_pos["DEF"].head(d), by_pos["MID"].head(m), by_pos["FWD"].head(f)]
        if any(len(p) < n for p, n in zip(picks, (1, d, m, f))):
            continue
        tot = sum(p[col].sum() for p in picks)
        if tot > best:
            best, best_ids = tot, set(pd.concat(picks).id)
    return best_ids


def squad_points(sq: pd.DataFrame, cols: list[str]) -> float:
    """Expected points your best XI (with captain) would score across these gameweeks.
    Bench players only count when they'd make the XI - which is what a transfer really changes."""
    total = 0.0
    for c in cols:
        xi = sq[sq.id.isin(best_xi(sq, c))]
        if len(xi):
            total += float(xi[c].sum() + xi[c].max())
    return total


def best_moves(sq: pd.DataFrame, xp: pd.DataFrame, bank: float, cols: list[str], exclude: set | None = None,
               per_out: int = 4) -> pd.DataFrame:
    """Single transfers ranked by the gain in best-XI points over `cols`."""
    base = squad_points(sq, cols)
    pool = xp[~xp.id.isin(sq.id) & (xp.status == "a") & (xp.start_rate >= 0.5)]
    if exclude:
        pool = pool[~pool.id.isin(exclude)]
    counts = sq.team.value_counts().to_dict()
    long_col = "_h"
    pool = pool.assign(_h=pool[cols].sum(axis=1))
    rows = []
    for _, out in sq.iterrows():
        c = pool[(pool.position == out.position) & (pool.price <= out.price + bank + 1e-9)]
        c = c[np.array([counts.get(t, 0) - (1 if t == out.team else 0) < 3 for t in c.team], dtype=bool)]
        for _, inn in c.sort_values(long_col, ascending=False).head(per_out).iterrows():
            after = pd.concat([sq[sq.id != out.id], inn.to_frame().T.drop(columns="_h")])
            after[cols] = after[cols].astype(float)
            gain = squad_points(after, cols) - base
            rows.append(dict(out=out.player, out_id=out.id, out_team=out.team, out_price=out.price,
                             out_pts=float(out[cols].sum()), inn=inn.player, in_id=inn.id, in_team=inn.team,
                             in_price=inn.price, in_pts=float(inn[long_col]), gain=gain,
                             flag=out.status not in ("a", None) or (pd.notna(out.chance_next) and out.chance_next < 75)))
    return pd.DataFrame(rows).sort_values("gain", ascending=False) if rows else pd.DataFrame()


def analyse(con, xp: pd.DataFrame, horizon: int = 5) -> dict | None:
    """Squad table, recommended XI/captain and single-transfer suggestions."""
    sq_ids, bank = squad(con)
    if sq_ids.empty or xp.empty:
        return None
    long_col = f"xPts_{horizon}gw"
    sq = sq_ids.merge(xp, on="id", how="left").dropna(subset=["player"])
    if sq.empty:
        return None
    xi = best_xi(sq)
    sq["start_now"] = sq.bench == 0
    sq["start_best"] = sq.id.isin(xi)
    ranked = sq[sq.start_best].sort_values("xPts_next", ascending=False)
    cap = ranked.id.iloc[0] if len(ranked) else None
    vc = ranked.id.iloc[1] if len(ranked) > 1 else None

    def total(starters: pd.DataFrame, captain_id) -> float:
        return float(starters.xPts_next.sum() + starters.loc[starters.id == captain_id, "xPts_next"].sum())
    cur_cap = sq.loc[sq.captain, "id"].iloc[0] if sq.captain.any() else None
    now_pts = total(sq[sq.start_now], cur_cap)
    best_pts = total(sq[sq.start_best], cap)

    # single transfers ranked by how much they improve your best XI over the horizon
    gw_cols = [c for c in xp.columns if c.startswith("GW")][:horizon]
    moves = best_moves(sq, xp, bank, gw_cols)
    if not moves.empty:  # one suggestion per incoming player, so the list isn't five versions of the same move
        moves = moves.drop_duplicates("inn")
    return dict(synced=sq_ids.attrs.get("source") == "fpl", squad=sq, captain=cap, vice=vc, current_captain=cur_cap, now_pts=now_pts, best_pts=best_pts,
                moves=moves, bank=bank, horizon=horizon)

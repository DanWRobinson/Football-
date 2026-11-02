"""Team-news adjustment: lower a team's expected goals when key players are missing.

Each missing player's importance = his share of the team's season xG + xA. The team loses
`news_attack_weight` (default 60%) of that share - the rest is assumed to be covered by
whoever replaces him. Doubtful players count half. Missing regular goalkeepers/defenders
nudge goals conceded up a little. Adjustments are capped so one bad injury list can't
swing a prediction wildly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from .. import config, db

MAX_ATTACK_CUT = 0.25
DEF_BUMP_PER_PLAYER = 0.04
MAX_DEF_BUMP = 0.12


def _key(name: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(name)) if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def _same_player(a: str, b: str) -> bool:
    """'M. Salah' / 'Salah' / 'Mohamed Salah' all match."""
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    la, lb = ka.split()[-1], kb.split()[-1]
    return la == lb and len(la) >= 4 and (ka.split()[0][0] == kb.split()[0][0] or len(ka.split()) == 1
                                          or len(kb.split()) == 1)


@dataclass
class TeamNews:
    attack_mult: float = 1.0
    defence_mult: float = 1.0
    missing: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.missing:
            return ""
        parts = []
        if self.attack_mult < 0.995:
            parts.append(f"attack {self.attack_mult - 1:+.0%}")
        if self.defence_mult > 1.005:
            parts.append(f"conceding {self.defence_mult - 1:+.0%}")
        return "Out: " + ", ".join(self.missing[:6]) + (f" ({'; '.join(parts)})" if parts else "")


def _importance(con, league: str) -> pd.DataFrame:
    """player, team, share (of team xG+xA), defensive regular flag."""
    if league == "E0":
        p = db.read_sql(con, "SELECT web_name AS player, team, position, minutes, starts, xg, xa FROM fpl_players")
        if not p.empty:
            p["games_share"] = p.starts / max(p.starts.max(), 1)
            p["defensive"] = p.position.isin(["GK", "DEF"]) & (p.games_share > 0.6)
            p["xgi"] = p.xg.fillna(0) + p.xa.fillna(0)
            tot = p.groupby("team").xgi.transform("sum").replace(0, 1)
            p["share"] = p.xgi / tot
            return p[["player", "team", "share", "defensive"]]
    p = db.read_sql(con, "SELECT player, team, position, minutes, games, xg, xa, source FROM players "
                         "WHERE league=? AND season=?", (league, config.current_season()))
    if p.empty:
        return p
    p = p.sort_values("source").drop_duplicates(["team", "player"])
    p["xgi"] = p.xg.fillna(0) + p.xa.fillna(0)
    tot = p.groupby("team").xgi.transform("sum").replace(0, 1)
    p["share"] = p.xgi / tot
    team_mins = p.groupby("team").minutes.transform("max").replace(0, 1)
    pos = p.position.fillna("").str.upper()
    p["defensive"] = (pos.str.contains("GK") | pos.str.startswith("D")) & (p.minutes / team_mins > 0.6)
    return p[["player", "team", "share", "defensive"]]


def team_news(con, league: str, weight: float | None = None) -> dict[str, TeamNews]:
    weight = weight if weight is not None else config.model_settings().get("news_attack_weight", 0.6)
    av = db.read_sql(con, "SELECT * FROM availability WHERE league=?", (league,))
    if av.empty:
        return {}
    imp = _importance(con, league)
    out: dict[str, TeamNews] = {}
    for team, rows in av.groupby("team"):
        rows = rows.drop_duplicates("player")
        squad = imp[imp.team == team] if not imp.empty else imp
        cut, n_def, names = 0.0, 0, []
        for _, r in rows.iterrows():
            w = 0.5 if r.status == "doubt" else 1.0
            hit = squad[[_same_player(r.player, x) for x in squad.player]] if not squad.empty else squad
            share = float(hit.share.max()) if len(hit) else 0.0
            if len(hit) and bool(hit.defensive.any()):
                n_def += w
            cut += w * share * weight
            if share >= 0.05 or (len(hit) and bool(hit.defensive.any())):
                names.append(r.player + ("?" if r.status == "doubt" else ""))
        out[team] = TeamNews(attack_mult=1 - min(cut, MAX_ATTACK_CUT),
                             defence_mult=1 + min(n_def * DEF_BUMP_PER_PLAYER, MAX_DEF_BUMP),
                             missing=names)
    return out

"""Credit balances and what they qualify you for, from config/tickets.yaml."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import yaml

from .. import config

CONFIG = Path(os.environ.get("FOOTY_TICKETS_CONFIG", config.ROOT / "config" / "tickets.yaml"))
CATEGORY_LABEL = {
    "pl_home": "Premier League home", "pl_away": "Premier League away",
    "europe_home": "European home", "europe_away": "European away",
    "cup_home": "Domestic cup home", "cup_away": "Domestic cup away",
}


def load(path: Path | str | None = None) -> dict:
    p = Path(path or CONFIG)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _date(v) -> dt.date | None:
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        return None


def balances(cfg: dict, today: dt.date | None = None) -> dict[str, int]:
    """Balance per category: the My Account figure on `as_of` plus games played since."""
    today = today or dt.date.today()
    cr = dict(cfg.get("credits") or {})
    as_of = _date(cr.pop("as_of", None)) or dt.date.min
    out = {k: int(v or 0) for k, v in cr.items()}
    per = int(cfg.get("credits_per_game", 1))
    for g in cfg.get("games") or []:
        d = _date(g.get("date"))
        if d and as_of < d <= today:
            cat = g.get("category", "pl_home")
            out[cat] = out.get(cat, 0) + per
    return out


def levels(cfg: dict, today: dt.date | None = None) -> list[dict]:
    """One row per category with thresholds: where you are and what the next level needs."""
    bal = balances(cfg, today)
    rows = []
    for cat, tiers in (cfg.get("thresholds") or {}).items():
        b = bal.get(cat, 0)
        tiers = sorted(tiers, key=lambda t: -int(t["min"]))
        now = next((t for t in tiers if b >= int(t["min"])), None)
        nxt = next((t for t in reversed(tiers) if int(t["min"]) > b), None)
        rows.append({
            "category": CATEGORY_LABEL.get(cat, cat), "key": cat, "credits": b,
            "level": now["label"] if now else "-",
            "next_level": nxt["label"] if nxt else "Top level",
            "games_needed": (int(nxt["min"]) - b) if nxt else 0,
        })
    for cat, b in bal.items():          # categories with no thresholds still show the balance
        if cat not in (cfg.get("thresholds") or {}):
            rows.append({"category": CATEGORY_LABEL.get(cat, cat), "key": cat, "credits": b,
                         "level": "", "next_level": "", "games_needed": None})
    return rows


def eligibility(event, bal: dict[str, int]) -> str:
    """'eligible', 'not_eligible' (too few credits), 'higher_stage' (you qualify for an earlier,
    better stage instead) or 'unknown' (the page didn't say who it's for)."""
    if event.min_credits is None and event.max_credits is None:
        return "unknown"
    b = bal.get(event.category, 0)
    if event.min_credits is not None and b < event.min_credits:
        return "not_eligible"
    if event.max_credits is not None and b > event.max_credits:
        return "higher_stage"
    return "eligible"

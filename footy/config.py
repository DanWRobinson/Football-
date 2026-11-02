"""Configuration loading and season helpers."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env support (KEY=value lines) for API keys like API_FOOTBALL_KEY."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
CONFIG_PATH = Path(os.environ.get("FOOTY_CONFIG", ROOT / "config" / "leagues.yaml"))
DATA_DIR = Path(os.environ.get("FOOTY_DATA", ROOT / "data"))
DB_PATH = Path(os.environ.get("FOOTY_DB", DATA_DIR / "footy.db"))
CACHE_DIR = DATA_DIR / "cache"


@dataclass
class League:
    key: str
    name: str
    country: str
    tier: int
    understat: str | None = None
    fotmob: int | None = None
    api_football: int | None = None
    fpl: bool = False
    kind: str = "league"          # 'league' (domestic) | 'europe' (UEFA club competitions)
    xg_sources: list[str] = field(default_factory=list)

    @property
    def is_europe(self) -> bool:
        return self.kind == "europe"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.country})"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def leagues() -> dict[str, League]:
    out = {}
    for row in load_config()["leagues"]:
        lg = League(**row)
        out[lg.key] = lg
    return out


def league(key: str) -> League:
    try:
        return leagues()[key]
    except KeyError:
        raise KeyError(f"Unknown league {key!r}. Known: {', '.join(leagues())}") from None


def model_settings() -> dict:
    return dict(load_config().get("model", {}))


def current_season(today: dt.date | None = None) -> int:
    """Season is identified by its starting year: 2026 == 2026/27.

    European seasons start in late July/August, so anything from July onwards counts
    as the new season.
    """
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def season_label(season: int) -> str:
    return f"{season}/{str(season + 1)[-2:]}"


def resolve_leagues(spec: str | list[str] | None) -> list[str]:
    """'all', 'england', 'big5', 'europe', 'domestic', or a comma list of keys."""
    allk = list(leagues())
    if spec is None or spec == "all":
        return allk
    if isinstance(spec, str):
        spec = [s.strip() for s in spec.split(",") if s.strip()]
    out: list[str] = []
    for s in spec:
        s_low = s.lower()
        if s_low == "all":
            out += allk
        elif s_low == "england":
            out += [k for k, v in leagues().items() if v.country == "England"]
        elif s_low == "big5":
            out += ["E0", "SP1", "I1", "D1", "F1"]
        elif s_low == "europe":
            out += [k for k, v in leagues().items() if v.is_europe]
        elif s_low == "domestic":
            out += [k for k, v in leagues().items() if not v.is_europe]
        else:
            league(s.upper())
            out.append(s.upper())
    return list(dict.fromkeys(out))

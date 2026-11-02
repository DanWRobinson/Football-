"""ClubElo (clubelo.com): Elo ratings for every top-flight (and many second-tier) European club.

Used as the starting point for team ratings, which matters most early in the season and for
promoted/relegated teams. API: http://api.clubelo.com/YYYY-MM-DD -> CSV
Rank,Club,Country,Level,Elo,From,To
"""

from __future__ import annotations

import datetime as dt
import io

import pandas as pd

from .http import get

BASE = "http://api.clubelo.com"
COUNTRY = {"England": "ENG", "Spain": "ESP", "Italy": "ITA", "Germany": "GER", "France": "FRA",
           "Netherlands": "NED", "Portugal": "POR", "Belgium": "BEL", "Scotland": "SCO"}


def fetch(date: dt.date | None = None) -> pd.DataFrame:
    date = date or dt.date.today()
    return parse(get(f"{BASE}/{date.isoformat()}", ttl=12 * 3600))


def parse(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Club": "club", "Country": "country", "Level": "level", "Elo": "elo"})
    df = df.dropna(subset=["club", "elo"])
    df["level"] = pd.to_numeric(df["level"], errors="coerce").astype("Int64")
    return df[["club", "country", "level", "elo"]]

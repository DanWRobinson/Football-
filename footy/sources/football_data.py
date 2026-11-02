"""football-data.co.uk: results, shots, corners, cards and bookmaker odds.

The backbone source - it covers every league in the config, including the National
League (division code EC). Historical files: /mmz4281/{yyYY}/{DIV}.csv, upcoming
fixtures with odds: /fixtures.csv (main leagues).
"""

from __future__ import annotations

import io

import pandas as pd

from ..config import League
from .base import MatchRecord, Source
from .http import get

BASE = "https://www.football-data.co.uk"

STAT_COLS = {  # football-data column -> (side, metric)
    "HS": ("h", "shots"), "AS": ("a", "shots"),
    "HST": ("h", "sot"), "AST": ("a", "sot"),
    "HC": ("h", "corners"), "AC": ("a", "corners"),
    "HF": ("h", "fouls"), "AF": ("a", "fouls"),
    "HY": ("h", "yellow"), "AY": ("a", "yellow"),
    "HR": ("h", "red"), "AR": ("a", "red"),
    "HTHG": ("h", "ht_goals"), "HTAG": ("a", "ht_goals"),
}

BOOKS = {"Avg": "avg", "Max": "max", "PS": "pinnacle", "B365": "b365", "BW": "bwin", "WH": "wh"}


def season_code(season: int) -> str:
    return f"{season % 100:02d}{(season + 1) % 100:02d}"


def _odds_from_row(row: pd.Series) -> list[tuple]:
    out = []
    for prefix, book in BOOKS.items():
        for closing in ("", "C"):
            bk = book + ("_close" if closing else "")
            for sel in ("H", "D", "A"):
                col = f"{prefix}{closing}{sel}"
                if col in row and pd.notna(row[col]):
                    out.append(("1X2", sel, bk, float(row[col]), None))
            for sel, sym in (("O", ">"), ("U", "<")):
                col = f"{prefix}{closing}{sym}2.5"
                if col in row and pd.notna(row[col]):
                    out.append(("OU2.5", sel, bk, float(row[col]), 2.5))
            # Asian handicap
            line_col = "AHh" if not closing else "AHCh"
            for sel in ("H", "A"):
                col = f"{prefix}{closing}AH{sel}"
                if col in row and pd.notna(row[col]):
                    line = float(row[line_col]) if line_col in row and pd.notna(row[line_col]) else None
                    out.append(("AH", sel, bk, float(row[col]), line))
    return out


def _parse_date(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    d2 = pd.to_datetime(s, format="%d/%m/%y", errors="coerce")
    return d.fillna(d2)


def parse_csv(text: str, lg: League, season: int | None, division: str | None = None) -> list[MatchRecord]:
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    if division and "Div" in df.columns:
        df = df[df["Div"] == division]
    df = df.dropna(subset=["HomeTeam", "AwayTeam"]) if "HomeTeam" in df else df.iloc[0:0]
    if df.empty:
        return []
    df["_date"] = _parse_date(df["Date"])
    out = []
    for _, r in df.iterrows():
        if pd.isna(r["_date"]):
            continue
        hs, as_ = {}, {}
        for col, (side, metric) in STAT_COLS.items():
            if col in r and pd.notna(r[col]):
                (hs if side == "h" else as_)[metric] = float(r[col])
        played = "FTHG" in r and pd.notna(r.get("FTHG")) and pd.notna(r.get("FTAG"))
        ssn = season
        if ssn is None:
            d = r["_date"]
            ssn = d.year if d.month >= 7 else d.year - 1
        out.append(MatchRecord(
            league=lg.key, season=ssn, date=r["_date"].strftime("%Y-%m-%d"),
            home=str(r["HomeTeam"]).strip(), away=str(r["AwayTeam"]).strip(),
            home_goals=int(r["FTHG"]) if played else None,
            away_goals=int(r["FTAG"]) if played else None,
            kickoff=str(r["Time"]) if "Time" in r and pd.notna(r.get("Time")) else None,
            home_stats=hs, away_stats=as_, odds=_odds_from_row(r),
        ))
    return out


class FootballData(Source):
    name = "football_data"

    def matches(self, lg: League, season: int) -> list[MatchRecord]:
        text = get(f"{BASE}/mmz4281/{season_code(season)}/{lg.key}.csv", ttl=6 * 3600)
        return parse_csv(text, lg, season)

    def fixtures(self, lg: League) -> list[MatchRecord]:
        text = get(f"{BASE}/fixtures.csv", ttl=3 * 3600)
        return parse_csv(text, lg, None, division=lg.key)

"""The Odds API (the-odds-api.com): live prices from ~19 UK bookmakers and three exchanges.

Free tier = 500 credits a month; a request costs (markets x regions) credits. We ask for the
`uk` region with h2h (1X2) + totals (+ spreads for the competitions where Asian handicaps are
commonly offered), and pace spending so the month's credits last: each run gets
remaining / days-left-in-month, spent first on competitions with games in the next 48 hours.

Bet365 is not part of The Odds API, so Bet365 prices still come from football-data.co.uk.
The key is read from the ODDS_API_KEY environment variable (a GitHub secret) - never commit it.
"""

from __future__ import annotations

import datetime as dt
import os

import requests

BASE = "https://api.the-odds-api.com/v4"
EXCHANGES = {"betfair_ex_uk", "smarkets", "matchbook"}
BOOK_NAMES = {"williamhill": "William Hill", "paddypower": "Paddy Power", "skybet": "Sky Bet",
              "betfred_uk": "Betfred", "ladbrokes_uk": "Ladbrokes", "coral": "Coral", "unibet_uk": "Unibet",
              "betway": "Betway", "boylesports": "BoyleSports", "virginbet": "Virgin Bet", "sport888": "888sport",
              "leovegas": "LeoVegas", "grosvenor": "Grosvenor", "casumo": "Casumo", "livescorebet": "LiveScore Bet",
              "betfair_sb_uk": "Betfair Sportsbook", "betfair_ex_uk": "Betfair Exchange", "smarkets": "Smarkets",
              "matchbook": "Matchbook", "betvictor": "BetVictor"}


def key() -> str | None:
    return os.environ.get("ODDS_API_KEY") or None


def fetch(sport: str, markets: str, api_key: str) -> tuple[list, int | None]:
    r = requests.get(f"{BASE}/sports/{sport}/odds/", timeout=30, params={
        "regions": "uk", "markets": markets, "oddsFormat": "decimal", "apiKey": api_key})
    remaining = r.headers.get("x-requests-remaining")
    if r.status_code == 401:
        raise RuntimeError("Odds API rejected the key (401) - check the ODDS_API_KEY secret")
    if r.status_code == 429:
        raise RuntimeError("Odds API rate limit / monthly credits used up (429)")
    r.raise_for_status()
    return r.json(), int(float(remaining)) if remaining is not None else None


def parse(events: list) -> list[dict]:
    """-> [{home, away, date, rows: [(market, selection, book, price, line)]}]

    market/selection follow the odds table: 1X2 H/D/A, OU<line> O/U, AH<home line> H/A.
    book = 'oa:<bookmaker key>'.
    """
    out = []
    for e in events:
        home, away = e.get("home_team"), e.get("away_team")
        if not home or not away:
            continue
        rows = []
        for b in e.get("bookmakers") or []:
            bk = "oa:" + b["key"]
            for m in b.get("markets") or []:
                mk = m.get("key")
                for o in m.get("outcomes") or []:
                    price, name, pt = o.get("price"), o.get("name"), o.get("point")
                    if not price or price <= 1:
                        continue
                    if mk == "h2h":
                        sel = "H" if name == home else "A" if name == away else "D" if name == "Draw" else None
                        if sel:
                            rows.append(("1X2", sel, bk, float(price), None))
                    elif mk == "totals" and pt is not None:
                        sel = "O" if name == "Over" else "U" if name == "Under" else None
                        if sel:
                            rows.append((f"OU{float(pt):g}", sel, bk, float(price), float(pt)))
                    elif mk == "spreads" and pt is not None:
                        # point is the named team's handicap; store everything as the home line
                        if name == home:
                            rows.append((f"AH{float(pt):+g}", "H", bk, float(price), float(pt)))
                        elif name == away:
                            rows.append((f"AH{-float(pt):+g}", "A", bk, float(price), -float(pt)))
        commence = e.get("commence_time", "")
        try:
            d = dt.datetime.fromisoformat(commence.replace("Z", "+00:00"))
            date, ko = d.date().isoformat(), d.strftime("%H:%M")
        except ValueError:
            continue
        out.append({"home": home, "away": away, "date": date, "kickoff": ko, "rows": rows})
    return out

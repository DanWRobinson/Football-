"""Upcoming fixture predictions with market comparison.

Prices come from two places: football-data.co.uk (Bet365, Pinnacle, market average/max - a
snapshot a day or two before kick-off) and The Odds API (live prices from ~19 UK bookmakers and
three exchanges, stored with book names 'oa:<bookmaker>'). For every market we work out:

  * the model's probability (refunds removed for whole/quarter Asian handicap lines),
  * a sharp margin-free market probability (Pinnacle, else the exchanges, else the average),
  * the best price on offer and which bookmaker has it, and Bet365's price where known.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .. import config, db
from .probs import markets, score_matrix
from .ratings import Ratings
from .value import devig, edge, kelly

BOOK_PREF = ["pinnacle", "exch", "avg", "oa_avg", "b365"]
EXCHANGES = {"betfair_ex_uk", "smarkets", "matchbook"}
EXCHANGE_COMMISSION = 0.02   # best exchange prices are shown net of a typical 2% commission


def ah_label(line: float) -> str:
    """Same key format as the page's script: 0, +0.25, -1.5 ..."""
    return "0" if line == 0 else f"{line:+g}"


def ah_prob(M: np.ndarray, line: float, side: str = "H") -> float:
    """Win probability with refunds removed for backing `side` at handicap `line` (its own line)."""
    n = M.shape[0]
    i, j = np.indices(M.shape)
    d = (i - j) if side == "H" else (j - i)
    q = round(line * 4)

    def res(x):
        return np.sign(d + x)
    if q % 2:   # quarter line: half the stake on each neighbour
        a, b = res(line - 0.25), res(line + 0.25)
        win = (M * ((a > 0) & (b > 0))).sum() + 0.5 * (M * (((a > 0) & (b == 0)) | ((a == 0) & (b > 0)))).sum()
        lose = (M * ((a < 0) & (b < 0))).sum() + 0.5 * (M * (((a < 0) & (b == 0)) | ((a == 0) & (b < 0)))).sum()
    else:
        r = res(line)
        win, lose = (M * (r > 0)).sum(), (M * (r < 0)).sum()
    return float(win / (win + lose)) if win + lose > 0 else float("nan")


def _price_table(r: pd.Series) -> dict:
    """{(market, selection): {book: price}} from an odds_wide row."""
    out: dict[tuple, dict] = {}
    for col, v in r.items():
        if not isinstance(col, str) or pd.isna(v):
            continue
        m = re.match(r"^(1X2|OU[\d.]+|AH[+-]?[\d.]+)_(H|D|A|O|U)_(.+)$", col)
        if m:
            out.setdefault((m.group(1), m.group(2)), {})[m.group(3)] = float(v)
    return out


# a book's prices for one market must add up to a believable margin, or they're ignored for that game
# (thin exchange markets can show nonsense like 1.14 / 1.16 / 1.15)
MARGIN_OK = {"exchange": (0.97, 1.06), "book": (0.99, 1.20)}


def _sels(market: str) -> list[str]:
    return ["H", "D", "A"] if market == "1X2" else ["O", "U"] if market.startswith("OU") else ["H", "A"]


def _valid(prices: dict, market: str, book: str) -> bool:
    sels = _sels(market)
    vals = [prices.get((market, s), {}).get(book) for s in sels]
    if any(v is None or v <= 1 for v in vals):
        return False
    lo, hi = MARGIN_OK["exchange" if book[3:] in EXCHANGES else "book"]
    return lo <= sum(1 / v for v in vals) <= hi


def _sharp_probs(prices: dict, market: str, sels: list[str]):
    """Margin-free probabilities for a market, preferring the sharpest source available."""
    def complete(book):
        return all(book in prices.get((market, s), {}) for s in sels) and _valid(prices, market, book)
    if complete("pinnacle"):
        return devig([prices[(market, s)]["pinnacle"] for s in sels]), "pinnacle"
    ex = [b for b in (f"oa:{e}" for e in EXCHANGES) if complete(b)]
    if ex:
        return np.mean([devig([prices[(market, s)][b] for s in sels]) for b in ex], axis=0), "exchange"
    if complete("avg"):
        return devig([prices[(market, s)]["avg"] for s in sels]), "avg"
    books = [b for b in {b for s in sels for b in prices.get((market, s), {})} if b.startswith("oa:") and complete(b)]
    if books:
        return np.mean([devig([prices[(market, s)][b] for s in sels]) for b in books], axis=0), "bookmakers"
    return None, None


STALE_RATIO = 1.25   # a price this far above the market's fair odds is almost always stale or a mistake


def _best(prices: dict, market: str, sel: str, fair_p: float | None = None) -> tuple[float | None, str | None]:
    best, who = None, None
    offers = prices.get((market, sel), {})
    live = any(b.startswith("oa:") for b in offers)   # when live prices exist, ignore football-data's older snapshot
    for book, p in offers.items():
        if live and not book.startswith("oa:"):
            continue
        if fair_p and p * fair_p > STALE_RATIO:
            continue
        if book in ("avg", "pinnacle_close", "avg_close", "b365_close", "max_close") or book.endswith("_close"):
            continue
        if book.startswith("oa:") and not _valid(prices, market, book):
            continue
        name = book
        if book.startswith("oa:"):
            k = book[3:]
            if k in EXCHANGES:
                p = 1 + (p - 1) * (1 - EXCHANGE_COMMISSION)
            from ..sources.odds_api import BOOK_NAMES
            name = BOOK_NAMES.get(k, k)
        else:
            name = {"max": "best (football-data)", "b365": "Bet365", "pinnacle": "Pinnacle", "wh": "William Hill",
                    "bwin": "bwin"}.get(book, book)
        if best is None or p > best:
            best, who = p, name
    return best, who


def predict_fixtures(con, frame: pd.DataFrame, ratings: dict[str, Ratings],
                     days_ahead: int = 14, leagues: list[str] | None = None,
                     apply_news: bool = True) -> pd.DataFrame:
    today = pd.Timestamp.today().normalize()
    fx = frame[(frame.status == "fixture") & (frame.date >= today) &
               (frame.date <= today + pd.Timedelta(days=days_ahead))]
    if leagues:
        fx = fx[fx.league.isin(leagues)]
    if fx.empty:
        return pd.DataFrame()
    od = db.odds_wide(con, list(fx.match_id))
    fx = fx.merge(od, on="match_id", how="left")
    max_goals = config.model_settings().get("max_goals", 10)
    news = {}
    if apply_news:
        from .news import team_news
        news = {lg: team_news(con, lg) for lg in fx.league.unique()}
    rows = []
    for _, r in fx.iterrows():
        rt = ratings.get(r.league)
        if rt is None:
            continue
        lh0, la0 = rt.lambdas(r.home, r.away)
        nh, na = news.get(r.league, {}).get(r.home), news.get(r.league, {}).get(r.away)
        lh = lh0 * (nh.attack_mult if nh else 1) * (na.defence_mult if na else 1)
        la = la0 * (na.attack_mult if na else 1) * (nh.defence_mult if nh else 1)
        M = score_matrix(lh, la, rt.rho, max_goals)
        mk = markets(M)
        row = dict(match_id=r.match_id, league=r.league, date=r.date, kickoff=r.kickoff,
                   home=r.home, away=r.away, home_name=r.home_name, away_name=r.away_name,
                   referee=r.get("referee"), xg_home=lh, xg_away=la, xg_home_base=lh0, xg_away_base=la0,
                   news_home=nh.summary() if nh else "", news_away=na.summary() if na else "",
                   **{k: v for k, v in mk.items()})
        prices = _price_table(r)
        mp, book = _sharp_probs(prices, "1X2", ["H", "D", "A"])
        if mp is not None:
            row.update(mkt_H=mp[0], mkt_D=mp[1], mkt_A=mp[2], mkt_book=book)
        # every priced selection: model p, sharp market p, best price (+ where), Bet365 price
        px = {}
        for (market, sel) in prices:
            if market == "1X2":
                key, p = sel, mk[sel]
                mprob, _ = (mp, None) if mp is not None else (None, None)
                m_p = mprob[["H", "D", "A"].index(sel)] if mprob is not None else None
            elif market.startswith("OU"):
                line = float(market[2:])
                if f"O{line:g}" not in mk:
                    continue
                key = f"{sel}{line:g}"
                p = mk[f"O{line:g}"] if sel == "O" else 1 - mk[f"O{line:g}"]
                sp, _ = _sharp_probs(prices, market, ["O", "U"])
                m_p = (sp[0] if sel == "O" else sp[1]) if sp is not None else None
            else:   # AH<home line>
                hl = float(market[2:])
                line = hl if sel == "H" else -hl
                if abs(line) > 2.5:
                    continue
                key = ("AH" if sel == "H" else "AHA") + ah_label(line)
                p = ah_prob(M, line, sel)
                sp, _ = _sharp_probs(prices, market, ["H", "A"])
                m_p = (sp[0] if sel == "H" else sp[1]) if sp is not None else None
            best, who = _best(prices, market, sel, m_p)
            b365 = prices.get((market, sel), {}).get("b365")
            if best is None and b365 is None:
                continue
            px[key] = dict(market=market, sel=sel, p=p, mp=m_p, best=best, best_book=who, b365=b365)
        row["px"] = px
        # legacy columns used by the tables and CLI
        for k in ("H", "D", "A", "O2.5", "U2.5"):
            e = px.get(k, {})
            row[f"best_{k}"], row[f"b365_{k}"] = e.get("best"), e.get("b365")
        if "O2.5" in px and px["O2.5"].get("mp") is not None:
            row["mkt_O2.5"], row["mkt_U2.5"] = px["O2.5"]["mp"], 1 - px["O2.5"]["mp"]
        rows.append(row)
    out = pd.DataFrame(rows)
    if not out.empty:
        from .referees import expected_cards
        out = expected_cards(out, frame)
    return out


BOOKS = {"best": "best", "b365": "b365"}


def _sel_label(key: str, home: str, away: str) -> tuple[str, str]:
    if key in ("H", "D", "A"):
        return "1X2", {"H": home, "A": away, "D": "Draw"}[key]
    if key[0] in "OU" and key[1:2].isdigit():
        return f"{'Over' if key[0] == 'O' else 'Under'} {key[1:]}", f"{'Over' if key[0] == 'O' else 'Under'} {key[1:]} goals"
    if key.startswith("AHA"):
        return "Handicap", f"{away} {key[3:]}"
    return "Handicap", f"{home} {key[2:]}"


def value_bets(pred: pd.DataFrame, min_edge: float = 0.05, max_price: float = 8.0,
               kelly_fraction: float = 0.25, book: str = "best") -> pd.DataFrame:
    """One row per priced selection with model edge >= min_edge.

    book: 'best' = best price across all bookmakers/exchanges, 'b365' = Bet365's price.
    """
    if pred.empty or "px" not in pred:
        return pd.DataFrame()
    out = []
    for _, r in pred.iterrows():
        home, away = r.home_name or r.home, r.away_name or r.away
        for key, e in (r.px or {}).items():
            price = e.get("b365") if book == "b365" else e.get("best")
            if price is None or pd.isna(price) or price > max_price or not e.get("p") or pd.isna(e["p"]):
                continue
            p = e["p"]
            ed = edge(p, price)
            if ed >= min_edge:
                market, sel = _sel_label(key, home, away)
                out.append(dict(match_id=r.match_id, sel_key=key, date=r.date, league=r.league,
                                match=f"{home} v {away}", market=market, selection=sel, model_p=p, fair_odds=1 / p,
                                market_p=e.get("mp", np.nan), price=price, edge=ed,
                                bookmaker="Bet365" if book == "b365" else e.get("best_book"),
                                stake_pct=kelly(p, price, kelly_fraction),
                                team_news="; ".join(x for x in (r.get("news_home"), r.get("news_away")) if x)))
    return pd.DataFrame(out).sort_values("edge", ascending=False) if out else pd.DataFrame()

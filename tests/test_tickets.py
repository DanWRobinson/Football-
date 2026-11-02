import datetime as dt
import re
from pathlib import Path

import pytest

from footy.tickets import alerts, credits as cr, lfc
from footy.tickets.lfc import LONDON

FX = Path(__file__).parent / "fixtures" / "lfc"
NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=LONDON)


def at(*a):
    return dt.datetime(*a, tzinfo=LONDON)


def fake_fetcher(pages: dict[str, str]):
    calls = []

    def fetch(url, ttl):
        calls.append((url, ttl))
        for key, name in pages.items():
            if key in url if key != "LISTING" else url == lfc.LISTING_URL:
                return (FX / name).read_text()
        raise lfc_error(url)
    fetch.calls = calls
    return fetch


def lfc_error(url):
    from footy.sources.http import FetchError
    return FetchError(f"HTTP 404 for {url}")


MU = "liverpool-v-manchester-united-english-premier-league-20261122"
FENER = "fenerbahce-v-liverpool-uefa-champions-league-20261104"
CUP = "2026-27-carabao-cup-quarter-final"
PAGES = {MU: "man_utd.html", FENER: "fenerbahce.html", CUP: "cup_tbc.html",
         "LISTING": "listing.html"}


def match_page(name, slug="x"):
    fx = lfc.Fixture(slug, "https://www.liverpoolfc.com/tickets/match/" + slug)
    text = lfc.page_text((FX / name).read_text())
    assert lfc.read_header(text, fx, NOW)
    return fx, text


def test_listing_first_team_only():
    fx = lfc.fixtures_from_listing((FX / "listing.html").read_text())
    slugs = [f.slug for f in fx]
    assert slugs[:3] == [FENER, "crystal-palace-v-liverpool-english-premier-league-20261108", MU]
    assert CUP in slugs and not any(re.search("women|u21", s) for s in slugs)
    assert len(slugs) == 8


def test_match_header():
    fx, _ = match_page("man_utd.html")
    assert (fx.home, fx.away, fx.kickoff, fx.competition, fx.venue, fx.category) == (
        "Liverpool", "Manchester United", at(2026, 11, 22, 16, 30), "Premier League", "Anfield", "pl_home")
    fx, _ = match_page("fenerbahce.html")
    assert (fx.name, fx.kickoff, fx.category) == ("Fenerbahce v Liverpool", at(2026, 11, 4, 17, 45), "europe_away")
    fx, _ = match_page("cup_tbc.html")
    assert (fx.name, fx.kickoff, fx.competition, fx.category) == ("Liverpool v Bournemouth", None, "League Cup", "cup_home")


def test_cards():
    _, text = match_page("man_utd.html")
    cs = lfc.cards(text, NOW)
    assert [c.status for c in cs][:5] == ["REGISTER SOON"] * 5 and cs[5].status == "ON SALE SOON"
    c = cs[0]
    assert (c.when, c.stage, c.short, c.audience, lfc.credits_rule(c.title)) == (
        at(2026, 11, 6, 11), "registration", "Additional Members Sale Registration", None, (4, None))
    assert (cs[5].stage, cs[5].when) == ("members_sale", at(2026, 11, 16, 11))
    assert [c.audience for c in cs[6:]] == ["young_adult", "local", "local"]
    assert lfc.exchange_status(text, cs) == "none"   # "cannot be listed on the Ticket Exchange" is small print


def test_match_page_events():
    fx, text = match_page("man_utd.html")
    ev = [e for e in lfc.sale_events(text, fx.url, NOW, fx) if e.audience is None]
    got = {(e.title, e.when, e.has_time) for e in ev}
    assert ("Additional Members Sale Registration opens (4+ credits)", at(2026, 11, 6, 11), True) in got
    assert ("Additional Members Sale Registration closes (4+ credits)", at(2026, 11, 7, 10), True) in got
    assert ("Additional Members Sale Registration closes (1+ credits)", at(2026, 11, 7, 10), True) in got
    assert ("Additional Members Sale opens (4+ credits)", at(2026, 11, 16, 11), True) in got
    assert ("Additional Members Sale: Sale link emailed (4+ credits)", at(2026, 11, 13, 9), False) in got
    # "the sale taking place on Monday 16 November" doesn't add a second, untimed entry
    assert not any(e.when.date() == dt.date(2026, 11, 16) and not e.has_time for e in ev)
    assert all(e.category == "pl_home" and e.fixture == "Liverpool v Manchester United" for e in ev)
    local = [e for e in lfc.sale_events(text, fx.url, NOW, fx) if e.audience == "local"]
    assert {e.title for e in local} >= {"Local Members Ballot: Buy/renew All Red membership",
                                       "Local Members Ballot: Registration opens", "Local Members Ballot"}


def test_sold_out_away_card():
    fx, text = match_page("fenerbahce.html")
    ev = [(e.title, e.when, e.category) for e in lfc.sale_events(text, fx.url, NOW, fx)]
    assert ev == [("Members' sale opens (6+ credits)", at(2026, 10, 21, 11), "europe_away"),
                  ("Members' sale closes (6+ credits)", at(2026, 10, 21, 12, 30), "europe_away"),
                  ("Forwarding deadline (6+ credits)", at(2026, 10, 21, 13), "europe_away")]


def test_news_sentences():
    text = lfc.page_text((FX / "news.html").read_text())
    ev = lfc.sale_events(text, "u", NOW)
    got = [(e.stage, e.action, e.when, e.has_time) for e in ev]
    assert got == [("registration", "opens", at(2026, 10, 26, 14), True),
                   ("registration", "closes", at(2026, 11, 2, 10), True),
                   ("guaranteed_sale", "week_of", at(2026, 11, 9, 9), False)]


@pytest.mark.parametrize("s,expected", [
    ("Members with 13+ credits", (13, None)), ("a balance of four or more credits", (4, None)),
    ("between four and 12 credits", (4, 12)), ("supporters with fewer than four credits", (0, 3)),
    ("members with 5-8 credits", (5, 8)), ("no credit rule here at 13 or more", (13, None)),
    ("tickets on sale now", (None, None))])
def test_credit_rules(s, expected):
    assert lfc.credits_rule(s) == expected


@pytest.mark.parametrize("s,when", [
    ("on sale at 8.15am on Tuesday 10 November", at(2026, 11, 10, 8, 15)),
    ("Thursday, November 12 at 11:00", at(2026, 11, 12, 11, 0)),
    ("from 12 noon, 3rd Dec", at(2026, 12, 3, 12, 0)),
    ("closes 20/11/2026 at 9pm", at(2026, 11, 20, 21, 0)),
    ("closes at 12pm on Jan 4", at(2027, 1, 4, 12, 0)),      # no year: the nearest one
])
def test_datetimes(s, when):
    assert lfc.find_datetimes(s, NOW)[0].when == when


def test_credit_balances_and_levels():
    cfg = {"credits": {"as_of": "2026-09-01", "pl_home": 3},
           "games": [{"date": "2026-09-20", "category": "pl_home"},
                     {"date": "2026-08-01", "category": "pl_home"},     # before as_of: already counted
                     {"date": "2026-12-20", "category": "pl_home"}],    # not played yet
           "thresholds": {"pl_home": [{"min": 13, "label": "G"}, {"min": 4, "label": "B"}, {"min": 0, "label": "S"}]}}
    assert cr.balances(cfg, dt.date(2026, 10, 30))["pl_home"] == 4
    row = cr.levels(cfg, dt.date(2026, 10, 30))[0]
    assert (row["level"], row["next_level"], row["games_needed"]) == ("B", "G", 9)
    ev = lfc.SaleEvent("guaranteed_sale", "opens", NOW, True, "", "u", min_credits=13)
    assert cr.eligibility(ev, {"pl_home": 4}) == "not_eligible"
    ev.min_credits, ev.max_credits = 4, 12
    assert cr.eligibility(ev, {"pl_home": 4}) == "eligible"
    assert cr.eligibility(ev, {"pl_home": 14}) == "higher_stage"


def test_check_alerts_once(tmp_path):
    state = tmp_path / "state.json"
    cfg = {"credits": {"pl_home": 5}, "alerts": {"reminders": ["2d", "3h", "30m"]}, "exchange": {"watch": "home"}}
    sent = []
    send = lambda title, msg, *a, **k: sent.append(title)  # noqa: E731
    fetch = fake_fetcher(PAGES)
    quiet = lambda *a: None  # noqa: E731

    # first run records everything quietly (no flood of "new sale" alerts)
    r = alerts.check(cfg, at(2026, 11, 2, 12), state, fetch, send, log=quiet)
    assert r["fixtures"] == 3 and not sent      # past/sold-out Fenerbahce sale is ignored, women's games skipped
    assert "BEGIN:VEVENT" in (tmp_path / "lfc-tickets.ics").read_text()
    # registration opens Fri 6 Nov 11:00 -> 2-day reminder on Wed; one alert for the 4+ card, not one per card
    alerts.check(cfg, at(2026, 11, 4, 12), state, fetch, send, log=quiet)
    assert sent == ["LFC: Additional Members Sale Registration opens (4+ credits) in 2 days"]
    alerts.check(cfg, at(2026, 11, 4, 12, 15), state, fetch, send, log=quiet)
    assert len(sent) == 1                         # not repeated
    sent.clear()
    alerts.check(cfg, at(2026, 11, 6, 10, 40), state, fetch, send, log=quiet)
    assert sent == ["LFC: Additional Members Sale Registration opens (4+ credits) in 20 min",
                    "LFC: Additional Members Sale Registration closes (4+ credits) in 23h"]
    # local-postcode / young-adult cards stay quiet unless you opt in
    sent.clear()
    cfg["alerts"]["audiences"] = ["local"]
    alerts.check(cfg, at(2026, 11, 6, 9, 40), tmp_path / "s2.json", fetch, send, log=quiet)
    assert "LFC: Local Members Ballot: Registration opens in 20 min" in sent
    assert not any("Young Adult" in t for t in sent)


def test_new_sale_alert_and_eligibility(tmp_path):
    state = tmp_path / "state.json"
    sent = []
    send = lambda title, msg, *a, **k: sent.append((title, msg))  # noqa: E731
    quiet = lambda *a: None  # noqa: E731
    only_fener = {FENER: "fenerbahce.html", "LISTING": "listing.html"}
    cfg = {"credits": {"pl_home": 2}, "alerts": {"reminders": []}}
    alerts.check(cfg, NOW, state, fake_fetcher(only_fener), send, log=quiet)
    alerts.check(cfg, NOW, state, fake_fetcher(PAGES), send, log=quiet)      # Man Utd page appears
    titles = [t for t, _ in sent]
    assert "New LFC sale date: Liverpool v Manchester United" in titles
    msgs = [m for t, m in sent if "Manchester United" in t]
    # with 2 credits you get the 2+ card, and never the 4+ one
    assert any("Additional Members Sale Registration opens (2+ credits)" in m and "You qualify" in m for m in msgs)
    assert not any("(4+ credits)" in m or "(3+ credits)" in m for m in msgs)


def test_exchange_alert(tmp_path):
    state = tmp_path / "state.json"
    cfg = {"credits": {}, "alerts": {"reminders": []}, "exchange": {"watch": ["manchester united"]}}
    sent = []
    send = lambda title, msg, *a, **k: sent.append(title)  # noqa: E731
    alerts.check(cfg, NOW, state, fake_fetcher(PAGES), send, log=lambda *a: None)
    assert not sent
    fetch = fake_fetcher(dict(PAGES, **{MU: "man_utd_exchange.html"}))
    alerts.check(cfg, NOW + dt.timedelta(minutes=5), state, fetch, send, log=lambda *a: None)
    assert sent == ["Ticket Exchange open: Liverpool v Manchester United"]
    assert any(ttl == 0 and MU in u for u, ttl in fetch.calls)   # watched page fetched fresh


def test_ics_alarms():
    ev = lfc.SaleEvent("members_sale", "opens", at(2026, 11, 12, 11), True, "On sale, 11am", "https://x",
                       "Liverpool v Villarreal", category="europe_home")
    out = alerts.ics([ev], {"alerts": {"reminders": ["3h", "30m"]}})
    assert "DTSTART:20261112T110000Z" in out and "TRIGGER:-PT180M" in out and "TRIGGER:-PT30M" in out
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))


def test_failed_alert_is_retried(tmp_path):
    state = tmp_path / "state.json"
    cfg = {"credits": {"pl_home": 5}, "alerts": {"reminders": ["2d"]}}
    tries = []
    quiet = lambda *a: None  # noqa: E731
    alerts.check(cfg, at(2026, 11, 2, 12), state, fake_fetcher(PAGES), lambda *a, **k: None, log=quiet)
    down = lambda title, *a, **k: tries.append(title) or "failed"  # noqa: E731
    alerts.check(cfg, at(2026, 11, 4, 12), state, fake_fetcher(PAGES), down, log=quiet)
    alerts.check(cfg, at(2026, 11, 4, 12, 15), state, fake_fetcher(PAGES), down, log=quiet)
    assert len(tries) == 2 and tries[0] == tries[1]          # tried again, not marked as sent

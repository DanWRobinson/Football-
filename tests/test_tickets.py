import datetime as dt
from pathlib import Path

import pytest

from footy.tickets import alerts, credits as cr, lfc
from footy.tickets.lfc import LONDON

FX = Path(__file__).parent / "fixtures" / "lfc"
NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=LONDON)


def at(*a):
    return dt.datetime(*a, tzinfo=LONDON)


def fake_fetcher(pages: dict[str, str]):
    calls = []

    def fetch(url, ttl):
        calls.append((url, ttl))
        for key, name in pages.items():
            if key in url:
                return (FX / name).read_text()
        raise lfc_error(url)
    fetch.calls = calls
    return fetch


def lfc_error(url):
    from footy.sources.http import FetchError
    return FetchError(f"HTTP 404 for {url}")


PAGES = {"tickets-availability/liverpool-fc-v-newcastle": "newcastle.html",
         "tickets-availability/liverpool-fc-v-villarreal": "villarreal.html",
         "/news/all-red": "news.html",
         "tickets-availability": "listing.html"}


def test_listing_fixtures():
    fx = lfc.fixtures_from_listing((FX / "listing.html").read_text())
    assert [f.name for f in fx] == ["Aston Villa v Liverpool", "Liverpool v Villarreal",
                                   "Liverpool v Newcastle United"]
    n = fx[-1]
    assert n.kickoff == at(2027, 1, 31, 20, 0) and n.is_home and n.opponent == "Newcastle United"
    assert not fx[0].is_home and fx[0].kickoff == at(2026, 11, 15, 16, 30)
    assert lfc.news_links((FX / "listing.html").read_text())[0].endswith(
        "all-red-ticket-sale-details-confirmed-for-remaining-premier-league-home-games")


def test_sentences_to_events():
    text = lfc.page_text((FX / "news.html").read_text())
    ev = lfc.sale_events(text, "u", NOW)
    got = [(e.stage, e.action, e.when, e.has_time) for e in ev]
    assert got == [("registration", "opens", at(2026, 10, 26, 14), True),
                   ("registration", "closes", at(2026, 11, 2, 10), True),
                   ("guaranteed_sale", "week_of", at(2026, 11, 9, 9), False)]


def test_fixture_page_credits_and_exchange():
    fx = lfc.fixtures_from_listing((FX / "listing.html").read_text())[-1]
    text = lfc.page_text((FX / "newcastle.html").read_text())
    ev = {(e.stage, e.action): e for e in lfc.sale_events(text, fx.url, NOW, fx)}
    g = ev[("guaranteed_sale", "opens")]
    assert g.when == at(2026, 11, 10, 8, 15) and g.min_credits == 13 and g.fixture == fx.name
    r = ev[("registration", "closes")]
    assert (r.min_credits, r.max_credits) == (4, 12) and r.when == at(2026, 11, 2, 10)
    assert ev[("ballot_results", "closes")].title == "Ballot results due"
    # the match date (Saturday January 31) is not a sale date
    assert all(e.when.date() != fx.kickoff.date() for e in ev.values())
    assert lfc.exchange_status(text) == "none"


def test_next_data_page():
    text = lfc.page_text((FX / "villarreal.html").read_text())
    assert lfc.competition(text)[1] == "Champions League"
    assert lfc.exchange_status(text) == "open"
    fx = lfc.Fixture("v", "u", "Liverpool", "Villarreal", at(2026, 11, 25, 20), "Champions League", "europe")
    ev = lfc.sale_events(text, "u", NOW, fx)
    assert [(e.stage, e.when, e.min_credits, e.category) for e in ev if e.stage != "exchange"] == [
        ("members_sale", at(2026, 11, 12, 11), 3, "europe_home"),
        ("members_sale", at(2026, 11, 13, 13), 1, "europe_home")]


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
    cfg = {"credits": {"pl_home": 5, "europe_home": 0}, "alerts": {"reminders": ["2d", "3h", "30m"]},
           "exchange": {"watch": "home"}}
    sent = []
    send = lambda title, msg, *a, **k: sent.append(title)  # noqa: E731
    fetch = fake_fetcher(PAGES)

    # first run records everything quietly (no flood of "new sale" alerts) ...
    r = alerts.check(cfg, at(2026, 10, 30, 12), state, fetch, send, log=lambda *a: None)
    assert r["fixtures"] == 3 and r["errors"] and not any(t.startswith("New") for t in sent)
    assert (tmp_path / "lfc-tickets.ics").read_text().startswith("BEGIN:VCALENDAR")
    # ... but the registration deadline is under 3 days away, so the 2-day reminder fires
    sent.clear()
    alerts.check(cfg, at(2026, 10, 31, 11), state, fetch, send, log=lambda *a: None)
    assert any("Registration closes" in t for t in sent)
    n = len(sent)
    alerts.check(cfg, at(2026, 10, 31, 11, 15), state, fetch, send, log=lambda *a: None)
    assert len(sent) == n                                   # not repeated
    sent.clear()
    alerts.check(cfg, at(2026, 11, 2, 9, 40), state, fetch, send, log=lambda *a: None)
    assert sent == ["LFC: Registration closes (4-12 credits) in 20 min"]   # skips the stale 3h one

    # 5 PL credits: the 13+ guaranteed sale never alerts
    assert not any("Guaranteed sale opens" in t for t in sent)


def test_exchange_alert(tmp_path):
    state = tmp_path / "state.json"
    cfg = {"credits": {}, "alerts": {"reminders": []}, "exchange": {"watch": ["villarreal"]}}
    pages = dict(PAGES, **{"tickets-availability/liverpool-fc-v-villarreal": "newcastle.html"})
    sent = []
    send = lambda title, msg, *a, **k: sent.append(title)  # noqa: E731
    alerts.check(cfg, NOW, state, fake_fetcher(pages), send, log=lambda *a: None)
    assert not sent
    fetch = fake_fetcher(PAGES)                     # now the Villarreal page shows tickets
    alerts.check(cfg, NOW + dt.timedelta(minutes=5), state, fetch, send, log=lambda *a: None)
    assert sent == ["Ticket Exchange open: Liverpool v Villarreal"]
    assert any(ttl == 0 and "villarreal" in u for u, ttl in fetch.calls)   # watched page fetched fresh


def test_ics_alarms():
    ev = lfc.SaleEvent("members_sale", "opens", at(2026, 11, 12, 11), True, "On sale, 11am", "https://x",
                       "Liverpool v Villarreal", category="europe_home")
    out = alerts.ics([ev], {"alerts": {"reminders": ["3h", "30m"]}})
    assert "DTSTART:20261112T110000Z" in out and "TRIGGER:-PT180M" in out and "TRIGGER:-PT30M" in out
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))

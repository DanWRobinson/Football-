"""Turn scraped sale dates into phone notifications and a calendar feed.

State lives in data/tickets/state.json so each alert is sent once, even when the check runs
every 15 minutes from GitHub Actions.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import requests

from .. import config
from . import credits as cr
from . import lfc
from .lfc import LONDON, SaleEvent

STATE_DIR = config.DATA_DIR / "tickets"
STATE_PATH = STATE_DIR / "state.json"
ICS_PATH = STATE_DIR / "lfc-tickets.ics"


# ---------------------------------------------------------------- state

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"events": {}, "sent": {}, "exchange": {}, "fixtures": {}}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True))


def events_from_state(state: dict) -> list[SaleEvent]:
    return sorted((SaleEvent.from_json(d) for d in state.get("events", {}).values()), key=lambda e: e.when)


# ---------------------------------------------------------------- notifications

def notify(title: str, message: str, url: str | None = None, priority: str = "default",
           tags: str = "soccer", log=print) -> bool:
    """Send to ntfy (https://ntfy.sh) when NTFY_TOPIC is set; always print."""
    log(f"[alert] {title}: {message}")
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    body = {"topic": topic, "title": title, "message": message, "tags": tags.split(","),
            "priority": {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}.get(priority, 3)}
    if url:
        body["click"] = url
    headers = {}
    if tok := os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {tok}"
    try:
        r = requests.post(server, json=body, headers=headers, timeout=15)   # JSON keeps emoji/UTF-8 safe
        return r.ok
    except requests.RequestException as e:
        log(f"  ntfy failed: {e}")
        return False


def parse_offset(s: str) -> dt.timedelta:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([dhm])\s*", str(s))
    if not m:
        raise ValueError(f"Bad reminder {s!r}: use e.g. 2d, 3h, 30m")
    n, u = float(m.group(1)), m.group(2)
    return dt.timedelta(days=n) if u == "d" else dt.timedelta(hours=n) if u == "h" else dt.timedelta(minutes=n)


def _fmt_when(e: SaleEvent) -> str:
    w = e.when.astimezone(LONDON)
    if not e.has_time:
        return w.strftime("w/c %a %d %b") if e.action == "week_of" else w.strftime("%a %d %b (time TBC)")
    return w.strftime("%a %d %b, %H:%M").replace(", 0", ", ")


def _who(e: SaleEvent) -> str:
    return e.fixture or "All Red sale"


def _groups(events: list[SaleEvent]) -> list[tuple[SaleEvent, str]]:
    """One alert per (stage, action, time): the same deadline is often repeated on every fixture
    page and in the news. Returns (most specific event, who-text) pairs."""
    by: dict[tuple, list[SaleEvent]] = {}
    for e in events:
        by.setdefault((e.stage, e.action, e.when), []).append(e)
    out = []
    for evs in by.values():
        evs.sort(key=lambda e: (e.min_credits is None, e.fixture is None))
        names = list(dict.fromkeys(e.fixture for e in evs if e.fixture))
        who = names[0] + (f" + {len(names) - 1} more" if len(names) > 1 else "") if names else "All Red sale"
        out.append((evs[0], who))
    return sorted(out, key=lambda t: t[0].when)


def _wanted(e: SaleEvent, cfg: dict, bal: dict) -> bool:
    a = cfg.get("alerts") or {}
    stages = a.get("stages")
    if stages and e.stage not in stages:
        return False
    if a.get("only_eligible", True) and cr.eligibility(e, bal) in ("not_eligible", "higher_stage"):
        return False
    return True


# ---------------------------------------------------------------- exchange

def watched(fixtures: list[lfc.Fixture], spec) -> list[lfc.Fixture]:
    if spec in (None, "none", False):
        return []
    if spec == "all":
        return fixtures
    if spec == "home":
        return [f for f in fixtures if f.is_home]
    if spec == "away":
        return [f for f in fixtures if not f.is_home]
    names = [s.lower() for s in (spec if isinstance(spec, list) else [spec])]
    return [f for f in fixtures if any(n in f.opponent.lower() for n in names)]


# ---------------------------------------------------------------- main check

def check(cfg: dict | None = None, now: dt.datetime | None = None, state_path: Path = STATE_PATH,
          fetcher=lfc.fetch, send=notify, log=print, exchange_only: bool = False) -> dict:
    """One pass: scrape, alert on anything new or due, save state, rewrite the calendar."""
    cfg = cr.load() if cfg is None else cfg
    now = now or dt.datetime.now(LONDON)
    state = load_state(state_path)
    first_run = not state.get("events") and not state.get("fixtures")
    bal = cr.balances(cfg, now.date())
    acfg = cfg.get("alerts") or {}
    spec = (cfg.get("exchange") or {}).get("watch", "home")

    prev_fixtures = state.get("fixtures", {})
    live = {s for s, f in prev_fixtures.items() if f.get("watch")}
    fixtures, events, errors = lfc.scrape(now, live_slugs=live, fetcher=fetcher, log=log,
                                          fixture_ttl=0 if exchange_only else 3600)
    for e in errors:
        log(f"  fetch problem: {e}")
    if not fixtures and errors:
        state["last_error"] = errors[0]
        state["fail_count"] = state.get("fail_count", 0) + 1
        if state["fail_count"] == 8:     # ~2 hours of 15-minute checks: say so once
            send("LFC ticket checker can't read the club site",
                 f"{errors[0][:200]}. Alerts are paused until it can; check the club site yourself.",
                 lfc.LISTING_URL, "default", "warning")
        save_state(state, state_path)
        return {"fixtures": 0, "events": 0, "sent": 0, "errors": errors}

    sent = 0
    watch_slugs = {f.slug for f in watched(fixtures, spec)}

    # 1) newly announced sale dates
    known = state.setdefault("events", {})
    new = [e for e in events if e.id not in known and e.when > now]
    for e in events:
        known[e.id] = e.to_json()
    if not exchange_only and acfg.get("new_sales", True) and not first_run:
        for e, who in _groups([e for e in new if _wanted(e, cfg, bal)]):
            gkey = f"new|{e.stage}|{e.action}|{e.when.isoformat()}"
            if gkey in state.setdefault("sent", {}):
                continue
            state["sent"][gkey] = now.isoformat()
            note = " You qualify." if cr.eligibility(e, bal) == "eligible" else ""
            send(f"New LFC sale date: {who}", f"{e.title} - {_fmt_when(e)}.{note}", e.url,
                 "high", "soccer,calendar")
            sent += 1
    if first_run and new:
        log(f"First run: recorded {len(new)} upcoming sale dates without alerting; reminders start now.")

    # 2) reminders before each opening/closing
    offsets = sorted((parse_offset(o) for o in acfg.get("reminders", ["2d", "3h", "30m"])), reverse=True)
    sent_keys = state.setdefault("sent", {})
    upcoming = [SaleEvent.from_json(d) for d in known.values()]
    upcoming = [e for e in upcoming if e.when > now and e.action != "week_of" and _wanted(e, cfg, bal)]
    for e, who in _groups(upcoming):
        due = [o for o in offsets if e.when - o <= now]
        if not due or not e.has_time and min(due) < dt.timedelta(hours=12):
            continue
        o = min(due)
        gid = f"{e.stage}|{e.action}|{e.when.isoformat()}"
        key = f"{gid}|{int(o.total_seconds())}"
        if key in sent_keys:
            continue
        left = e.when - now
        hrs = left.total_seconds() / 3600
        in_txt = f"in {int(left.total_seconds() // 60)} min" if hrs < 1.5 else f"in {hrs:.0f}h" if hrs < 36 else f"in {left.days} days"
        send(f"LFC: {e.title} {in_txt}", f"{who} - {_fmt_when(e)}. Be logged in early.", e.url,
             "urgent" if hrs < 1.5 else "high", "alarm_clock")
        for o2 in due:
            sent_keys[f"{gid}|{int(o2.total_seconds())}"] = now.isoformat()
        sent += 1

    # 3) Ticket Exchange status changes
    ex = state.setdefault("exchange", {})
    for f in fixtures:
        old = ex.get(f.slug)
        if f.slug in watch_slugs and f.exchange == "open" and old != "open" and not first_run and \
                "exchange" in (acfg.get("stages") or ["exchange"]):
            send(f"Ticket Exchange open: {f.name}", f"{f.competition}, {f.kickoff.strftime('%a %d %b %H:%M')}. "
                 "Tickets on the Exchange go fast.", f.url, "urgent", "rotating_light")
            sent += 1
        ex[f.slug] = f.exchange

    state["fixtures"] = {f.slug: {"name": f.name, "url": f.url, "kickoff": f.kickoff.isoformat(),
                                  "competition": f.competition, "category": f.category,
                                  "exchange": f.exchange, "watch": f.slug in watch_slugs}
                         for f in fixtures}
    # forget events more than 30 days old
    cutoff = now - dt.timedelta(days=30)
    state["events"] = {k: v for k, v in known.items() if dt.datetime.fromisoformat(v["when"]) > cutoff}
    state["sent"] = {k: v for k, v in state.get("sent", {}).items()
                     if dt.datetime.fromisoformat(v) > cutoff}
    state["checked_at"] = now.isoformat()
    state.pop("last_error", None)
    state["fail_count"] = 0
    save_state(state, state_path)
    write_ics(events_from_state(state), cfg, state_path.parent / ICS_PATH.name)
    return {"fixtures": len(fixtures), "events": len(events), "new": len(new), "sent": sent, "errors": errors}


def watch(every: int, cfg: dict | None = None, log=print) -> None:
    """Loop for Ticket Exchange alerts from your own computer (Actions can't run this often)."""
    cfg = cr.load() if cfg is None else cfg
    floor = int((cfg.get("exchange") or {}).get("min_interval_seconds", 120))
    every = max(every, floor)
    log(f"Checking every {every}s. Ctrl+C to stop.")
    n = 0
    while True:
        try:
            r = check(cfg, log=log, exchange_only=n % 10 != 0)   # full sale scan every 10th pass
            log(f"{dt.datetime.now(LONDON):%H:%M} {r['fixtures']} fixtures, {r['sent']} alerts")
        except Exception as e:  # noqa: BLE001
            log(f"check failed: {e}")
        n += 1
        time.sleep(every)


# ---------------------------------------------------------------- calendar

def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def _fold(line: str) -> str:
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode())
        b = b[cut:]
    out.append(b.decode())
    return "\r\n ".join(out)


def ics(events: list[SaleEvent], cfg: dict) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    offsets = [parse_offset(o) for o in (cfg.get("alerts") or {}).get("reminders", ["2d", "3h", "30m"])]
    bal = cr.balances(cfg)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//footy//lfc-tickets//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:LFC ticket sales", "X-PUBLISHED-TTL:PT1H",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
    for e, who in _groups([e for e in events if _wanted(e, cfg, bal)]):
        elig = cr.eligibility(e, bal)
        lines += ["BEGIN:VEVENT", f"UID:{e.id}@footy-lfc-tickets", f"DTSTAMP:{stamp}",
                  f"SUMMARY:{_ics_escape('LFC ' + e.title + ' - ' + who)}"]
        if e.has_time:
            start = e.when.astimezone(dt.timezone.utc)
            lines += [f"DTSTART:{start:%Y%m%dT%H%M%SZ}", "DURATION:PT30M"]
        else:
            d = e.when.date()
            end = d + dt.timedelta(days=7 if e.action == "week_of" else 1)
            lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
        desc = e.text + ("\n\nYour credits qualify." if elig == "eligible" else "") + f"\n\n{e.url}"
        lines += [f"DESCRIPTION:{_ics_escape(desc)}", f"URL:{e.url}"]
        if e.has_time:
            for o in offsets:
                mins = int(o.total_seconds() // 60)
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(e.title)}",
                          f"TRIGGER:-PT{mins}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def write_ics(events: list[SaleEvent], cfg: dict, path: Path = ICS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ics(events, cfg), encoding="utf-8", newline="")
    return path

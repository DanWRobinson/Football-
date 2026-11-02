"""Read sale dates and Ticket Exchange status from liverpoolfc.com's public ticket pages.

The club writes sale details as prose ("Registration closes at 10am GMT on Monday November 2",
"Members with 13+ credits ... on sale Tuesday 10 November at 8.15am"), so the parser works on
sentences rather than a fixed page layout:

1. Pull the visible text out of the HTML (plus any text inside embedded Next.js JSON).
2. Split it into sentences and clauses, and classify each clause by sale stage
   (registration, ballot, guaranteed sale, members' sale, Ticket Exchange, ...).
3. Find every date in a clause, pair it with the nearest time, and decide from the words just
   before it whether the stage opens or closes then.
4. Pick up credit requirements ("13+ credits", "between four and 12 credits").

A redesign of the site can still break it; `python -m footy tickets doctor` shows exactly what
was parsed from each page so you can check it against what you see in the browser.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html as htmllib
import json
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
BASE = "https://www.liverpoolfc.com"
LISTING_URL = BASE + "/tickets"     # /tickets/tickets-availability redirects here
HUB_PAGES: list[str] = []

# ---------------------------------------------------------------- HTML -> text

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
          "section", "article", "header", "footer", "table", "dt", "dd", "button", "a", "span"}
_INLINE_OK = {"a", "span"}   # don't break sentences on these; they only add a space


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.json_blobs: list[str] = []
        self._skip = 0
        self._json = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
            self._json = tag == "script" and (
                a.get("id") == "__NEXT_DATA__" or "json" in (a.get("type") or ""))
        elif tag in _BLOCK:
            self.out.append(" " if tag in _INLINE_OK else "\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip = max(0, self._skip - 1)
            self._json = False
        elif tag in _BLOCK and tag not in _INLINE_OK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._skip:
            if self._json:
                self.json_blobs.append(data)
            return
        self.out.append(data)


def _json_strings(obj, acc: list[str]):
    if isinstance(obj, str):
        if len(obj) > 25 and " " in obj:
            acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _json_strings(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _json_strings(v, acc)


def page_text(html: str) -> str:
    """Visible text, one block per line. Rich text hidden in JSON (Next.js pages) is included."""
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    extra: list[str] = []
    for blob in p.json_blobs:
        try:
            _json_strings(json.loads(blob), extra)
        except ValueError:
            continue
    for s in extra:
        if "<" in s and ">" in s:          # HTML stored as a string
            s = page_text(s)
        if s not in text:
            text += "\n" + s
    text = htmllib.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------- dates and times

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_WD = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?"
_ORD = r"(?:st|nd|rd|th)?"
DATE_RE = re.compile(
    rf"(?:\b{_WD},?\s+)?(?:"
    rf"\b(?P<d1>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<m1>{_MON})\b\.?(?:,?\s+(?P<y1>20\d\d))?"
    rf"|\b(?P<m2>{_MON})\b\.?\s+(?P<d2>\d{{1,2}}){_ORD}\b(?:,?\s+(?P<y2>20\d\d))?"
    rf"|\b(?P<d3>\d{{1,2}})/(?P<m3>\d{{1,2}})/(?P<y3>(?:20)?\d\d)\b)", re.I)
TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2})(?:[.:](?P<mi>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])"
    r"|\b(?P<h24>[01]?\d|2[0-3]):(?P<mi24>[0-5]\d)\b(?!\s*[ap]\.?m)"
    r"|\b(?P<word>midday|noon|midnight)\b", re.I)


def _time(m: re.Match) -> tuple[int, int]:
    if m.group("word"):
        return (12, 0) if m.group("word").lower() in ("midday", "noon") else (23, 59)
    if m.group("h24") is not None:
        return int(m.group("h24")), int(m.group("mi24"))
    h, mi = int(m.group("h")), int(m.group("mi") or 0)
    pm = m.group("ap").lower().startswith("p")
    if h == 12:
        h = 0
    return (h + 12 if pm else h), mi


def _month(s: str) -> int:
    s = s.lower().rstrip(".")
    return _MONTHS.get(s) or _MONTHS[s[:3]]


def _pick_year(month: int, day: int, now: dt.datetime, latest: dt.datetime | None) -> int | None:
    """Sale dates rarely carry a year: take the one closest to today, and never after kick-off."""
    best = None
    for y in (now.year - 1, now.year, now.year + 1):
        try:
            d = dt.datetime(y, month, day, tzinfo=LONDON)
        except ValueError:
            continue
        if latest and d > latest + dt.timedelta(days=1):
            continue
        if best is None or abs(d - now) < abs(best - now):
            best = d
    return best.year if best else None


@dataclass
class Found:
    start: int
    end: int
    when: dt.datetime
    has_time: bool


def find_datetimes(text: str, now: dt.datetime, latest: dt.datetime | None = None) -> list[Found]:
    times = [(m.start(), m.end(), _time(m)) for m in TIME_RE.finditer(text)]
    used: set[int] = set()
    out = []
    for m in DATE_RE.finditer(text):
        g = m.groupdict()
        try:
            if g["d1"]:
                day, mon, yr = int(g["d1"]), _month(g["m1"]), g["y1"]
            elif g["d2"]:
                day, mon, yr = int(g["d2"]), _month(g["m2"]), g["y2"]
            else:
                day, mon, yr = int(g["d3"]), int(g["m3"]), g["y3"]
                yr = ("20" + yr) if yr and len(yr) == 2 else yr
        except (KeyError, ValueError):
            continue
        year = int(yr) if yr else _pick_year(mon, day, now, latest)
        if not year or not (1 <= mon <= 12):
            continue
        # nearest time within 45 characters either side, not already claimed by another date
        best = None
        for i, (ts, te, hm) in enumerate(times):
            if i in used:
                continue
            gap = ts - m.end() if ts >= m.end() else m.start() - te
            if 0 <= gap <= 45 and (best is None or gap < best[0]):
                best = (gap, i, hm)
        try:
            if best:
                used.add(best[1])
                when = dt.datetime(year, mon, day, *best[2], tzinfo=LONDON)
            else:
                when = dt.datetime(year, mon, day, 9, 0, tzinfo=LONDON)
        except ValueError:
            continue
        out.append(Found(m.start(), m.end(), when, best is not None))
    return out


# ---------------------------------------------------------------- stages and credits

STAGES = [   # (stage, pattern) - first match wins, so the order matters
    ("membership_deadline", r"purchased an? .{0,40}membership.{0,60}\b(by|before)\b|membership.{0,40}\bby midnight"),
    ("forwarding_deadline", r"forwarding deadline"),
    ("link_sent", r"^(?!.*\b(buy|purchase)\b).*\bunique link"),
    ("exchange", r"ticket exchange"),
    ("ballot_results", r"ballot.{0,60}(result|successful|unsuccessful|notified)|(result|successful|unsuccessful).{0,60}ballot"),
    ("guaranteed_sale", r"guaranteed"),
    ("registration", r"\bregist(er|ration|ered)"),
    ("ballot", r"\bballot"),
    ("hospitality", r"hospitality"),
    ("general_sale", r"general sale|general public"),
    ("season_ticket", r"season ticket holders?"),
    ("members_sale", r"\b(all red|members?'?)\b.{0,80}\b(sale|on sale|buy|purchase)|\b(sale|on sale)\b.{0,80}\b(all red|members?)\b"),
    ("sale", r"\bon sale\b|\bsale\b|\bpurchase\b"),
]
STAGE_LABEL = {
    "membership_deadline": "Buy/renew All Red membership", "forwarding_deadline": "Forwarding deadline",
    "link_sent": "Sale link emailed",
    "exchange": "Ticket Exchange", "ballot_results": "Ballot results", "guaranteed_sale": "Guaranteed sale",
    "registration": "Registration", "ballot": "Ballot", "hospitality": "Hospitality",
    "general_sale": "General sale", "season_ticket": "Season ticket holders",
    "members_sale": "Members' sale", "sale": "Sale",
}


def classify(clause: str) -> str | None:
    low = clause.lower()
    for stage, pat in STAGES:
        if re.search(pat, low):
            return stage
    return None


_CLOSE = re.compile(r"\b(clos(e|es|ed|ing)|until|deadline|ends?|ending|by|no later than|before)\b", re.I)
_OPEN = re.compile(r"\b(open(s|ed|ing)?|from|between|begin(s|ning)?|start(s|ing)?|go(es)? on sale|on sale|available|commenc(e|es|ing)|launch(es)?|live)\b", re.I)
_WEEK = re.compile(r"week (commencing|beginning|of)(\s+(on|the))?$", re.I)


def action_for(before: str) -> str:
    tail = before[-40:]
    if _WEEK.search(before.rstrip()):
        return "week_of"
    closes = [m.end() for m in _CLOSE.finditer(tail)]
    opens = [m.end() for m in _OPEN.finditer(tail)]
    if closes and (not opens or max(closes) >= max(opens)):
        return "closes"
    if opens:
        return "opens"
    return "on"


_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_N = r"(\d{1,3}|" + "|".join(_NUM_WORDS) + r")"


def _n(s: str) -> int:
    return int(s) if s.isdigit() else _NUM_WORDS[s.lower()]


def credits_rule(clause: str) -> tuple[int | None, int | None]:
    """(min, max) credits a clause asks for, or (None, None)."""
    low = clause.lower()
    if "credit" not in low:
        return None, None
    if m := re.search(rf"between\s+{_N}\s+and\s+{_N}", low):
        return _n(m.group(1)), _n(m.group(2))
    if m := re.search(rf"\b{_N}\s*(?:-|–|to)\s*{_N}\s+credits?", low):
        return _n(m.group(1)), _n(m.group(2))
    if m := re.search(rf"(?:fewer|less) than\s+{_N}", low):
        return 0, _n(m.group(1)) - 1
    if m := re.search(rf"\b{_N}\s*(?:\+|or more|or above|and above|plus)", low):
        return _n(m.group(1)), None
    if m := re.search(rf"\b(?:at least|minimum of|a minimum of)\s+{_N}", low):
        return _n(m.group(1)), None
    if m := re.search(rf"\b{_N}\s+credits?\b", low):
        return _n(m.group(1)), None
    return None, None


# ---------------------------------------------------------------- fixtures

MATCH_RE = re.compile(r"/tickets/match/(?P<slug>[a-z0-9-]+)")
OLD_SLUG_RE = re.compile(   # the pre-2026 /tickets/tickets-availability/<teams>-31-jan-2026-0800pm-502 links
    r"/tickets/tickets-availability/(?P<slug>(?P<teams>[a-z0-9-]+?-v-[a-z0-9-]+?)-(?P<d>\d{1,2})-(?P<m>[a-z]{3})-"
    r"(?P<y>20\d\d)-(?P<hm>\d{4})(?P<ap>am|pm)-(?P<id>\d+))\b")
NEWS_RE = re.compile(r"/news/(?P<slug>[a-z0-9-]*(?:ticket|ballot|sale|members|all-red)[a-z0-9-]*)\b")
NOT_FIRST_TEAM = re.compile(r"women|lfcw|u1\d|u2\d|academy|legends|hospitality")

COMPETITIONS = [   # (key, label, pattern)
    ("europe", "Champions League", r"champions league|uefa|europa league|conference league"),
    ("cup", "FA Cup", r"\bfa cup|emirates fa cup"),
    ("cup", "League Cup", r"carabao cup|league cup|efl cup"),
    ("pl", "Premier League", r"premier league"),
]


@dataclass
class Fixture:
    slug: str
    url: str
    home: str = "Liverpool"
    away: str = "TBC"
    kickoff: dt.datetime | None = None     # None = date & time TBC
    competition: str = "Premier League"
    comp_key: str = "pl"
    exchange: str = "unknown"          # open | none | closed | unknown
    venue: str = ""

    @property
    def is_home(self) -> bool:
        return self.home.startswith("Liverpool")

    @property
    def opponent(self) -> str:
        return self.away if self.is_home else self.home

    @property
    def name(self) -> str:
        return f"{self.home} v {self.away}"

    @property
    def category(self) -> str:
        return f"{self.comp_key}_{'home' if self.is_home else 'away'}"

    @property
    def ko_text(self) -> str:
        return self.kickoff.strftime("%a %d %b %H:%M") if self.kickoff else "TBC"


def _team(s: str) -> str:
    s = s.replace("-", " ").title()
    return {"Liverpool Fc": "Liverpool"}.get(s, s.replace(" Fc", "").replace("Afc ", "AFC "))


def _old_fixture(m: re.Match) -> Fixture | None:
    home, away = m.group("teams").split("-v-", 1)
    hm, ap = m.group("hm"), m.group("ap")
    h = (int(hm[:2]) % 12) + (12 if ap == "pm" else 0)
    try:
        ko = dt.datetime(int(m.group("y")), _MONTHS[m.group("m")], int(m.group("d")), h, int(hm[2:]), tzinfo=LONDON)
    except (KeyError, ValueError):
        return None
    return Fixture(m.group("slug"), BASE + "/tickets/tickets-availability/" + m.group("slug"),
                   _team(home), _team(away), ko)


def fixtures_from_listing(html: str) -> list[Fixture]:
    """Men's first-team fixture links from the tickets page, in page (date) order."""
    html = html.replace("\\/", "/")
    seen, out = set(), []
    for m in MATCH_RE.finditer(html):
        slug = m.group("slug")
        if slug in seen or NOT_FIRST_TEAM.search(slug):
            continue
        seen.add(slug)
        out.append(Fixture(slug, BASE + "/tickets/match/" + slug))
    for m in OLD_SLUG_RE.finditer(html):
        if m.group("slug") not in seen and (fx := _old_fixture(m)):
            seen.add(fx.slug)
            out.append(fx)
    return out


HEADER_RE = re.compile(r"^(?:(?P<hh>\d{1,2}):(?P<mm>\d{2}) / [A-Za-z]{3,9}\.? (?P<d>\d{1,2}) (?P<mon>[A-Za-z]{3,9})"
                       r"(?: (?P<y>20\d\d))?|(?P<tbc>Date (?:& |and )?Time TBC|TBC))$", re.I)


def read_header(text: str, fx: Fixture, now: dt.datetime) -> bool:
    """Fill kick-off, teams, venue and competition from the match page header:
    "16:30 / SUN 22 NOV" (or "Date & Time TBC"), then home, away, venue, competition lines."""
    lines = text.split("\n")
    for i, ln in enumerate(lines[:400]):
        m = HEADER_RE.match(ln.strip())
        if not m or len(lines) < i + 5:
            continue
        home, away, a, b = (x.strip() for x in lines[i + 1:i + 5])
        if not (home and away) or "liverpool" not in (home + away).lower():
            continue
        if m.group("tbc"):
            fx.kickoff = None
        else:
            try:
                mon, day = _month(m.group("mon")), int(m.group("d"))
                yr = int(m.group("y")) if m.group("y") else _pick_year(mon, day, now + dt.timedelta(days=120), None)
                fx.kickoff = dt.datetime(yr, mon, day, int(m.group("hh")), int(m.group("mm")), tzinfo=LONDON)
            except (KeyError, ValueError, TypeError):
                continue
        fx.home, fx.away = home, away
        # venue and competition come in either order; the competition is the one we recognise
        comp = next((x for x in (b, a) if competition(x)[2]), b)
        fx.venue = a if comp is b else b
        fx.comp_key, fx.competition, _ = competition(comp)
        if not competition(comp)[2]:
            fx.competition = comp
        return True
    return False


def competition(text: str) -> tuple[str, str, int]:
    low = text.lower()
    best = ("pl", "Premier League", 0)
    for key, label, pat in COMPETITIONS:
        n = len(re.findall(pat, low))
        if n > best[2]:
            best = (key, label, n)
    return best


def news_links(html: str, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for m in NEWS_RE.finditer(html.replace("\\/", "/")):
        u = BASE + "/news/" + m.group("slug")
        if u not in seen:
            seen.append(u)
    return seen[:limit]


# ---------------------------------------------------------------- sale cards

CARD_RE = re.compile(
    r"^(?P<status>(?:REGISTER|REGISTRATION|ON SALE|SALE|SOLD OUT|BALLOT|BUY|AVAILABLE|CLOSED|COMING|OPEN|"
    r"CHECK BACK|LIMITED|TICKET EXCHANGE|EXCHANGE)[A-Z \'&/-]*?)\s*"
    r"(?P<date>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]* \d{1,2} [A-Za-z]{3,9} 20\d\d,? \d{1,2}[:.]\d{2}\s*[ap]m)?\s*$")
_STOP = ("Official Club Partners", "Join All Red Video")


@dataclass
class Card:
    status: str            # "REGISTER SOON", "ON SALE NOW", "SOLD OUT", ...
    when: dt.datetime | None
    title: str             # "Additional Members Sale Registration All Red Members with ... 4 or more"
    body: str

    @property
    def short(self) -> str | None:
        """"Additional Members Sale Registration" - the title without the who-can-buy part
        (None when the title is only who-can-buy, e.g. "Season Ticket Holders and All Red Members ...")."""
        m = re.search(r"(?:^|\s)(?:All Red|Season Ticket Holders|Supporters|Official Members|Fans|Members with)\b", self.title)
        head = (self.title[:m.start()] if m else self.title).strip(" -:")
        return head or None

    @property
    def state(self) -> str:
        st = self.status.upper()
        if "SOLD OUT" in st:
            return "sold_out"
        if "CLOSED" in st:
            return "closed"
        if "SOON" in st or "COMING" in st or "CHECK BACK" in st:
            return "soon"
        return "open"

    @property
    def stage(self) -> str:
        st, title = self.status.upper(), self.title.lower()
        if "exchange" in title or "EXCHANGE" in st:
            return "exchange"
        if "REGIST" in st:
            return "registration"
        if "BALLOT" in st or ("ballot" in title and "sale" not in title):
            return "ballot"
        if "hospitality" in title:
            return "hospitality"
        if "general sale" in title or "general public" in title:
            return "general_sale"
        if re.search(r"\b(all red|members?)\b", title):
            return "members_sale"
        if "season ticket" in title:
            return "season_ticket"
        return "sale"

    @property
    def audience(self) -> str | None:
        """Cards only some members can use (by age, postcode, ...). None = any All Red member."""
        t = self.title.lower()
        if "postcode" in t or re.search(r"\blocal\b", t):
            return "local"
        if re.search(r"young adult|aged|\b1[6-9]\s*-\s*2\d", t):
            return "young_adult"
        if "junior" in t and "all red" not in t:
            return "junior"
        if re.search(r"disab|accessib|ambulant|wheelchair", t):
            return "accessible"
        if "season ticket" in t and "member" not in t:
            return "season_ticket"
        return None


def cards(text: str, now: dt.datetime) -> list[Card]:
    lines = text.split("\n")
    heads = [(i, m) for i, ln in enumerate(lines) if (m := CARD_RE.match(ln.strip()))
             and (m.group("date") or not m.group("status").startswith("BUY"))]   # "BUY NOW" buttons aren't cards
    out = []
    for n, (i, m) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        for j in range(i + 1, end):
            if lines[j].strip().startswith(_STOP):
                end = j
                break
        rest = [ln for ln in lines[i + 1:end] if ln.strip() and ln.strip().lower() not in ("read more", "buy now")]
        if not rest:
            continue
        when = None
        if m.group("date"):
            f = find_datetimes(m.group("date"), now)
            when = f[0].when if f else None
        out.append(Card(m.group("status").strip(), when, rest[0].strip(), "\n".join(rest[1:])))
    return out


_CREDIT_CAT = re.compile(r"(premier league|european|uefa|champions league|domestic cup|cup|fa cup|league cup)\s+"
                         r"(home|away)\s+(?:match\s+)?credit", re.I)


def credit_category(text: str) -> str | None:
    """"European Away Match Credit Balance" -> europe_away."""
    m = _CREDIT_CAT.search(text)
    if not m:
        return None
    comp = m.group(1).lower()
    key = "pl" if comp == "premier league" else "europe" if comp in ("european", "uefa", "champions league") else "cup"
    return f"{key}_{m.group(2).lower()}"


def exchange_status(text: str, page_cards: list[Card] | None = None) -> str:
    """Ticket Exchange status. Cards are definitive when the page has them; otherwise look for
    sentences that say the Exchange is open (ignoring the usual "cannot be listed on" small print)."""
    if page_cards:
        ex = [c for c in page_cards if c.stage == "exchange"]
        if not ex:
            return "none"
        states = {c.state for c in ex}
        return "open" if "open" in states else "closed" if "closed" in states else "none"
    status = "unknown"
    for clause in re.split(r"(?<=[.!?])\s+|\n", text):
        low = clause.lower()
        if "exchange" not in low or re.search(r"cannot|can not|can't|not be (listed|returned)", low):
            continue
        if re.search(r"sold out|no tickets|none available|not (currently )?available|currently no|unavailable", low):
            status = "none"
        elif re.search(r"\bclosed\b|has closed|now closed", low):
            status = "closed"
        elif re.search(r"\bavailable\b|\bnow open\b|\bis open\b|\bopen now\b|\blive\b|buy now|\bon sale now\b", low):
            return "open"
    return status


# ---------------------------------------------------------------- sale events

@dataclass
class SaleEvent:
    stage: str
    action: str                       # opens | closes | on | week_of
    when: dt.datetime
    has_time: bool
    text: str
    url: str
    fixture: str | None = None        # "Liverpool v Manchester United"
    fixture_slug: str | None = None
    category: str = "pl_home"
    min_credits: int | None = None
    max_credits: int | None = None
    card: str | None = None           # short card name, "Additional Members Sale Registration"
    audience: str | None = None       # local | young_adult | junior | accessible | season_ticket
    id: str = field(default="")

    def __post_init__(self):
        if not self.id:
            key = (f"{self.fixture_slug or self.url}|{self.stage}|{self.action}|{self.when.isoformat()}|"
                   f"{self.min_credits}|{self.max_credits}|{self.audience}|{self.card}")
            self.id = hashlib.sha1(key.encode()).hexdigest()[:12]

    @property
    def title(self) -> str:
        verb = {"opens": "opens", "closes": "closes", "on": "", "week_of": ""}[self.action]
        if self.stage == "ballot_results":
            verb = "due" if self.action in ("closes", "on") else verb
        if self.stage in ("membership_deadline", "forwarding_deadline", "link_sent"):
            verb = ""
        label = STAGE_LABEL[self.stage]
        if self.card:
            same = {"registration": "regist", "ballot": "ballot", "members_sale": "sale", "sale": "sale",
                    "general_sale": "sale", "exchange": "exchange", "hospitality": "hospitality"}
            label = self.card if same.get(self.stage, "@") in self.card.lower() else f"{self.card}: {label}"
        who = ""
        if self.min_credits is not None:
            who = f" ({self.min_credits}" + (f"-{self.max_credits}" if self.max_credits is not None else "+") + " credits)"
        return re.sub(r"\s+", " ", f"{label} {verb}{who}").strip()

    def to_json(self) -> dict:
        d = asdict(self)
        d["when"] = self.when.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict) -> "SaleEvent":
        d = dict(d)
        d["when"] = dt.datetime.fromisoformat(d["when"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_CLAUSE_SPLIT = re.compile(r";\s+|\s+(?=using (?:the|a|their) unique link)|,\s+(?:and|while|whereas|but)\s+|\s+(?:while|whereas)\s+|\s+and\s+(?=(?:those|members|supporters|fans|all red|season)\b)", re.I)


def _paragraphs(text: str) -> list[list[str]]:
    return [[s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line) if s.strip()]
            for line in text.split("\n")]


def _text_events(text: str, url: str, now: dt.datetime, fixture: Fixture | None, category: str,
                 stage0: str | None = None, credits0=(None, None), card: Card | None = None) -> list[SaleEvent]:
    latest = fixture.kickoff if fixture else None
    out: list[SaleEvent] = []
    last_stage = stage0
    for para in _paragraphs(text):
        para_rule: dict[str, tuple] = {}     # stage -> credit rule stated earlier in this paragraph
        for sent in para:
            if len(sent) > 600:      # navigation dumps and the like
                continue
            s_credits = credits_rule(sent)
            s_stage = classify(sent)
            if not s_stage and (s_credits[0] is not None or card):
                s_stage = last_stage   # "Members with 9+ credits from 11am on ..." continues the last stage
            last_stage = s_stage or last_stage
            for clause in _CLAUSE_SPLIT.split(sent):
                stage = classify(clause) or s_stage
                if not stage:
                    continue
                if card and stage in ("sale", "members_sale", "registration", "ballot") and stage != stage0 \
                        and not re.match(r"^\s*(registration|ballot|sale)\s*:", clause, re.I):
                    stage = stage0 if stage in ("sale", "members_sale") else stage
                mn, mx = credits_rule(clause)
                if mn is None:
                    mn, mx = s_credits
                if mn is None:
                    mn, mx = para_rule.get(stage, credits0)
                elif stage not in para_rule:
                    para_rule[stage] = (mn, mx)
                prev, last_act = 0, None
                for f in find_datetimes(clause, now, latest):
                    before = clause[prev:f.start]
                    prev = f.end
                    if fixture and fixture.kickoff and f.when.date() == fixture.kickoff.date() and stage != "exchange":
                        continue          # that's the match date, not a sale date
                    act = action_for(before)
                    if last_act == "opens" and re.fullmatch(r"\s*(and|to|-|–)\s*(on\s+)?", before, re.I):
                        act = "closes"    # "between Thursday 12 November and Friday 13 November"
                    last_act = act
                    out.append(SaleEvent(
                        stage, act, f.when, f.has_time and act != "week_of", sent, url,
                        fixture.name if fixture else None, fixture.slug if fixture else None,
                        credit_category(clause) or category, mn, mx,
                        card.short if card and card.short else None, card.audience if card else None))
    return out


def sale_events(text: str, url: str, now: dt.datetime, fixture: Fixture | None = None,
                default_category: str = "pl_home", page_cards: list[Card] | None = None) -> list[SaleEvent]:
    """Every dated sale step on a page. Match pages are read card by card (each card's title says
    who it's for and which credits count); news pages, which have no cards, sentence by sentence."""
    category = fixture.category if fixture else default_category
    page_cards = cards(text, now) if page_cards is None else page_cards
    events: list[SaleEvent] = []
    if page_cards:
        for c in page_cards:
            mn, mx = credits_rule(c.title)
            cat = credit_category(c.title) or category
            if c.when:
                act = "opens" if c.state in ("soon", "open", "sold_out") else "on"
                events.append(SaleEvent(c.stage, act, c.when, True, f"{c.status.title()}: {c.title}", url,
                                        fixture.name if fixture else None, fixture.slug if fixture else None,
                                        cat, mn, mx, c.short or None, c.audience))
            events += _text_events(c.body, url, now, fixture, cat, c.stage, (mn, mx), c)
        # "...the sale taking place on Monday 16 November" just repeats a date another card gives a time for
        timed_days = {e.when.date() for e in events if e.has_time}
        events = [e for e in events if e.has_time or e.action == "week_of" or e.when.date() not in timed_days
                  or e.stage in ("link_sent", "ballot", "ballot_results", "membership_deadline")]
    else:
        events = _text_events(text, url, now, fixture, category)
    uniq: dict[str, SaleEvent] = {}
    for e in events:
        uniq.setdefault(e.id, e)
    return sorted(uniq.values(), key=lambda e: e.when)


# ---------------------------------------------------------------- fetching

def fetch(url: str, ttl: float) -> str:
    from ..sources import http
    return http.get(url, ttl=ttl, headers={"User-Agent": "footy-lfc-ticket-alerts/1.0 (personal sale-date reminders)"})


def scrape(now: dt.datetime | None = None, fixture_ttl: float = 3600, listing_ttl: float = 600,
           live_slugs: set[str] | None = None, fetcher=fetch, log=print):
    """Fetch the tickets page, every men's first-team match page and linked ticket news.

    Returns (fixtures, events, errors). Pages in `live_slugs` are always fetched fresh (used for
    Ticket Exchange alerts); everything else is cached for `fixture_ttl` seconds.
    """
    now = now or dt.datetime.now(LONDON)
    live_slugs = live_slugs or set()
    errors: list[str] = []
    events: list[SaleEvent] = []
    try:
        listing = fetcher(LISTING_URL, listing_ttl)
    except Exception as e:  # noqa: BLE001
        return [], [], [f"{LISTING_URL}: {e}"]
    extra = list(dict.fromkeys(news_links(listing) + HUB_PAGES))
    fixtures = []
    for fx in fixtures_from_listing(listing):
        try:
            html = fetcher(fx.url, 0 if fx.slug in live_slugs else fixture_ttl)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{fx.url}: {e}")
            continue
        text = page_text(html)
        page_cards = cards(text, now)
        if not read_header(text, fx, now):
            if not page_cards:
                errors.append(f"{fx.url}: no match header or sale cards found (page layout changed?)")
                continue
            fx.comp_key, fx.competition, _ = competition(text)
        if "women" in fx.competition.lower() or (fx.kickoff and fx.kickoff < now - dt.timedelta(hours=3)):
            continue
        fx.exchange = exchange_status(text, page_cards)
        events += sale_events(text, fx.url, now, fx, page_cards=page_cards)
        fixtures.append(fx)
        for u in news_links(html, 3):
            if u not in extra:
                extra.append(u)
    for u in extra[:10]:
        try:
            html = fetcher(u, fixture_ttl)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{u}: {e}")
            continue
        events += sale_events(page_text(html), u, now)
    uniq = {e.id: e for e in events}
    return fixtures, sorted(uniq.values(), key=lambda e: e.when), errors

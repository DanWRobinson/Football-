"""Team-name normalisation so different providers line up.

Every provider spells clubs differently ("Man United", "Manchester United",
"Manchester Utd"). We reduce a name to a canonical key, apply a hand-maintained alias
table, then fall back to fuzzy matching against the teams already known in a league.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

# key -> canonical key. Keys are post-normalisation (lowercase, no accents/suffixes).
ALIASES: dict[str, str] = {
    # England
    "manchester united": "man united", "manchester utd": "man united", "man utd": "man united",
    "manchester city": "man city",
    "tottenham hotspur": "tottenham", "spurs": "tottenham",
    "wolverhampton wanderers": "wolves", "wolverhampton": "wolves",
    "nottingham forest": "nottm forest", "nott m forest": "nottm forest", "nottham forest": "nottm forest",
    "newcastle united": "newcastle", "newcastle utd": "newcastle",
    "west ham united": "west ham", "brighton and hove albion": "brighton", "brighton hove albion": "brighton",
    "leicester city": "leicester", "leeds united": "leeds", "ipswich town": "ipswich",
    "sheffield united": "sheffield united", "sheffield utd": "sheffield united", "sheff utd": "sheffield united",
    "sheffield wednesday": "sheffield weds", "sheff wed": "sheffield weds", "sheffield wed": "sheffield weds",
    "west bromwich albion": "west brom", "wba": "west brom",
    "queens park rangers": "qpr", "luton town": "luton", "norwich city": "norwich",
    "cardiff city": "cardiff", "swansea city": "swansea", "stoke city": "stoke", "hull city": "hull",
    "coventry city": "coventry", "birmingham city": "birmingham", "derby county": "derby",
    "preston north end": "preston", "plymouth argyle": "plymouth", "bristol city": "bristol city",
    "oxford united": "oxford", "oxford utd": "oxford", "blackburn rovers": "blackburn",
    "huddersfield town": "huddersfield", "peterborough united": "peterborough",
    "milton keynes dons": "mk dons", "afc wimbledon": "wimbledon", "wycombe wanderers": "wycombe",
    "bolton wanderers": "bolton", "charlton athletic": "charlton", "cambridge united": "cambridge",
    "exeter city": "exeter", "lincoln city": "lincoln", "northampton town": "northampton",
    "shrewsbury town": "shrewsbury", "stockport county": "stockport", "wigan athletic": "wigan",
    "burton albion": "burton", "crawley town": "crawley", "leyton orient": "leyton orient",
    "bristol rovers": "bristol rvs", "bristol rvs": "bristol rvs", "mansfield town": "mansfield",
    "rotherham united": "rotherham", "doncaster rovers": "doncaster", "barnsley": "barnsley",
    "accrington stanley": "accrington", "bradford city": "bradford", "carlisle united": "carlisle",
    "colchester united": "colchester", "crewe alexandra": "crewe", "gillingham": "gillingham",
    "grimsby town": "grimsby", "harrogate town": "harrogate", "newport county": "newport county",
    "notts county": "notts county", "port vale": "port vale", "salford city": "salford",
    "swindon town": "swindon", "tranmere rovers": "tranmere", "walsall": "walsall",
    "forest green rovers": "forest green", "sutton united": "sutton", "chesterfield": "chesterfield",
    "cheltenham town": "cheltenham", "fleetwood town": "fleetwood", "morecambe": "morecambe",
    "barrow": "barrow", "bromley": "bromley", "dagenham and redbridge": "dag and red",
    "dagenham redbridge": "dag and red", "dag red": "dag and red", "boreham wood": "boreham wood",
    "oldham athletic": "oldham", "york city": "york", "southend united": "southend",
    "hartlepool united": "hartlepool", "aldershot town": "aldershot", "eastleigh": "eastleigh",
    "ebbsfleet united": "ebbsfleet", "maidenhead united": "maidenhead", "solihull moors": "solihull",
    "woking": "woking", "yeovil town": "yeovil", "halifax town": "halifax", "fc halifax town": "halifax",
    "altrincham": "altrincham", "gateshead": "gateshead", "wealdstone": "wealdstone",
    "tamworth": "tamworth", "braintree town": "braintree", "rochdale": "rochdale",
    "afc fylde": "fylde", "fylde": "fylde", "dorking wanderers": "dorking", "kidderminster harriers": "kidderminster",
    "scunthorpe united": "scunthorpe", "truro city": "truro", "boston united": "boston",
    "hornchurch": "hornchurch", "brackley town": "brackley",
    # Spain
    "atletico madrid": "ath madrid", "atletico de madrid": "ath madrid", "atl madrid": "ath madrid",
    "athletic club": "ath bilbao", "athletic bilbao": "ath bilbao",
    "real betis": "betis", "real sociedad": "sociedad", "celta vigo": "celta", "rc celta": "celta",
    "deportivo alaves": "alaves", "rayo vallecano": "vallecano", "espanyol": "espanol",
    "real valladolid": "valladolid", "ud las palmas": "las palmas", "rcd mallorca": "mallorca",
    "cd leganes": "leganes", "real oviedo": "oviedo", "levante ud": "levante",
    # Italy
    "internazionale": "inter", "inter milan": "inter", "ac milan": "milan", "as roma": "roma",
    "hellas verona": "verona", "ss lazio": "lazio", "ssc napoli": "napoli", "parma calcio": "parma",
    # Germany
    "bayern munich": "bayern munich", "bayern munchen": "bayern munich", "fc bayern munchen": "bayern munich",
    "borussia dortmund": "dortmund", "bayer leverkusen": "leverkusen", "bayer 04 leverkusen": "leverkusen",
    "borussia monchengladbach": "m gladbach", "borussia m gladbach": "m gladbach", "gladbach": "m gladbach",
    "eintracht frankfurt": "ein frankfurt", "rasenballsport leipzig": "rb leipzig", "leipzig": "rb leipzig",
    "vfb stuttgart": "stuttgart", "vfl wolfsburg": "wolfsburg", "sc freiburg": "freiburg",
    "tsg hoffenheim": "hoffenheim", "1899 hoffenheim": "hoffenheim", "tsg 1899 hoffenheim": "hoffenheim",
    "werder bremen": "werder bremen", "sv werder bremen": "werder bremen", "fsv mainz 05": "mainz",
    "mainz 05": "mainz", "1 fsv mainz 05": "mainz", "fc augsburg": "augsburg", "union berlin": "union berlin",
    "1 fc union berlin": "union berlin", "fc heidenheim": "heidenheim", "1 fc heidenheim": "heidenheim",
    "fc st pauli": "st pauli", "holstein kiel": "holstein kiel", "vfl bochum": "bochum",
    "1 fc koln": "fc koln", "koln": "fc koln", "cologne": "fc koln", "hamburger sv": "hamburg",
    # France
    "paris saint germain": "paris sg", "psg": "paris sg", "olympique marseille": "marseille",
    "olympique de marseille": "marseille", "olympique lyonnais": "lyon", "as monaco": "monaco",
    "stade rennais": "rennes", "ogc nice": "nice", "rc lens": "lens", "losc lille": "lille",
    "stade brestois": "brest", "stade brestois 29": "brest", "rc strasbourg": "strasbourg",
    "stade de reims": "reims", "saint etienne": "st etienne", "as saint etienne": "st etienne",
    "fc nantes": "nantes", "aj auxerre": "auxerre", "le havre ac": "le havre", "angers sco": "angers",
    "paris fc": "paris fc", "fc lorient": "lorient", "fc metz": "metz", "toulouse fc": "toulouse",
    # Netherlands / Portugal / Belgium / Scotland
    "psv eindhoven": "psv", "feyenoord rotterdam": "feyenoord", "az alkmaar": "az alkmaar", "az": "az alkmaar",
    "fc twente": "twente", "fc utrecht": "utrecht", "sc heerenveen": "heerenveen", "nec nijmegen": "nijmegen",
    "nec": "nijmegen", "fc groningen": "groningen", "go ahead eagles": "go ahead eagles",
    "sporting cp": "sp lisbon", "sporting lisbon": "sp lisbon", "sporting": "sp lisbon",
    "sl benfica": "benfica", "fc porto": "porto", "sc braga": "sp braga", "braga": "sp braga",
    "vitoria guimaraes": "guimaraes", "vitoria sc": "guimaraes",
    "club brugge": "club brugge", "club brugge kv": "club brugge", "rsc anderlecht": "anderlecht",
    "krc genk": "genk", "royal antwerp": "antwerp", "standard liege": "standard",
    "union saint gilloise": "st gilloise", "union st gilloise": "st gilloise", "royale union saint gilloise": "st gilloise",
    "celtic glasgow": "celtic", "rangers fc": "rangers", "glasgow rangers": "rangers",
    "heart of midlothian": "hearts", "hibernian": "hibernian", "aberdeen": "aberdeen",
    "dundee united": "dundee united", "dundee utd": "dundee united", "st mirren": "st mirren",
    "motherwell": "motherwell", "kilmarnock": "kilmarnock", "st johnstone": "st johnstone",
    "ross county": "ross county", "dundee fc": "dundee", "livingston": "livingston", "falkirk": "falkirk",
}

_SUFFIXES = re.compile(r"\b(fc|afc|cf|sc|ac|ssc|fk|sv|bk|kv|ud|cd|rcd|vfl|vfb|tsg|ss|calcio)\b")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalise(name: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    s = _strip_accents(str(name)).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical(name: str) -> str:
    """Canonical team key used in match ids."""
    s = normalise(name)
    if s in ALIASES:
        return ALIASES[s]
    stripped = re.sub(r"\s+", " ", _SUFFIXES.sub(" ", s)).strip()
    if stripped in ALIASES:
        return ALIASES[stripped]
    return stripped or s


def best_match(name: str, candidates: list[str], cutoff: float = 0.78) -> str | None:
    """Map `name` onto one of `candidates` (canonical keys) or None."""
    key = canonical(name)
    if key in candidates:
        return key
    # containment helps with "brighton" vs "brighton hove albion"
    for c in candidates:
        if len(key) >= 4 and len(c) >= 4 and (key in c.split() or c in key.split()):
            return c
    hit = difflib.get_close_matches(key, candidates, n=1, cutoff=cutoff)
    return hit[0] if hit else None

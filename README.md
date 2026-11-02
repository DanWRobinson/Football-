# Footy Edge

A data-led toolkit for **fantasy football** and **betting** decisions. It pulls detailed stats (xG, xGA,
big chances, shots, PPDA, odds) for 13 leagues into a local database. It fits xG-based team models
and gives you a dashboard for:

| Decision | What you get |
|---|---|
| **Match bets** | 1X2, over/under, BTTS, clean sheet, Asian handicap and correct-score probabilities, fair odds, and margin-free market odds side by side |
| **Value finding** | Every selection where the model's probability × the best price > 1 + your edge threshold, with quarter-Kelly stake sizing |
| **Is the model any good here?** | A walk-forward backtest per league: log loss vs the closing market, ROI of the value strategy, calibration |
| **FPL** | Expected points per player for the next 1–8 gameweeks (handles doubles and blanks), xPts per £m, and a fixture ticker based on model attack/clean-sheet odds, not FPL's FDR |
| **Player props** | Anytime scorer, score-or-assist, 1+/2+ shots and shots on target, priced for upcoming fixtures |
| **Form & regression** | League table vs xG table (xPts, xPos), G−xG / GA−xGA over- and under-performance, rolling xG charts, home/away and last-N splits |

## Leagues

| Country | Leagues |
|---|---|
| England | Premier League, Championship, League One, League Two, National League |
| Big 5 | La Liga, Serie A, Bundesliga, Ligue 1 |
| Next tier | Eredivisie, Primeira Liga, Belgian Pro League, Scottish Premiership |
| Europe | Champions League, Europa League, Conference League |

**European competitions.** FotMob supplies their fixtures, results, xG and big chances; there are
no free odds, so type Bet365's price into the dashboard's **Match explorer** to see your edge. The
two teams usually come from different leagues, so every club is put on one common scale:

- **Clubs from the 13 leagues:** their domestic rating plus a league-strength offset. The offset
  starts from the league's average ClubElo and is corrected by actual European results.
- **Other clubs** (Galatasaray, Red Star and so on): their ClubElo rating.

The fitted league strengths appear on the League tables page when you pick a European competition.
The **FPL planner** flags (★) gameweeks where a Premier League team played in Europe 1–4 days
before, and cuts regular starters' start probability by 7% for that gameweek.

Add or remove leagues in `config/leagues.yaml`.

## LFC tickets (sale alerts, credits, Ticket Exchange)

Helps you be **ready and quick** for Liverpool ticket sales. It only reads the club's public
ticket pages. It never logs in, joins a queue, reserves or buys anything, so you always buy on
liverpoolfc.com yourself. (Auto-buying bots break the club's terms and can get a membership
cancelled. Reselling football tickets without the club's permission is a criminal offence in the UK.)

| What | How |
|---|---|
| **Sale-date alerts** | Every 15 minutes (06:00–midnight UTC) a GitHub Action reads the [tickets page](https://www.liverpoolfc.com/tickets) and each men's first-team match page. It sends a phone alert when a new sale date appears, then reminders 2 days, 3 hours and 30 minutes before each registration window, ballot, guaranteed sale or members' sale opens or closes. |
| **Credit tracker** | You keep your balances in `config/tickets.yaml`, copied from My Account, plus the games you've bought since. It shows your level (2026-27 Premier League home: **13+** guaranteed sale, **4–12** ballot for every game, **under 4** selected games only), how many games until the next level, and it **skips alerts for stages you don't qualify for**. |
| **Ticket Exchange alerts** | Sends an alert when a watched fixture's page changes to show Exchange tickets available (home games by default). The Action checks every 15 minutes. For faster checks, run `python -m footy tickets watch` on a laptop or Codespace (every 3 minutes by default, never faster than every 2). |
| **Calendar** | `lfc-tickets.ics` sits next to the daily page, and the **LFC tickets** tab has a subscribe link. Every sale date lands in your iPhone calendar with the same alarms. |

### One-time setup (about 5 minutes)

1. Install the free **ntfy** app ([iPhone](https://apps.apple.com/app/ntfy/id1625396347) / Android). Tap **+**
   and subscribe to a topic name nobody would guess, e.g. `lfc-dan-7f3k9q`. Anyone who knows the
   name can read the alerts, so treat it like a password.
2. On GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Name it
   `NTFY_TOPIC`, with that topic name as the value.
3. Edit `config/tickets.yaml`: copy your credit balances from My Account, and set `as_of` to today.
   After each game you buy for, add a line under `games:`, or just update the balances.
4. **Actions → LFC ticket alerts → Run workflow** to check it works. The first run records the
   current sale dates without alerting, so you won't get a flood of messages.

```bash
python -m footy tickets check        # one pass: read pages, send due alerts
python -m footy tickets list         # saved upcoming sale dates + whether you qualify
python -m footy tickets credits      # balances, level, games to next level
python -m footy tickets watch        # loop for Ticket Exchange alerts (default every 180s)
python -m footy tickets test-alert   # check your phone gets ntfy alerts (NTFY_TOPIC in .env)
python -m footy tickets doctor --url <page or saved .html>   # show exactly what a page parses to
```

**Caveats.**
- Match pages list each sale as a card: a status and date ("REGISTER SOON Fri 6 Nov 2026,
  11:00am"), a title saying who it's for ("…Premier League Home Credit Balance of 4 or more") and
  the details. The parser reads those cards, so it was tested on real pages from November 2026.
  If the club redesigns them, `doctor` shows what was found, and you get one alert if the site
  can't be read for about 2 hours.
- Cards for specific groups (an 'L' postcode, ages 17–24, wheelchair/ambulant seating) are skipped
  unless you add them under `alerts: audiences:` in `config/tickets.yaml`.
- I haven't seen a live Ticket Exchange listing on a match page yet, so Exchange alerts assume it
  appears as a card like the others. Run `doctor --url <match page>` when one is live to check.
- Credit-earning rules change (a multi-year loyalty scheme is being phased in from 2026-27), so
  My Account is always the source of truth. Update the thresholds in `config/tickets.yaml` if the
  club changes them.
- Ticket Exchange listings appear and go within minutes. The 15-minute Action is a safety net,
  not a guarantee; `watch` gets you closer.
- If your repo is public, so is `config/tickets.yaml`. It only holds credit counts.

## Data sources (pluggable)

| Source | Cost | Gives | Leagues |
|---|---|---|---|
| [football-data.co.uk](https://www.football-data.co.uk) | free | results, shots, SoT, corners, cards, **odds** (Pinnacle, average, max, Bet365, incl. closing) | all 13, down to the National League |
| [Understat](https://understat.com) | free | match xG, npxG, PPDA, deep completions; player xG, xA, npxG, xGChain, key passes | Big 5 |
| [FotMob](https://www.fotmob.com) | free | match xG, xGOT, **big chances**, big chances missed, touches in box; player big chances created | all, incl. EFL and next tier |
| [FPL API](https://fantasy.premierleague.com) | free | prices, ownership, FPL xG/xA, defensive contributions, fixtures, **injury flags**, match-by-match minutes per player | Premier League |
| [ClubElo](http://clubelo.com) | free | Elo rating for every club: used as each team's starting rating, so early-season and promoted-team ratings are sensible | top flights + most second tiers |
| [API-Football](https://www.api-football.com) | paid (free tier: 100 req/day) | match xG, shots, possession | all 13 |

Extra free data from the same sources:

- **Understat shot data:** xG split into open play, set pieces and penalties, plus chances of 0.3 xG or more.
- **FotMob team news and referees:** injured and suspended players and the referee appointment for fixtures in the next 8 days, plus referees for past matches.
- **football-data.co.uk referees:** recorded for English matches.

**Bet365** has no public API, and its terms ban scraping. Instead, its prices come from
football-data.co.uk, which records them for every league, both pre-match and closing. Value bets
are priced at Bet365 by default; use `--book best` or the dashboard toggle to see the best price
across all bookmakers.

**Team news.** For each team, the expected goals are reduced by the share of the team's xG + xA
that its missing players produce. It loses 60% of that share by default; the rest is assumed to be
covered by whoever replaces them. Doubtful players count half. Missing regular defenders and
goalkeepers slightly raise the expected goals conceded.

**How the merge works.** football-data.co.uk is the backbone: one row per match, with odds. The
other sources attach their stats to those rows. Team names are reconciled automatically ("Nott'm
Forest" = "Nottingham Forest"), and dates may differ by up to 3 days to allow for timezones. When
several sources give the same metric, the per-league priority in `leagues.yaml` (`xg_sources`)
decides which one wins.

**When there's no real xG** (mainly the National League, or when FotMob is unreachable), the tool
uses a **proxy xG** from shots and shots on target. The coefficients are fitted on leagues that have
both. The models weight proxy xG less than real xG, and the dashboard always shows which kind a
table is using.

To add a new provider, write a class in `footy/sources/` with `matches()`, `fixtures()` and/or
`players()` returning `MatchRecord`/`PlayerRecord`. Register it in `footy/sources/__init__.py` and
list it in a league's `xg_sources`.

## On an iPad or phone

There are two ways, and neither needs anything installed.

**1. Daily web page (bookmark it).** A GitHub Action refreshes every league each morning and
publishes one page with tabs for value bets, fixtures, xG tables, regression candidates, FPL picks,
players and data coverage. One-time setup:

1. On GitHub, open the repo's **Settings → Pages** and set **Source** to **GitHub Actions**.
2. Open the **Actions** tab, choose **Daily report**, then **Run workflow** to build it now.
3. When it finishes (the first run can take about an hour), the page is at
   `https://<your-username>.github.io/Football-/`. In Safari, use Share → **Add to Home Screen**.

Optional: add `API_FOOTBALL_KEY` or `FOTMOB_XMAS` under **Settings → Secrets and variables →
Actions**. The page also publishes the database itself (`/download/footy.db`) so the Codespace can
reuse it. Each run fetches a capped number of new FotMob matches, so xG and big-chance coverage
fills in over the first few days.

**2. Full interactive dashboard (Codespaces).** On the repo page, tap **Code → Codespaces →
Create codespace**. It installs everything, grabs the latest database from the daily page (or
fetches the Premier League if there isn't one yet) and opens the dashboard by itself. If it
doesn't, open the **Ports** tab and tap the globe icon next to 8501. Stop the codespace when you're
done (github.com/codespaces), because free hours are limited.

## Quick start

```bash
pip install -r requirements.txt

python -m footy doctor            # check which sources work from your network
python -m footy update            # all leagues, last + current season (first run: see note below)
python -m footy dashboard         # opens http://localhost:8501
```

Narrower updates:

```bash
python -m footy update -l england          # just the English pyramid
python -m footy update -l europe           # Champions/Europa/Conference League
python -m footy update -l big5 -s 2026     # big 5, current season only
python -m footy update -l E0,E1 --sources football_data,understat
python -m footy fpl                        # refresh FPL prices/injuries only
```

**The first run is slow.** FotMob needs one request per match for xG and big chances. That's
roughly 10 minutes per league at a polite rate. Results are cached forever once a match is
finished, so later updates only fetch new games. `FOTMOB_MAX_MATCHES` caps the number of requests
per run.

**Try it without any data:** `python -m footy demo && FOOTY_DB=data/demo.db python -m footy dashboard`
builds a database of *fictional* teams, so you can explore the dashboard offline.

### Command line

```bash
python -m footy status                     # coverage: matches, % with xG / big chances / odds per league
python -m footy ratings -l E0              # attack/defence ratings table
python -m footy predict -l all --days 7 --min-edge 0.05            # value at Bet365 prices
python -m footy predict -l E0 --book best                           # ...or best price anywhere
python -m footy elo                        # refresh ClubElo only
python -m footy backtest -l E0,E1,E2 --season 2025
python -m footy report                     # static HTML page -> site/index.html
```

### Optional keys (`.env`)

Copy `.env.example` to `.env`:

- `API_FOOTBALL_KEY`: turns on the paid adapter.
- `FOTMOB_XMAS`: only needed if FotMob starts returning 401/403. Copy the `x-mas` request header
  from your browser's dev tools on fotmob.com.

## How the models work

**Team ratings** (`footy/models/ratings.py`). This is a Poisson model with an attack and a defence
parameter per team plus home advantage, fitted per league:

- **Target:** a blend of xG and goals (default 70% xG). xG is a much more stable signal over 10–20
  games than goals.
- **Time decay:** older matches count for less (240-day half-life), and last season is included so
  early-season ratings aren't noise.
- **Shrinkage:** ratings are pulled toward a prior. Promoted teams start below average and relegated
  teams above, worked out automatically from where each team played last season.
- **Low scores:** a Dixon-Coles adjustment, fitted per league, corrects the probabilities of 0-0,
  1-0, 0-1 and 1-1.

You can change the decay, xG weight and shrinkage live in the dashboard sidebar.

**Value** (`footy/models/value.py`). Market probabilities have the margin removed with the power
method, which handles favourite–longshot bias. Pinnacle's prices are used when available because
they're the sharpest. Edge = p_model × price − 1, and stakes are fractional Kelly, capped.

**Backtest** (`footy/models/backtest.py`). The model is refitted every week using only data from
before that week. It's then scored against results and the margin-free **closing** odds. Bets are
placed at the pre-match *average* price, not the best price, to stay realistic. If the model's log
loss isn't close to the market's in a league, don't trust its value flags there. The market is very
efficient in the Premier League, less so in League Two or the National League.

**FPL** (`footy/models/fpl.py`). The expected-points calculation:

- **Attacking returns:** FPL's own xG/xA per 90, shrunk toward positional priors for players with
  few minutes, then scaled up or down by how many goals the team model expects that team to score
  in each fixture.
- **Clean sheets and goals conceded:** from the opponent's expected goals.
- **Minutes:** from start rate and the "chance of playing" flag.
- **Everything else:** 2025/26 scoring rules, including defensive contributions, saves and bonus.

**Props** (`footy/models/props.py`). A player's per-90 xG, shots and SoT are scaled by the fixture
multiplier and their typical minutes. Then P(anytime) = 1 − e^(−xG), and shots use Poisson tails.
Compare `fair_anytime` with the bookmaker's price.

## Caveats

- Free sources are scraped, so a site redesign can break an adapter. `python -m footy doctor` shows
  what's working, and `status` shows coverage. One broken source never stops the others.
- The models don't know about team news. Use the **Match explorer** to override expected goals by
  hand (say, for a missing striker).
- Player props use Poisson tails, which slightly understate the chance of big shot counts.
- Nothing here guarantees profit. Bookmakers limit winning accounts, and the closing line is usually
  smarter than any public model. Use the backtest to stay honest. Gamble responsibly:
  [BeGambleAware.org](https://www.begambleaware.org), 0808 8020 133.

## Project layout

```
config/leagues.yaml      leagues, provider ids, xG source priority, model defaults
footy/sources/           one adapter per provider (football_data, understat, fotmob, fpl, api_football)
footy/pipeline.py        fetch -> reconcile names/dates -> SQLite (data/footy.db)
footy/models/            data prep, ratings, probabilities, value, backtest, form, FPL, props
app/dashboard.py         Streamlit UI
footy/tickets/           LFC ticket sale alerts, credit tracker, Ticket Exchange alerts (config/tickets.yaml)
footy/report.py          static phone/iPad page (published daily by .github/workflows/daily-report.yml)
.devcontainer/           one-tap GitHub Codespaces setup
tests/                   parser tests on sample payloads, model recovery tests, dashboard smoke tests
```

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

Add or remove leagues in `config/leagues.yaml`.

## Data sources (pluggable)

| Source | Cost | Gives | Leagues |
|---|---|---|---|
| [football-data.co.uk](https://www.football-data.co.uk) | free | results, shots, SoT, corners, cards, **odds** (Pinnacle, average, max, Bet365, incl. closing) | all 13, down to the National League |
| [Understat](https://understat.com) | free | match xG, npxG, PPDA, deep completions; player xG, xA, npxG, xGChain, key passes | Big 5 |
| [FotMob](https://www.fotmob.com) | free | match xG, xGOT, **big chances**, big chances missed, touches in box; player big chances created | all, incl. EFL and next tier |
| [FPL API](https://fantasy.premierleague.com) | free | prices, ownership, FPL xG/xA, minutes, defensive contributions, fixtures | Premier League |
| [API-Football](https://www.api-football.com) | paid (free tier: 100 req/day) | match xG, shots, possession | all 13 |

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
python -m footy predict -l all --days 7 --min-edge 0.05
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
footy/report.py          static phone/iPad page (published daily by .github/workflows/daily-report.yml)
.devcontainer/           one-tap GitHub Codespaces setup
tests/                   parser tests on sample payloads, model recovery tests, dashboard smoke tests
```

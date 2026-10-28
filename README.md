# College Soccer Tracker

Live scores, results, fixtures, box scores and player stats for NCAA Division I and Division II men's soccer, in the spirit of SofaScore. Junior college (NJCAA) is planned next.

## How it works

Everything runs for free on GitHub; nothing needs to be installed.

1. **Every 10 minutes** a GitHub Action (`.github/workflows/update.yml`) asks NCAA.com's public scoreboard feed for yesterday's and today's games, and fetches box scores, goal scorers and play-by-play for live and newly finished games.
2. **Every night** it re-reads the whole season's schedule (Aug 15 – Dec 20) and fills in any missing box scores.
3. The data is kept in a SQLite database, saved compressed on the `data` branch between runs.
4. The scraper exports small JSON files, and the website in `site/` (plain HTML, CSS and JavaScript) is published to GitHub Pages together with them.

## Project layout

| Path | What it is |
|---|---|
| `scraper/ncaa.py` | Client for NCAA.com's data feed |
| `scraper/ingest.py` | Turns feed responses into database rows (games, teams, player box scores, match timeline) |
| `scraper/export.py` | Writes the JSON files the website reads |
| `scraper/__main__.py` | Commands: `season`, `live`, `backfill`, `export` |
| `site/` | The website |
| `tools/probe.py` | Diagnostic: saves raw responses from data sources to the `samples` branch |

## Running it by hand

On GitHub: **Actions → Update scores and website → Run workflow**. Tick "Refresh the whole season" to rebuild everything.

Locally (needs Python 3.11+):

```bash
pip install -r requirements.txt
python -m scraper season      # schedule and results for the season
python -m scraper live        # today's games
python -m scraper backfill    # box scores for finished games
python -m scraper export --out build/data && cp -r site/* build/
python -m http.server -d build
```

## If NCAA.com changes its feed

The feed addresses each query by a hash (see `HASHES` in `scraper/ncaa.py`). If runs start failing with `PersistedQueryNotFound`, open any game on ncaa.com with the browser's developer tools, find the request to `sdataprod.ncaa.com`, and copy the new `sha256Hash` value into `HASHES`.

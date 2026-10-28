"""Command line entry point.

    python -m scraper season    # refresh the whole season's schedule and results
    python -m scraper live      # refresh today's / yesterday's games, incl. box scores
    python -m scraper backfill --limit 200   # box scores for finished games missing them
    python -m scraper export    # write the website's JSON files
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from . import njcaa
from .export import export
from .ingest import fetch_details, ingest_scoreboard, needs_detail
from .ncaa import FeedError, NcaaClient

ET = ZoneInfo("America/New_York")
DIVISIONS = (1, 2)
log = logging.getLogger("scraper")
NJ: njcaa.NjcaaClient | None = None


def season_for(d: date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def season_dates(season: int, until: date | None = None):
    d, end = date(season, 8, 15), date(season, 12, 20)
    if until:
        end = min(end, until)
    while d <= end:
        yield d
        d += timedelta(days=1)


def refresh_dates(con, client, dates, divisions=DIVISIONS) -> int:
    n = 0
    for d in dates:
        for div in divisions:
            try:
                contests = client.scoreboard(div, d.strftime("%Y/%m/%d"), season_for(d))
            except FeedError as e:
                log.warning("scoreboard d%s %s: %s", div, d, e)
                continue
            n += ingest_scoreboard(con, contests, div, season_for(d))
    return n


def cmd_season(con, client, args):
    today = datetime.now(ET).date()
    season = args.season or season_for(today)
    n = refresh_dates(con, client, season_dates(season))
    log.info("season: %d contests refreshed", n)
    try:
        njcaa.ingest_season(con, NJ, season)
    except Exception:  # noqa: BLE001 - junior college must never break the NCAA update
        log.exception("njcaa season refresh failed")


def detail(con, client, row) -> bool:
    if (row["source"] or "ncaa") == "njcaa":
        return njcaa.fetch_details(con, NJ, row["id"])
    return fetch_details(con, client, row["id"])


def cmd_live(con, client, args):
    today = datetime.now(ET).date()
    dates = [today - timedelta(days=1), today]
    n = refresh_dates(con, client, dates)
    try:
        n += njcaa.ingest_recent(con, NJ, season_for(today))
    except Exception:  # noqa: BLE001
        log.exception("njcaa live refresh failed")
    rows = con.execute(
        f"SELECT * FROM games WHERE date IN ({','.join('?' * len(dates))})",
        [d.isoformat() for d in dates]).fetchall()
    todo = [r for r in rows if needs_detail(r)]
    for r in todo:
        detail(con, client, r)
    log.info("live: %d contests, %d box scores refreshed", n, len(todo))


def cmd_backfill(con, client, args):
    # NJCAA games whose box score isn't published yet are re-checked at most every 6 hours.
    rows = con.execute(
        "SELECT id, source FROM games WHERE status='final' AND (detail_state IS NULL OR detail_state!='F') "
        "AND NOT (detail_state='N' AND detail_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-6 hours')) "
        "AND NOT (source='njcaa' AND detail_state='N' AND date < date('now', '-10 days')) "
        "ORDER BY date DESC LIMIT ?", (args.limit,)).fetchall()
    started = time.monotonic()
    done = 0
    for r in rows:
        if args.max_minutes and time.monotonic() - started > args.max_minutes * 60:
            break
        done += detail(con, client, r)
    left = con.execute("SELECT COUNT(*) FROM games WHERE status='final' AND "
                       "(detail_state IS NULL)").fetchone()[0]
    log.info("backfill: %d games filled, %d still missing", done, left)


def main():
    ap = argparse.ArgumentParser(prog="scraper")
    ap.add_argument("command", choices=["season", "live", "backfill", "export"])
    ap.add_argument("--db", default="db/soccer.sqlite")
    ap.add_argument("--out", default="build/data")
    ap.add_argument("--season", type=int)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--max-minutes", type=float, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    con = db.connect(args.db)
    client = NcaaClient()
    global NJ
    NJ = njcaa.NjcaaClient()
    if args.command == "export":
        export(con, args.out)
    else:
        {"season": cmd_season, "live": cmd_live, "backfill": cmd_backfill}[args.command](con, client, args)
        log.info("%d NCAA and %d NJCAA requests made", client.requests_made, NJ.requests_made)
    con.close()


if __name__ == "__main__":
    main()

"""SQLite storage. One file holds everything: teams, games, player box scores."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    seo            TEXT PRIMARY KEY,      -- NCAA.com slug, e.g. 'north-carolina'
    ncaa_id        TEXT,
    name_short     TEXT,
    name_full      TEXT,
    nickname       TEXT,
    color          TEXT,
    division       INTEGER,
    conference     TEXT,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS games (
    id             INTEGER PRIMARY KEY,   -- NCAA contest id
    season         INTEGER,
    division       INTEGER,
    date           TEXT,                  -- YYYY-MM-DD (US date)
    start_epoch    INTEGER,
    has_time       INTEGER,
    status         TEXT,                  -- pre | live | final | postponed | canceled ...
    state          TEXT,                  -- raw NCAA gameState (P, I, F, O)
    period         TEXT,
    clock          TEXT,
    home           TEXT REFERENCES teams(seo),
    away           TEXT REFERENCES teams(seo),
    home_score     INTEGER,
    away_score     INTEGER,
    home_rank      INTEGER,
    away_rank      INTEGER,
    home_record    TEXT,
    away_record    TEXT,
    winner         TEXT,
    neutral        INTEGER,
    venue          TEXT,
    city           TEXT,
    state_usps     TEXT,
    round          TEXT,                  -- tournament round, if any
    linescore      TEXT,                  -- JSON
    team_stats     TEXT,                  -- JSON {home: {...}, away: {...}}
    timeline       TEXT,                  -- JSON list of events (goals, cards, subs)
    pbp            TEXT,                  -- JSON play-by-play
    detail_state   TEXT,                  -- state when details were last fetched
    detail_at      TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS games_date ON games(date);
CREATE INDEX IF NOT EXISTS games_home ON games(home);
CREATE INDEX IF NOT EXISTS games_away ON games(away);

CREATE TABLE IF NOT EXISTS player_games (
    game_id        INTEGER REFERENCES games(id),
    team           TEXT REFERENCES teams(seo),
    player         TEXT,                  -- player key: team seo + name slug
    first_name     TEXT,
    last_name      TEXT,
    number         TEXT,
    position       TEXT,
    starter        INTEGER,
    played         INTEGER,
    minutes        INTEGER,
    goals          INTEGER,
    assists        INTEGER,
    shots          INTEGER,
    shots_on_goal  INTEGER,
    pk_goals       INTEGER,
    pk_attempts    INTEGER,
    gw_goals       INTEGER,
    fouls          INTEGER,
    yellow         INTEGER,
    red            INTEGER,
    saves          INTEGER,
    goals_allowed  INTEGER,
    is_keeper      INTEGER,
    PRIMARY KEY (game_id, team, player)
);
CREATE INDEX IF NOT EXISTS pg_player ON player_games(player);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=OFF")
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, table: str, row: dict, key: tuple[str, ...]) -> None:
    """Insert or update, leaving columns not present in `row` untouched."""
    cols = list(row)
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in key)
    sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({','.join(key)}) DO " + (f"UPDATE SET {updates}" if updates else "NOTHING"))
    con.execute(sql, [row[c] for c in cols])

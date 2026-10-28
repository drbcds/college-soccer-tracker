"""Turn NCAA feed responses into rows in the database."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

from .db import upsert
from .ncaa import FeedError, NcaaClient

log = logging.getLogger(__name__)

STATE_STATUS = {"P": "pre", "I": "live", "F": "final", "O": "postponed"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def to_int(v):
    if v in (None, "", "-"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def team_seo(t: dict) -> str:
    return t.get("seoname") or slug(t.get("nameShort") or t.get("nameFull") or "unknown")


def norm_name(text: str) -> str:
    """'McErlain, Connall' -> 'Connall McErlain'."""
    text = text.strip().rstrip(".").strip()
    if "," in text:
        last, first = [p.strip() for p in text.split(",", 1)]
        return f"{first} {last}".strip()
    return text


def clock_minute(clock: str | None) -> int | None:
    """'04:51' -> 5 (soccer minute convention)."""
    if not clock or ":" not in clock:
        return None
    mm, ss = clock.split(":")[:2]
    try:
        m, s = int(mm), int(ss)
    except ValueError:
        return None
    return m + 1 if (m or s) else 0


# ---------------------------------------------------------------- scoreboard
def ingest_scoreboard(con, contests: list[dict], division: int, season: int) -> int:
    stamp = now()
    for c in contests:
        teams = c.get("teams") or []
        home = next((t for t in teams if t.get("isHome")), teams[0] if teams else {})
        away = next((t for t in teams if t is not home), {})
        for t in (home, away):
            if not t:
                continue
            con.execute(
                "INSERT INTO teams (seo, name_short, conference, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(seo) DO UPDATE SET name_short=COALESCE(teams.name_short, excluded.name_short), "
                "conference=COALESCE(excluded.conference, teams.conference), updated_at=excluded.updated_at",
                (team_seo(t), (t.get("nameShort") or "").strip(), t.get("conferenceSeo"), stamp))
        state = c.get("gameState")
        status = (c.get("statusCodeDisplay") or STATE_STATUS.get(state, "")).lower()
        winner = None
        if home.get("isWinner"):
            winner = team_seo(home)
        elif away.get("isWinner"):
            winner = team_seo(away)
        m, d, y = (c.get("startDate") or "//").split("/")
        upsert(con, "games", {
            "id": int(c["contestId"]),
            "season": season,
            "division": division,
            "date": f"{y}-{m}-{d}" if y else None,
            "start_epoch": to_int(c.get("startTimeEpoch")),
            "has_time": 1 if c.get("hasStartTime") else 0,
            "status": status,
            "state": state,
            "period": c.get("currentPeriod") or "",
            "clock": c.get("contestClock") or "",
            "home": team_seo(home),
            "away": team_seo(away),
            "home_score": home.get("score"),
            "away_score": away.get("score"),
            "home_rank": home.get("teamRank"),
            "away_rank": away.get("teamRank"),
            "winner": winner,
            "round": c.get("roundDescription") or None,
            "updated_at": stamp,
        }, ("id",))
    con.commit()
    return len(contests)


# ---------------------------------------------------------------- details
def needs_detail(row) -> bool:
    """Should we (re)fetch box score / timeline for this game?"""
    if row["status"] == "live":
        return True
    if row["status"] == "final":
        return row["detail_state"] != "F"
    return False


def fetch_details(con, client: NcaaClient, game_id: int) -> bool:
    try:
        game = client.game(game_id)
        box = client.boxscore(game_id)
        scoring = client.scoring(game_id)
        pbp = client.pbp(game_id)
    except FeedError as e:
        log.warning("game %s: %s", game_id, e)
        return False
    if not game:
        return False

    teams = {str(t.get("teamId")): t for t in game.get("teams") or []}
    by_home = {bool(t.get("isHome")): team_seo(t) for t in teams.values()}
    stamp = now()

    for tid, t in teams.items():
        con.execute(
            "INSERT INTO teams (seo, ncaa_id, name_short, name_full, color, division, updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(seo) DO UPDATE SET ncaa_id=excluded.ncaa_id, "
            "name_short=excluded.name_short, name_full=excluded.name_full, color=excluded.color, "
            "division=excluded.division, updated_at=excluded.updated_at",
            (team_seo(t), tid, (t.get("nameShort") or "").strip(), t.get("nameFull"),
             t.get("color"), t.get("division"), stamp))
    if box:
        for t in box.get("teams") or []:
            if t.get("teamName"):
                con.execute("UPDATE teams SET nickname=? WHERE seo=?", (t["teamName"], team_seo(t)))

    home_t = next((t for t in teams.values() if t.get("isHome")), {})
    away_t = next((t for t in teams.values() if not t.get("isHome")), {})
    loc = game.get("location") or {}
    state = game.get("gameState")
    status = (game.get("statusCodeDisplay") or STATE_STATUS.get(state, "")).lower()

    # ---- player box scores
    players_by_name: dict[str, str] = {}   # 'connall mcerlain' -> team seo
    proper_names: dict[str, str] = {}      # 'connall mcerlain' -> 'Connall McErlain'
    team_stats = {}
    if box:
        con.execute("DELETE FROM player_games WHERE game_id=?", (game_id,))
        for tb in box.get("teamBoxscore") or []:
            t = teams.get(str(tb.get("teamId")))
            if not t:
                continue
            seo = team_seo(t)
            side = "home" if t.get("isHome") else "away"
            team_stats[side] = tb.get("teamStats")
            for p in tb.get("playerStats") or []:
                first, last = (p.get("firstName") or "").strip(), (p.get("lastName") or "").strip()
                if not (first or last):
                    continue
                name = f"{first} {last}".strip()
                players_by_name[name.lower()] = seo
                proper_names[name.lower()] = name
                pen = p.get("penalties") or {}
                gt = p.get("goalTypes") or {}
                pos = (p.get("position") or "").upper()
                upsert(con, "player_games", {
                    "game_id": game_id, "team": seo, "player": f"{seo}--{slug(name)}",
                    "first_name": first, "last_name": last,
                    "number": str(p.get("number") if p.get("number") is not None else ""),
                    "position": pos, "starter": 1 if p.get("starter") else 0,
                    "played": 1 if p.get("participated") else 0,
                    "minutes": to_int(p.get("minutesPlayed")), "goals": to_int(p.get("goals")) or 0,
                    "assists": to_int(p.get("assists")) or 0, "shots": to_int(p.get("shots")) or 0,
                    "shots_on_goal": to_int(p.get("shotsOnGoal")) or 0,
                    "pk_goals": to_int(p.get("penaltyShotGoals")) or 0,
                    "pk_attempts": to_int(p.get("penaltyShotAttempts")) or 0,
                    "gw_goals": to_int(gt.get("gameWinningGoals")) or 0,
                    "fouls": to_int(pen.get("fouls")) or 0, "yellow": to_int(pen.get("yellowCards")) or 0,
                    "red": to_int(pen.get("redCards")) or 0, "saves": to_int(p.get("saves")),
                    "goals_allowed": to_int(p.get("goalsAllowed")),
                    "is_keeper": 1 if "GOAL" in pos else 0,
                }, ("game_id", "team", "player"))

    timeline = build_timeline(scoring, pbp, teams, players_by_name)
    for e in timeline:  # use box-score spelling for names (scoring summary lowercases some)
        for k in ("player", "assist", "off"):
            if e.get(k):
                e[k] = proper_names.get(e[k].lower(), e[k])
    pbp_slim = slim_pbp(pbp, teams)

    upsert(con, "games", {
        "id": game_id,
        "status": status, "state": state,
        "period": game.get("currentPeriod") or "",
        "clock": game.get("clock") or "",
        "home_score": home_t.get("score"), "away_score": away_t.get("score"),
        "home_record": (home_t.get("record") or "").strip("()") or None,
        "away_record": (away_t.get("record") or "").strip("()") or None,
        "venue": loc.get("venue"), "city": loc.get("city"), "state_usps": loc.get("stateUsps"),
        "linescore": json.dumps(game.get("linescores") or []),
        "team_stats": json.dumps(team_stats),
        "timeline": json.dumps(timeline),
        "pbp": json.dumps(pbp_slim),
        "detail_state": state, "detail_at": stamp, "updated_at": stamp,
    }, ("id",))
    if by_home:
        con.execute("UPDATE games SET home=COALESCE(?, home), away=COALESCE(?, away) WHERE id=?",
                    (by_home.get(True), by_home.get(False), game_id))
    con.commit()
    return True


SUB_RE = re.compile(r"^(?P<abbr>[A-Z0-9&.' -]+?) substitution:\s*(?P<on>.+?) for (?P<off>.+?)\.?$")
GOALIE_RE = re.compile(r"^(?P<name>.+?) at goalie for (?P<abbr>.+?)\.?$")
CARD_RE = re.compile(r"(?P<color>yellow|red|green)\s+card", re.I)


def build_timeline(scoring, pbp, teams, players_by_name) -> list[dict]:
    seo_by_id = {tid: team_seo(t) for tid, t in teams.items()}
    events: list[dict] = []

    # Goals: the scoring summary is the most reliable source.
    for per in (scoring or {}).get("periods") or []:
        for s in per.get("summary") or []:
            text = s.get("scoreText") or ""
            scorer, assist = text, None
            m = re.match(r"^(.*?)\s*\((.*)\)\s*$", text)
            if m:
                scorer, extra = m.group(1), m.group(2)
                a = re.search(r"Assist(?:ed)? by (.+)", extra, re.I)
                assist = a.group(1).strip() if a else None
            events.append({
                "type": "goal", "minute": clock_minute(s.get("time")), "clock": s.get("time"),
                "team": seo_by_id.get(str(s.get("teamId"))), "player": scorer.strip(),
                "assist": assist, "score": [to_int(s.get("homeScore")), to_int(s.get("visitScore"))],
                "note": (s.get("scoreType") or "").lower() if (s.get("scoreType") or "GOAL") != "GOAL" else None,
            })

    # Cards and substitutions come from play-by-play text.
    abbr_team: dict[str, str] = {}
    plays = []
    for per in (pbp or {}).get("periods") or []:
        for blk in per.get("playbyplayStats") or []:
            for pl in blk.get("plays") or []:
                plays.append((blk.get("teamId"), pl))
    for tid, pl in plays:
        m = GOALIE_RE.match(pl.get("playText") or "")
        if m and str(tid) in seo_by_id:
            abbr_team.setdefault(m.group("abbr").strip(), seo_by_id[str(tid)])

    def team_for(name: str, abbr: str | None, tid) -> str | None:
        if name and name.lower() in players_by_name:
            return players_by_name[name.lower()]
        if abbr and abbr in abbr_team:
            return abbr_team[abbr]
        return seo_by_id.get(str(tid))

    for tid, pl in plays:
        text = (pl.get("playText") or "").strip()
        clock = pl.get("clock")
        m = SUB_RE.match(text)
        if m:
            on, off = norm_name(m.group("on")), norm_name(m.group("off"))
            events.append({"type": "sub", "minute": clock_minute(clock), "clock": clock,
                           "team": team_for(on, m.group("abbr").strip(), tid),
                           "player": on, "off": off})
            continue
        m = CARD_RE.search(text)
        if m:
            who = re.search(r"\bon (?:[A-Z]{2,5} )?(.+?)(?:\s*\[|\.?$)", text)
            name = norm_name(who.group(1)) if who else ""
            events.append({"type": m.group("color").lower(), "minute": clock_minute(clock),
                           "clock": clock, "team": team_for(name, None, tid), "player": name})

    def sort_key(e):
        c = e.get("clock") or "00:00"
        try:
            mm, ss = c.split(":")[:2]
            return int(mm) * 60 + int(ss)
        except ValueError:
            return 0
    events.sort(key=sort_key)
    return events


def slim_pbp(pbp, teams) -> list[dict]:
    """Compact play-by-play: [{period, clock, team, text}]."""
    if not pbp:
        return []
    seo_by_id = {tid: team_seo(t) for tid, t in teams.items()}
    out = []
    for per in pbp.get("periods") or []:
        label = per.get("periodDisplay") or str(per.get("periodNumber"))
        for blk in per.get("playbyplayStats") or []:
            for pl in blk.get("plays") or []:
                text = (pl.get("playText") or "").strip()
                if text:
                    out.append({"p": label, "c": pl.get("clock") or blk.get("clock") or "",
                                "t": seo_by_id.get(str(blk.get("teamId"))), "x": text})
    return out

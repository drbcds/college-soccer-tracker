"""NJCAA (junior college) men's soccer.

Source: the public GraphQL API behind njcaa.org (Ardor SportsHub). It lists
every NJCAA team with division and region, each team's schedule, results and
box scores (goals with minute, scorer and assists; cards with minute; shots,
shots on goal and saves). Field players' minutes are not published.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime

import requests

from .db import upsert
from .ingest import clock_minute, nice, now, slug, to_int

log = logging.getLogger(__name__)

ENDPOINT = "https://public-graphql-api.ardorsportshub.com/public/graphql"
NJCAA_TENANT = "404933495157163011"
SPORT, SPORT_ID, GENDER_ID = "msoc", "14", "1"
DIVISIONS = {"DI": "J1", "DII": "J2", "DIII": "J3"}
POSITIONS = {"GK": "GOALKEEPER", "G": "GOALKEEPER", "D": "DEFENDER", "CB": "DEFENDER", "LB": "DEFENDER",
             "RB": "DEFENDER", "WB": "DEFENDER", "M": "MIDFIELDER", "CM": "MIDFIELDER", "CAM": "MIDFIELDER",
             "CDM": "MIDFIELDER", "DM": "MIDFIELDER", "AM": "MIDFIELDER", "LM": "MIDFIELDER", "RM": "MIDFIELDER",
             "W": "FORWARD", "LW": "FORWARD", "RW": "FORWARD", "F": "FORWARD", "FW": "FORWARD", "ST": "FORWARD",
             "CF": "FORWARD"}

GAME_FIELDS = """gameId: id date time scheduledAt timezone homeTeamName awayTeamName homeScore awayScore
  status location sport metadata hasBoxscore boxscoreGameId gameType eventTypeName isPostseason isTournament"""

TEAMS_Q = """query($t: ID!, $sport: String!, $season: String!) {
  listTeams(tenantId: $t, limit: 5000, sport: $sport, season: $season) {
    id tenantId schoolName officialSchoolName displayName name regionName logo { url } metadata } }"""
SCHEDULE_Q = """query($t: ID!, $sport: String, $season: String, $limit: Int) {
  schedule(tenantId: $t, sport: $sport, season: $season, limit: $limit) { games { %s } } }""" % GAME_FIELDS
TEAM_SCHEDULE_Q = """query($t: ID!, $team: ID!, $sportId: ID!, $seasonId: ID!, $genderId: ID, $limit: Int) {
  schedule(tenantId: $t, teamSeasonId: $team, sportId: $sportId, seasonId: $seasonId, genderId: $genderId, limit: $limit) {
    games { %s } } }""" % GAME_FIELDS
BOX_Q = """query($t: ID!, $g: ID!) {
  getGame(tenantId: $t, id: $g) { id date time scheduledAt homeTeamName awayTeamName homeScore awayScore status location }
  gameBoxscoreDetails(tenantId: $t, gameId: $g) {
    gameId game hasBoxscore teams { homeAway name score periods stats }
    players { playerId number name position team stats } } }"""


class NjcaaClient:
    def __init__(self, min_interval: float = 0.3):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36",
            "Origin": "https://www.njcaa.org", "Referer": "https://www.njcaa.org/",
        })
        self.min_interval = min_interval
        self._last = 0.0
        self.requests_made = 0

    def q(self, query: str, variables: dict) -> dict:
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.post(ENDPOINT, json={"query": query, "variables": variables}, timeout=60)
                self.requests_made += 1
            except requests.RequestException as e:
                log.warning("njcaa request failed (%s), retrying", e)
                time.sleep(2 ** attempt + random.random())
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 2 + random.random())
                continue
            r.raise_for_status()
            body = r.json()
            if body.get("errors") and not body.get("data"):
                raise RuntimeError(body["errors"][0].get("message"))
            return body.get("data") or {}
        raise RuntimeError("njcaa: gave up after retries")


# ------------------------------------------------------------------ names
GENERIC = {"community", "college", "junior", "cc", "jc", "the", "of", "at", "technical", "tech",
           "and", "institute", "school", "district", "county"}


def short_name(full: str) -> str:
    s = re.sub(r"\s+Community College$", " CC", full.strip())
    s = re.sub(r"\s+Junior College$", " JC", s)
    s = re.sub(r"^(The )", "", s)
    return s


def name_key(name: str) -> str:
    words = [w for w in slug(name).split("-") if w and w not in GENERIC]
    return "-".join(words)


def team_seo(full: str) -> str:
    return "nj-" + slug(short_name(full))


def season_label(season: int) -> str:
    return f"{season}-{str(season + 1)[-2:]}"


PLACEHOLDER = re.compile(
    r"^(tba|tbd|bye|winner|loser|seed|game \d|#?\d+ seed|region\b|district\b|njcaa\b|semi|quarter|final|championship|"
    r"first round|second round|east|west|north|south|opponent|home team|away team)", re.I)


def is_placeholder(name: str) -> bool:
    n = (name or "").strip()
    return not n or bool(PLACEHOLDER.match(n)) or "tournament" in n.lower() or "bracket" in n.lower()


class TeamIndex:
    """Resolve the team names/ids that appear in games to our team slugs."""

    def __init__(self, con):
        self.by_ts: dict[str, str] = {}
        self.by_key: dict[str, str | None] = {}
        for r in con.execute("SELECT seo, ncaa_id, name_short, name_full FROM teams WHERE seo LIKE 'nj-%'"):
            if r["ncaa_id"]:
                self.by_ts[str(r["ncaa_id"])] = r["seo"]
            for n in (r["name_short"], r["name_full"]):
                if n:
                    k = name_key(n)
                    self.by_key[k] = r["seo"] if self.by_key.get(k, r["seo"]) == r["seo"] else None

    def resolve(self, con, team_season_id, name: str) -> str | None:
        """Our team slug, or None for a bracket placeholder ('TBA', 'Semifinal', 'Region 12 Champion')."""
        if team_season_id and str(team_season_id) in self.by_ts:
            return self.by_ts[str(team_season_id)]
        k = name_key(name or "")
        if k and self.by_key.get(k):
            return self.by_key[k]
        if is_placeholder(name):
            return None
        # Opponent outside the NJCAA (NAIA, NCAA, club...): keep a placeholder team.
        seo = team_seo(name or "unknown")
        con.execute("INSERT INTO teams (seo, name_short, name_full, assoc, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(seo) DO NOTHING", (seo, short_name(name or "Unknown"), name, "other", now()))
        self.by_key.setdefault(k, seo)
        return seo


# ------------------------------------------------------------------ teams
def ingest_teams(con, client: NjcaaClient, season: int) -> list[dict]:
    data = client.q(TEAMS_Q, {"t": NJCAA_TENANT, "sport": SPORT, "season": season_label(season)})
    teams = data.get("listTeams") or []
    stamp = now()
    for t in teams:
        md = t.get("metadata") or {}
        if isinstance(md, str):
            md = json.loads(md or "{}")
        full = t.get("officialSchoolName") or t.get("displayName") or t.get("schoolName") or t.get("name")
        upsert(con, "teams", {
            "seo": team_seo(full), "ncaa_id": str(t["id"]), "name_short": short_name(t.get("displayName") or full),
            "name_full": full, "division": DIVISIONS.get(md.get("division"), "J"),
            "conference": t.get("regionName") or md.get("regionName"), "assoc": "njcaa",
            "logo": (t.get("logo") or {}).get("url") or md.get("logo"), "school_tenant": t.get("tenantId"),
            "updated_at": stamp,
        }, ("seo",))
    con.commit()
    log.info("njcaa: %d teams", len(teams))
    return teams


# ------------------------------------------------------------------ games
STATUS = {"final": "final", "forfeited": "final", "forfeit": "final", "scheduled": "pre",
          "in_progress": "live", "in progress": "live", "live": "live", "halftime": "live",
          "postponed": "postponed", "canceled": "canceled", "cancelled": "canceled", "suspended": "postponed"}


def _epoch(g: dict) -> tuple[int | None, int]:
    at = g.get("scheduledAt") or (g.get("metadata") or {}).get("datetime")
    if at:
        try:
            return int(datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()), 1 if g.get("time") else 0
        except ValueError:
            pass
    if g.get("date"):
        return int(datetime.fromisoformat(g["date"] + "T16:00:00+00:00").timestamp()), 0
    return None, 0


def _round(g: dict, md: dict) -> str | None:
    name = (g.get("eventTypeName") or md.get("eventTypeName") or "").strip()
    if name.lower() in ("", "standard game", "tournament game", "bracket game", "regular season"):
        name = ""
    if g.get("isPostseason") or md.get("isPostseason") or md.get("isPlayoff") or md.get("isChampionship"):
        return name or "Postseason"
    return name or None


def ingest_games(con, games: list[dict], idx: TeamIndex, season: int) -> int:
    stamp = now()
    n = 0
    for g in games:
        md = g.get("metadata") or {}
        if isinstance(md, str):
            md = json.loads(md or "{}")
        date = g.get("date") or md.get("date")
        # Fall season only (spring friendlies are listed in the same season label).
        if not date or not (f"{season}-07-01" <= date <= f"{season}-12-31"):
            continue
        if (md.get("entryKind") or "game") != "game":
            continue
        gid = int(md.get("boxscoreGameId") or g.get("boxscoreGameId") or g["gameId"])
        home = idx.resolve(con, md.get("homeTeamSeasonId"), g.get("homeTeamName") or md.get("homeTeamName"))
        away = idx.resolve(con, md.get("awayTeamSeasonId"), g.get("awayTeamName") or md.get("awayTeamName"))
        if home == away:
            continue
        known = {r["seo"] for r in con.execute(
            "SELECT seo FROM teams WHERE assoc='njcaa' AND seo IN (?,?)", (home or "", away or ""))}
        if home is None or away is None or len(known) < 2:
            # One side isn't an NJCAA team we know. If the known team already has a game that day,
            # this is the other school's copy with an unrecognised name ('East' for East Mississippi).
            other = known.pop() if len(known) == 1 else None
            if other and con.execute("SELECT 1 FROM games WHERE date=? AND (home=? OR away=?) AND id!=? AND source='njcaa'",
                                     (date, other, other, gid)).fetchone():
                continue
            if home is None or away is None:
                if (g.get("status") or "").lower() not in ("scheduled", "") or date < datetime.utcnow().strftime("%Y-%m-%d"):
                    continue
                home, away = home or "tbd", away or "tbd"
                con.execute("INSERT INTO teams (seo, name_short, assoc) VALUES ('tbd','TBD','other') ON CONFLICT(seo) DO NOTHING")
        status = STATUS.get((g.get("status") or "").strip().lower(), (g.get("status") or "").lower() or "pre")
        hs, as_ = to_int(g.get("homeScore")), to_int(g.get("awayScore"))
        if status == "pre" and hs is not None and as_ is not None and date < datetime.utcnow().strftime("%Y-%m-%d"):
            status = "final"
        winner = None
        if status == "final" and hs is not None and as_ is not None and hs != as_:
            winner = home if hs > as_ else away

        # The same match can be entered by both schools under different ids: keep one.
        dup = con.execute(
            "SELECT id, detail_state FROM games WHERE date=? AND ((home=? AND away=?) OR (home=? AND away=?)) AND id!=? AND source='njcaa'",
            (date, home, away, away, home, gid)).fetchone()
        if dup:
            has_box = g.get("hasBoxscore") or md.get("hasBoxscore")
            if dup["detail_state"] == "F" or not has_box:
                continue
            con.execute("DELETE FROM games WHERE id=?", (dup["id"],))
            con.execute("DELETE FROM player_games WHERE game_id=?", (dup["id"],))

        div = con.execute("SELECT division FROM teams WHERE seo=?", (home,)).fetchone()
        div2 = con.execute("SELECT division FROM teams WHERE seo=?", (away,)).fetchone()
        division = (div and div["division"]) or (div2 and div2["division"]) or "J"
        epoch, has_time = _epoch(g)
        upsert(con, "games", {
            "id": gid, "season": season, "division": division, "date": date, "start_epoch": epoch,
            "has_time": has_time, "status": status, "state": g.get("status"), "period": "", "clock": "",
            "home": home, "away": away, "home_score": hs, "away_score": as_, "winner": winner,
            "venue": g.get("location") or md.get("location"),
            "round": _round(g, md),
            "source": "njcaa", "has_box": 1 if (g.get("hasBoxscore") or md.get("hasBoxscore")) else 0,
            "updated_at": stamp,
        }, ("id",))
        n += 1
    con.commit()
    return n


def ingest_season(con, client: NjcaaClient, season: int) -> int:
    teams = ingest_teams(con, client, season)
    idx = TeamIndex(con)
    total = 0
    for t in teams:
        md = t.get("metadata") or {}
        if isinstance(md, str):
            md = json.loads(md or "{}")
        variables = {"team": str(t["id"]), "sportId": md.get("sportId") or SPORT_ID,
                     "seasonId": md.get("seasonId"), "genderId": md.get("genderId") or GENDER_ID, "limit": 200}
        games = []
        for tenant in (NJCAA_TENANT, t.get("tenantId")):
            if not tenant:
                continue
            try:
                games = (client.q(TEAM_SCHEDULE_Q, {"t": str(tenant), **variables}).get("schedule") or {}).get("games") or []
            except Exception as e:  # noqa: BLE001
                log.warning("njcaa schedule %s: %s", t.get("displayName"), e)
            if games:
                break
        total += ingest_games(con, games, idx, season)
    log.info("njcaa season: %d game entries from %d teams", total, len(teams))
    return total


def ingest_recent(con, client: NjcaaClient, season: int) -> int:
    """The association-wide schedule lists recent and upcoming games."""
    if not con.execute("SELECT 1 FROM teams WHERE assoc='njcaa' LIMIT 1").fetchone():
        ingest_teams(con, client, season)
    data = client.q(SCHEDULE_Q, {"t": NJCAA_TENANT, "sport": SPORT, "season": season_label(season), "limit": 500})
    games = (data.get("schedule") or {}).get("games") or []
    return ingest_games(con, games, TeamIndex(con), season)


# ------------------------------------------------------------------ box scores
def _stat(stats: dict, *keys):
    for k in keys:
        cur = stats
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, ""):
            return to_int(cur)
    return None


def fetch_details(con, client: NjcaaClient, game_id: int) -> bool:
    row = con.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not row:
        return False
    try:
        data = client.q(BOX_Q, {"t": NJCAA_TENANT, "g": str(game_id)})
    except Exception as e:  # noqa: BLE001
        log.warning("njcaa box %s: %s", game_id, e)
        return False
    box = data.get("gameBoxscoreDetails") or {}
    game = data.get("getGame") or {}
    side_seo = {"H": row["home"], "V": row["away"], "home": row["home"], "away": row["away"]}
    stamp = now()

    status = STATUS.get((game.get("status") or "").lower(), row["status"])
    hs, as_ = to_int(game.get("homeScore")), to_int(game.get("awayScore"))
    if hs is None:
        hs, as_ = row["home_score"], row["away_score"]

    detail = box.get("game") or {}
    if isinstance(detail, str):
        detail = json.loads(detail or "{}")
    sections = {s.get("code"): s.get("rows") or [] for s in ((detail.get("competition") or {}).get("sections") or [])}

    # Event rows reference roster-member ids; map them to the player ids used in the box score.
    pdetails = detail.get("playerDetails") or []
    rm_to_pid = {str(x.get("rosterMemberId")): str(x.get("playerId")) for x in pdetails if x.get("rosterMemberId")}
    part = {str(x.get("playerId")): (x.get("participation") or "").lower() for x in pdetails}
    pos_by_pid = {str(x.get("playerId")): x.get("position") for x in pdetails}

    def pid(v):
        v = str(v or "")
        return rm_to_pid.get(v, v)

    cards: dict[str, dict] = {}
    for r in sections.get("penalties", []):
        v = r.get("values") or {}
        kind = (v.get("penalty") or "").lower()
        if v.get("playerId") and ("yellow" in kind or "red" in kind):
            c = cards.setdefault(pid(v["playerId"]), {"yellow": 0, "red": 0})
            c["red" if "red" in kind else "yellow"] += 1
    ev_goals, ev_assists = {}, {}
    for r in sections.get("scoring", []):
        v = r.get("values") or {}
        if v.get("scorerId"):
            ev_goals[pid(v["scorerId"])] = ev_goals.get(pid(v["scorerId"]), 0) + 1
        for k in ("assist1Id", "assist2Id"):
            if v.get(k):
                ev_assists[pid(v[k])] = ev_assists.get(pid(v[k]), 0) + 1

    names: dict[str, str] = {}
    con.execute("DELETE FROM player_games WHERE game_id=?", (game_id,))
    for p in box.get("players") or []:
        team = side_seo.get(p.get("team"))
        name = nice((p.get("name") or "").strip())
        if not team or not name:
            continue
        ppid = str(p.get("playerId"))
        names[ppid] = name
        st = p.get("stats") or {}
        first, _, last = name.partition(" ")
        pos = (p.get("position") or pos_by_pid.get(ppid) or "").upper()
        keeper = pos in ("GK", "G") or "GOAL" in pos
        pp = part.get(ppid, "")
        starter = 1 if pp == "started" or _stat(st, "gs", "participation.gs") else 0
        goals = max(_stat(st, "goals", "scoring.goals", "g") or 0, ev_goals.get(ppid, 0))
        assists = max(_stat(st, "ast", "playmaking.ast", "a") or 0, ev_assists.get(ppid, 0))
        c = cards.get(ppid, {})
        played = 1 if (starter or pp in ("played", "started") or (_stat(st, "gp") or 0) > 0
                       or _stat(st, "goalie_minutes") or goals or assists or c) else 0
        upsert(con, "player_games", {
            "game_id": game_id, "team": team, "player": f"{team}--{slug(name)}",
            "first_name": first, "last_name": last, "number": str(p.get("number") or ""),
            "position": POSITIONS.get(pos, pos),
            "starter": starter, "played": played,
            "minutes": _stat(st, "goalie_minutes", "minutes", "min"),
            "goals": goals, "assists": assists,
            "shots": _stat(st, "sh", "shots.sh") or 0, "shots_on_goal": _stat(st, "sog", "shots.sog") or 0,
            "pk_goals": _stat(st, "pkg", "pk") or 0, "pk_attempts": _stat(st, "pka", "pkatt") or 0,
            "gw_goals": _stat(st, "gw", "gwg") or 0, "fouls": _stat(st, "fouls", "fo") or 0,
            "yellow": c.get("yellow", 0) or (_stat(st, "yc", "yellow") or 0),
            "red": c.get("red", 0) or (_stat(st, "rc", "red") or 0),
            "saves": _stat(st, "saves", "goalie.saves"), "goals_allowed": _stat(st, "ga", "goalie.ga"),
            "is_keeper": 1 if keeper else 0,
        }, ("game_id", "team", "player"))

    def minute(v):
        m = clock_minute(v.get("clock"))
        seg = str(v.get("segment") or "").upper()
        if m is not None and seg in ("H2", "2") and m < 45:
            m += 45
        if m is not None and seg.startswith("OT") and m < 90:
            m += 90
        return m

    events = []
    score = [0, 0]
    for r in sections.get("scoring", []):
        v = r.get("values") or {}
        side = v.get("side")
        score[0 if side == "home" else 1] += 1
        assists = [names.get(pid(v.get(k)), v.get(k.replace("Id", "Name")) or "") for k in ("assist1Id", "assist2Id") if v.get(k)]
        events.append({"type": "goal", "minute": minute(v), "clock": v.get("clock"), "team": side_seo.get(side),
                       "player": names.get(pid(v.get("scorerId")), v.get("scorerName") or "Unknown"),
                       "assist": ", ".join(a for a in assists if a) or None, "score": list(score),
                       "note": (v.get("scoreType") or "").lower() or None})
    for r in sections.get("penalties", []):
        v = r.get("values") or {}
        kind = (v.get("penalty") or "").lower()
        if "yellow" in kind or "red" in kind:
            events.append({"type": "red" if "red" in kind else "yellow", "minute": minute(v), "clock": v.get("clock"),
                           "team": side_seo.get(v.get("side")),
                           "player": names.get(pid(v.get("playerId")), v.get("playerName") or "")})
    events.sort(key=lambda e: (e["minute"] if e["minute"] is not None else 999))

    team_stats, lines = {}, []
    periods = {}
    for t in box.get("teams") or []:
        side = "home" if t.get("homeAway") == "H" else "away"
        st = t.get("stats") or {}
        side_pids = {str(p.get("playerId")) for p in box.get("players") or [] if p.get("team") == t.get("homeAway")}
        team_cards = [c for k, c in cards.items() if k in side_pids]
        team_stats[side] = {
            "shots": _stat(st, "sh", "shots.sh"), "shotsOnGoal": _stat(st, "sog", "shots.sog"),
            "corners": _stat(st, "ck", "corners"), "offsides": _stat(st, "offsides", "off"),
            "goalie": {"saves": _stat(st, "saves", "goalie.saves")},
            "penalties": {"fouls": _stat(st, "fouls", "fo"),
                          "yellowCards": sum(c["yellow"] for c in team_cards) if cards else None,
                          "redCards": sum(c["red"] for c in team_cards) if cards else None},
        }
        for p in t.get("periods") or []:
            periods.setdefault(p.get("label"), {})[side] = p.get("score")
    for label, v in periods.items():
        lines.append({"period": label, "home": v.get("home"), "visit": v.get("away")})

    has_players = bool(box.get("players"))
    final_now = status == "final"
    upsert(con, "games", {
        "id": game_id, "status": status, "home_score": hs, "away_score": as_,
        "winner": (row["home"] if hs > as_ else row["away"] if as_ > hs else None) if final_now and hs is not None and as_ is not None else None,
        "venue": game.get("location") or row["venue"],
        "linescore": json.dumps(lines), "team_stats": json.dumps(team_stats), "timeline": json.dumps(events),
        "pbp": json.dumps([]),
        # A final game without published box score is re-checked later (schools upload late).
        "detail_state": "F" if final_now and has_players else "N", "detail_at": stamp, "updated_at": stamp,
    }, ("id",))
    con.commit()
    return True

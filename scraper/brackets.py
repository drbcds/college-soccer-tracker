"""Playoff brackets: conference tournaments (NCAA D-I/D-II) and region tournaments (NJCAA).

No feed publishes these brackets in a structured way, so they are rebuilt from games:
- NCAA: a conference's tournament starts on the earliest date any of its schools tags a game
  as a tournament game on its schedule page (scraper/schedules.py). From that date, every game
  between two schools of that conference (not an NCAA championship game) is a tournament game.
- NJCAA: games flagged postseason/bracket by the NJCAA, grouped by region and division.

Rounds come from team progression (a team's next tournament game is one round later) and
from the calendar (rounds are played on separate days). Future rounds are shown as TBD boxes
sized for a standard single-elimination bracket with byes.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

GENERIC = re.compile(r"^(conference tournament|tournament page|championship central)$|information|\binfo\b|\bpage\b|central|"
                     r"website|bracket|tickets|\bhome\b|^tournament$|^championships?$", re.I)
ROUND_WORDS = re.compile(r"\s*[-–:·]?\s*\b(first|second|third|opening|quarter-?|semi-?)?\s*(round|finals?|quarterfinals?|semifinals?)\b.*$", re.I)
NATIONAL = re.compile(r"\bNCAA\b", re.I)
MIN_MMDD = "10-15"


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _base_name(label: str) -> str:
    return ROUND_WORDS.sub("", label).strip(" -–:·") or label


# ------------------------------------------------------------------ which games
def knockout_ok(games: list[dict]) -> bool:
    """Tournament games never end in a plain draw, and a team that lost doesn't play again."""
    out_teams = set()
    for g in sorted(games, key=lambda g: (g["date"], g["start_epoch"] or 0)):
        if g["home"] in out_teams or g["away"] in out_teams:
            return False
        if g["status"] != "final" or g["home_score"] is None:
            continue
        draw = g["home_score"] == g["away_score"]
        if draw and not g.get("winner") and not re.search(r"PK|SO", g.get("period") or "", re.I):
            return False
        w = g.get("winner") or (g["home"] if g["home_score"] > g["away_score"] else g["away"] if g["away_score"] > g["home_score"] else None)
        if w:
            out_teams.add(g["away"] if w == g["home"] else g["home"])
    return True


def ncaa_tournaments(con, season: int):
    teams = {r["seo"]: dict(r) for r in con.execute("SELECT seo, conference, division FROM teams WHERE COALESCE(assoc,'ncaa')='ncaa'")}
    labels = defaultdict(list)       # (div, conf) -> [(team, date, label)]
    try:
        rows = con.execute("SELECT team, date, label FROM tourney_labels").fetchall()
    except Exception:  # noqa: BLE001 - table missing
        rows = []
    for r in rows:
        t = teams.get(r["team"])
        if not t or not t["conference"] or t["division"] not in (1, 2):
            continue
        if NATIONAL.search(r["label"]) or r["date"][5:] < MIN_MMDD or not r["date"].startswith(str(season)):
            continue
        labels[(t["division"], t["conference"])].append((r["team"], r["date"], r["label"]))

    out = []
    for (div, conf), lab in labels.items():
        conf_games = [dict(g) for g in con.execute(
            """SELECT g.* FROM games g JOIN teams th ON th.seo=g.home JOIN teams ta ON ta.seo=g.away
               WHERE COALESCE(g.source,'ncaa')='ncaa' AND th.conference=? AND ta.conference=? AND g.date>=?
               AND (g.round IS NULL OR g.round='') AND g.status NOT IN ('canceled','cancelled','postponed')
               ORDER BY g.date, g.start_epoch, g.id""", (conf, conf, f"{season}-{MIN_MMDD}"))]
        start = None
        for cand in sorted({d for _, d, _ in lab}):
            if knockout_ok([g for g in conf_games if g["date"] >= cand]):
                start = cand
                break
        if not start:
            continue
        lab = [x for x in lab if x[1] >= start]
        good = [_base_name(l) for _, _, l in lab if not GENERIC.search(l)]
        name = Counter(good).most_common(1)[0][0] if good else None
        games = [g for g in conf_games if g["date"] >= start]
        pending = [(team, d) for team, d, _ in lab]
        out.append({"key": f"ncaa-{div}-{conf}", "assoc": "ncaa", "div": div, "conf": conf, "name": name,
                    "games": games, "pending": pending})
    return out


def njcaa_tournaments(con, season: int):
    teams = {r["seo"]: dict(r) for r in con.execute("SELECT seo, conference, division FROM teams")}
    groups = defaultdict(list)
    rows = con.execute(
        "SELECT * FROM games WHERE source='njcaa' AND tourney=1 AND date>=? AND date<=? "
        "AND status NOT IN ('canceled','cancelled','postponed','no_contest') ORDER BY date, start_epoch, id",
        (f"{season}-{MIN_MMDD}", f"{season}-12-31")).fetchall()
    for g in rows:
        g = dict(g)
        th, ta = teams.get(g["home"], {}), teams.get(g["away"], {})
        known = [t for t in (th, ta) if t.get("conference")]
        if not known:
            continue
        regions = {t["conference"] for t in known}
        div = known[0].get("division") or g["division"]
        if len(regions) == 1:
            key = (div, known[0]["conference"])
        else:
            key = (div, "national")
        groups[key].append(g)
    out = []
    for (div, region), games in groups.items():
        name = f"NJCAA {div_label(div)} inter-region playoffs" if region == "national" else f"{region} Tournament"
        if region == "national":
            games = [g for g in games if g.get("round")]      # only games the NJCAA marks as postseason
        seg = last_segment([g for g in games if g["home"] not in TBD and g["away"] not in TBD])
        if seg:
            games = [g for g in games if g["date"] >= seg[0]["date"]]
        if not games:
            continue
        real_games = drop_non_knockout([g for g in games if g["home"] not in TBD and g["away"] not in TBD])
        pending = [(g["away"] if g["home"] in TBD else g["home"], g["date"]) for g in games
                   if (g["home"] in TBD) != (g["away"] in TBD) and g["status"] == "pre"]
        out.append({"key": f"njcaa-{div}-{region}".lower().replace(" ", "-"), "assoc": "njcaa", "div": div,
                    "conf": None if region == "national" else region, "name": name, "games": real_games, "pending": pending})
    return out


TBD = {"tbd", None, ""}


def last_segment(games: list[dict], gap_days: int = 7) -> list[dict]:
    """Keep the final run of games; a gap of more than a week means earlier games weren't this tournament."""
    games = sorted(games, key=lambda g: (g["date"], g["start_epoch"] or 0))
    start = 0
    for i in range(1, len(games)):
        if (_d(games[i]["date"]) - _d(games[i - 1]["date"])).days > gap_days:
            start = i
    return games[start:]


def drop_non_knockout(games: list[dict]) -> list[dict]:
    """Drop weakly-tagged games (not marked postseason) whose loser kept playing: not knockout games."""
    games = sorted(games, key=lambda g: (g["date"], g["start_epoch"] or 0))
    keep = []
    for i, g in enumerate(games):
        if g.get("round") or g["status"] != "final" or g["home_score"] is None or g["home_score"] == g["away_score"]:
            keep.append(g)
            continue
        loser = g["away"] if g["home_score"] > g["away_score"] else g["home"]
        if any(loser in (h["home"], h["away"]) for h in games[i + 1:]):
            continue
        keep.append(g)
    return keep


def div_label(div) -> str:
    return {"J1": "D-I", "J2": "D-II", "J3": "D-III"}.get(str(div), "")


# ------------------------------------------------------------------ bracket shape
def round_names(total: int) -> list[str]:
    names = []
    for r in range(1, total + 1):
        k = total - r
        names.append("Final" if k == 0 else "Semifinals" if k == 1 else "Quarterfinals" if k == 2
                     else "First round" if r == 1 else "Second round")
    return names


def build(t: dict, today: str) -> dict | None:
    games = [g for g in t["games"]]
    tbd = {"tbd", None, ""}
    real = lambda x: x not in tbd  # noqa: E731

    # Winners of draws decided on penalties: the team that plays again.
    later_teams = defaultdict(set)
    for i, g in enumerate(games):
        for h in games[i + 1:]:
            later_teams[g["id"]].update({h["home"], h["away"]})
    for g in games:
        g["pk"] = bool(re.search(r"PK|SO", g.get("period") or "", re.I))
        if g["status"] == "final" and not g.get("winner") and g["home_score"] is not None and g["home_score"] == g["away_score"]:
            nxt = {g["home"], g["away"]} & later_teams[g["id"]]
            if len(nxt) == 1:
                g["winner"] = nxt.pop()
                g["pk"] = True
        if g["status"] == "final" and g.get("winner") and g["home_score"] == g["away_score"]:
            g["pk"] = True

    # Pending: a school says it plays a tournament game on a date we have no game for yet.
    teams_with_game_on = {(g["home"], g["date"]) for g in games} | {(g["away"], g["date"]) for g in games}
    eliminated = set()
    for g in games:
        if g["status"] == "final" and g.get("winner"):
            eliminated.add(g["away"] if g["winner"] == g["home"] else g["home"])
    last_played = defaultdict(str)
    for g in games:
        for s in (g["home"], g["away"]):
            last_played[s] = max(last_played[s], g["date"])
    pend = {}
    first_day = min([g["date"] for g in games] + [d for _, d in (t.get("pending") or [])] or [today])
    for team, d in sorted(t.get("pending") or [], key=lambda x: x[1]):
        if d < today or (team, d) in teams_with_game_on or team in eliminated or team in pend or d <= last_played[team]:
            continue
        ref = last_played[team] or first_day
        if (_d(d) - _d(ref)).days > 7:      # e.g. national-championship dates listed 'if advancing'
            continue
        # A team whose latest game is still to be played doesn't have a confirmed next game yet.
        if any(g["status"] != "final" and team in (g["home"], g["away"]) for g in games):
            continue
        pend[team] = d
    if not games and not pend:
        return None
    if not games:
        return {"id": t["key"], "assoc": t["assoc"], "div": t["div"], "conf": t["conf"], "name": t["name"],
                "status": "upcoming", "champion": None, "start": min(pend.values()), "field": len(pend),
                "alive": len(pend), "qualified": sorted(pend), "rounds": [], "game_rounds": {}}

    entries = [{"kind": "game", "g": g, "date": g["date"]} for g in games] + \
              [{"kind": "pending", "team": tm, "date": d} for tm, d in pend.items()]
    entries.sort(key=lambda e: (e["date"], (e["g"]["start_epoch"] or 0) if e["kind"] == "game" else 0))

    # Rounds: a team's next game is one round later; games on the same day share a round
    # (so two teams with byes meeting in the quarterfinals line up with the other quarterfinals).
    last_round = defaultdict(int)
    by_date = defaultdict(list)
    for e in entries:
        by_date[e["date"]].append(e)
    done_by_date = []          # (date, round) of entries already placed
    for d in sorted(by_date):
        day = by_date[d]
        # Rounds are played on different days: anything 2+ days after round k is at least round k+1
        # (this places teams with a bye in the round they enter).
        # A team with a bye (known only from its schedule page) enters after the games already
        # played: anything 2+ days after round k is at least round k+1.
        floor = 1 + max([r for dd, r in done_by_date if (_d(d) - _d(dd)).days >= 2] or [0])
        for e in day:
            sides = [e["g"]["home"], e["g"]["away"]] if e["kind"] == "game" else [e["team"]]
            e["round"] = 1 + max([last_round[s] for s in sides if real(s)] or [0])
            if e["kind"] == "pending":
                e["round"] = max(e["round"], floor)
        if any(e["kind"] == "game" for e in day):
            top = max(e["round"] for e in day)
            for e in day:
                e["round"] = top
        for e in day:
            sides = [e["g"]["home"], e["g"]["away"]] if e["kind"] == "game" else [e["team"]]
            for s in sides:
                if real(s):
                    last_round[s] = e["round"]
            done_by_date.append((d, e["round"]))
    used = sorted({e["round"] for e in entries})
    remap = {r: i + 1 for i, r in enumerate(used)}
    for e in entries:
        e["round"] = remap[e["round"]]
    played_rounds = max(remap.values())

    field_teams = {s for e in entries for s in ([e["g"]["home"], e["g"]["away"]] if e["kind"] == "game" else [e["team"]]) if real(s)}
    field = len(field_teams)
    decided = [e for e in entries if e["kind"] == "game" and e["g"]["status"] == "final" and e["g"].get("winner")]
    alive = field - len(decided)
    total = max(played_rounds, math.ceil(math.log2(field)) if field > 1 else 1)

    # Is it finished? One team left and the last game decided.
    final_games = [e for e in entries if e["round"] == played_rounds and e["kind"] == "game"]
    champion = None
    last_day = max(e["date"] for e in entries)
    settled = field >= 3 or (_d(today) - _d(last_day)).days >= 2
    if (alive == 1 and not pend and settled and final_games and len(final_games) == 1
            and all(e["g"]["status"] == "final" for e in final_games)):
        champion = final_games[0]["g"].get("winner")
        total = played_rounds

    # Expected number of games per round in a bracket with byes.
    def expected(r):
        if r == 1:
            return max(1, field - 2 ** (total - 1)) if total > 1 else 1
        return 2 ** (total - r)

    rounds = [{"name": n, "matches": []} for n in round_names(total)]
    for e in entries:
        rounds[e["round"] - 1]["matches"].append(e)

    # Placeholders for rounds still to come (only while the tournament is running).
    if not champion:
        current = min((e["round"] for e in entries if not (e["kind"] == "game" and e["g"]["status"] == "final")),
                      default=played_rounds + 1)
        for r in range(max(1, current), total + 1):
            have = len(rounds[r - 1]["matches"])
            for _ in range(max(0, expected(r) - have)):
                rounds[r - 1]["matches"].append({"kind": "tbd", "round": r, "date": None})

    # Order each column so a game sits next to the games that feed it.
    prev_game = {}
    pos = {}
    for r_i, rnd in enumerate(rounds):
        def feeder_pos(e):
            if e["kind"] == "game":
                sides = [e["g"]["home"], e["g"]["away"]]
            elif e["kind"] == "pending":
                sides = [e["team"]]
            else:
                return (1e9, 0)
            ps = [pos[prev_game[s]] for s in sides if s in prev_game and prev_game[s] in pos]
            return (sum(ps) / len(ps) if ps else 1e6, e["date"] or "")
        if r_i == 0:
            rnd["matches"].sort(key=lambda e: (e["kind"] == "tbd", e["date"] or "", (e["g"]["start_epoch"] or 0) if e["kind"] == "game" else 0))
        else:
            rnd["matches"].sort(key=feeder_pos)
        for i, e in enumerate(rnd["matches"]):
            e["_id"] = f"{r_i}-{i}"
            pos[e["_id"]] = i
        for e in rnd["matches"]:
            sides = [e["g"]["home"], e["g"]["away"]] if e["kind"] == "game" else [e.get("team")]
            for s in sides:
                if real(s):
                    prev_game[s] = e["_id"]

    def out_match(e):
        if e["kind"] == "game":
            g = e["g"]
            return {"id": str(g["id"]), "date": g["date"], "t": g["start_epoch"], "tm": g["has_time"], "s": g["status"],
                    "per": g["period"], "clk": g["clock"],
                    "h": g["home"] if real(g["home"]) else None, "a": g["away"] if real(g["away"]) else None,
                    "hs": g["home_score"], "as": g["away_score"], "w": g.get("winner"), "pk": g.get("pk", False)}
        if e["kind"] == "pending":
            return {"id": None, "date": e["date"], "h": e["team"], "a": None, "s": "pre"}
        return {"id": None, "date": None, "h": None, "a": None, "s": "pre"}

    statuses = [e["g"]["status"] for e in entries if e["kind"] == "game"]
    status = "complete" if champion else "live" if any(s in ("live", "final") for s in statuses) else "upcoming"
    start = min(e["date"] for e in entries)
    return {
        "id": f"{t['key']}", "assoc": t["assoc"], "div": t["div"], "conf": t["conf"], "name": t["name"],
        "status": status, "champion": champion, "start": start, "field": field, "alive": alive if not champion else 1,
        "qualified": sorted(field_teams),
        "rounds": [{"name": r["name"], "matches": [out_match(e) for e in r["matches"]]} for r in rounds],
        "game_rounds": {str(e["g"]["id"]): rounds[e["round"] - 1]["name"] for e in entries if e["kind"] == "game"},
    }


def build_all(con, season: int) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for t in ncaa_tournaments(con, season) + njcaa_tournaments(con, season):
        b = build(t, today)
        if b:
            out.append(b)
    return out

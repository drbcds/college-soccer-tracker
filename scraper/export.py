"""Write the static JSON files the website reads."""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .ingest import nice

STAT_COLS = ("minutes", "goals", "assists", "shots", "shots_on_goal", "pk_goals", "pk_attempts",
             "gw_goals", "fouls", "yellow", "red", "saves", "goals_allowed")


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def game_summary(g) -> dict:
    return {
        "id": g["id"], "div": g["division"], "date": g["date"], "t": g["start_epoch"],
        "tm": g["has_time"], "s": g["status"], "per": g["period"], "clk": g["clock"],
        "h": g["home"], "a": g["away"], "hs": g["home_score"], "as": g["away_score"],
        "hr": g["home_rank"], "ar": g["away_rank"], "w": g["winner"], "rd": g["round"],
    }


def export(con, out_dir: str) -> None:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    games = con.execute("SELECT * FROM games WHERE home IS NOT NULL ORDER BY start_epoch, id").fetchall()
    team_rows = {r["seo"]: dict(r) for r in con.execute("SELECT * FROM teams")}

    # ---- records and standings (all games, ties count as ties)
    rec = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "gf": 0, "ga": 0, "form": []})
    for g in games:
        if g["status"] != "final" or g["home_score"] is None:
            continue
        for me, them, gf, ga in ((g["home"], g["away"], g["home_score"], g["away_score"]),
                                 (g["away"], g["home"], g["away_score"], g["home_score"])):
            r = rec[me]
            r["gf"] += gf
            r["ga"] += ga
            res = "w" if gf > ga else "l" if gf < ga else "t"
            # NCAA marks shootout winners; a draw after PKs stays a tie in the record.
            r[res] += 1
            r["form"].append(res.upper())

    teams_out = []
    for seo, t in team_rows.items():
        r = rec.get(seo, {"w": 0, "l": 0, "t": 0, "gf": 0, "ga": 0, "form": []})
        teams_out.append({
            "seo": seo, "name": t["name_short"] or seo, "full": t["name_full"], "nick": t["nickname"],
            "color": t["color"], "div": t["division"], "conf": t["conference"],
            "w": r["w"], "l": r["l"], "t": r["t"], "gf": r["gf"], "ga": r["ga"], "form": r["form"][-5:],
        })
    teams_out.sort(key=lambda x: x["name"].lower())
    _write(out / "teams.json", teams_out)

    # ---- games by day
    by_day = defaultdict(list)
    for g in games:
        by_day[g["date"]].append(game_summary(g))
    for day, lst in by_day.items():
        _write(out / "days" / f"{day}.json", lst)
    _write(out / "dates.json", {d: len(v) for d, v in sorted(by_day.items())})

    # ---- player game rows
    pg = defaultdict(list)          # game_id -> rows
    by_player = defaultdict(list)   # player key -> rows
    for r in con.execute("SELECT * FROM player_games"):
        r = dict(r)
        r["first_name"], r["last_name"] = nice(r["first_name"]), nice(r["last_name"])
        pg[r["game_id"]].append(r)
        by_player[r["player"]].append(r)
    game_by_id = {g["id"]: g for g in games}

    # ---- game detail files
    by_team_games = defaultdict(list)
    for g in games:
        by_team_games[g["home"]].append(g)
        by_team_games[g["away"]].append(g)
    for g in games:
        detail = game_summary(g)
        h2h = [game_summary(x) for x in by_team_games[g["home"]]
               if x["id"] != g["id"] and {x["home"], x["away"]} == {g["home"], g["away"]}]
        detail.update({
            "venue": g["venue"], "city": g["city"], "st": g["state_usps"],
            "hrec": g["home_record"], "arec": g["away_record"],
            "lines": json.loads(g["linescore"] or "[]"),
            "stats": json.loads(g["team_stats"] or "{}"),
            "events": [{**e, **{k: nice(e[k]) for k in ("player", "assist", "off") if e.get(k)}}
                       for e in json.loads(g["timeline"] or "[]")],
            "pbp": json.loads(g["pbp"] or "[]"),
            "players": [
                {k: r[k] for k in ("team", "player", "first_name", "last_name", "number", "position",
                                   "starter", "played", "is_keeper") + STAT_COLS}
                for r in sorted(pg.get(g["id"], []), key=lambda r: (-r["starter"], -(r["minutes"] or 0)))
            ],
            "h2h": h2h,
        })
        _write(out / "games" / f"{g['id']}.json", detail)

    # ---- players: season totals + game logs
    squads = defaultdict(list)
    search_players = []
    leaders = []
    for key, rows in by_player.items():
        rows.sort(key=lambda r: (game_by_id[r["game_id"]]["start_epoch"] or 0) if r["game_id"] in game_by_id else 0)
        last = rows[-1]
        tot = {c: sum((r[c] or 0) for r in rows) for c in STAT_COLS}
        tot["gp"] = sum(1 for r in rows if r["played"])
        tot["gs"] = sum(1 for r in rows if r["starter"])
        info = {"key": key, "team": last["team"], "first": last["first_name"], "last": last["last_name"],
                "num": last["number"], "pos": last["position"], "gk": any(r["is_keeper"] for r in rows)}
        squads[last["team"]].append({**info, **tot})
        search_players.append([key, f"{last['first_name']} {last['last_name']}", last["team"]])
        if tot["gp"]:
            leaders.append({**info, **tot, "div": team_rows.get(last["team"], {}).get("division")})
        log = []
        for r in rows:
            g = game_by_id.get(r["game_id"])
            if not g:
                continue
            home = g["home"] == r["team"]
            log.append({"g": game_summary(g), "home": home,
                        **{k: r[k] for k in ("starter", "played", "number", "position") + STAT_COLS}})
        _write(out / "players" / f"{key}.json", {**info, "totals": tot, "log": log})

    # ---- team files
    for seo, t in team_rows.items():
        tg = sorted(by_team_games.get(seo, []), key=lambda g: (g["start_epoch"] or 0, g["id"]))
        squad = sorted(squads.get(seo, []), key=lambda p: (-p["goals"], -p["assists"], -p["minutes"]))
        _write(out / "teams" / f"{seo}.json", {
            "team": next(x for x in teams_out if x["seo"] == seo),
            "games": [game_summary(g) for g in tg],
            "squad": squad,
        })

    # ---- leaders (top 50 per division per category)
    lead_out = {}
    for div in (1, 2):
        pool = [p for p in leaders if p["div"] == div]
        lead_out[str(div)] = {
            "goals": sorted(pool, key=lambda p: (-p["goals"], p["gp"]))[:50],
            "assists": sorted(pool, key=lambda p: (-p["assists"], p["gp"]))[:50],
            "points": sorted(pool, key=lambda p: (-(2 * p["goals"] + p["assists"]), p["gp"]))[:50],
            "saves": sorted([p for p in pool if p["gk"]], key=lambda p: -(p["saves"] or 0))[:50],
        }
    _write(out / "leaders.json", lead_out)
    _write(out / "search.json", {"players": search_players})

    live = [game_summary(g) for g in games if g["status"] == "live"]
    _write(out / "meta.json", {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "games": len(games), "teams": len(team_rows), "players": len(by_player), "live": len(live),
    })
    _write(out / "live.json", live)

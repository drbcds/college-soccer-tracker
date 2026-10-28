"""Probe data sources from GitHub's servers and save raw responses.

The development workspace cannot reach school or NCAA sites, so this script
runs inside GitHub Actions, saves what each source returns into the output
folder, and the workflow commits that folder to the `samples` branch.

Current target: NJCAA public GraphQL API (Ardor SportsHub).
"""
import json
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://public-graphql-api.ardorsportshub.com/public/graphql"
T = "404933495157163011"  # NJCAA tenant
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36",
                  "Origin": "https://www.njcaa.org", "Referer": "https://www.njcaa.org/"})
log = []


def gql(name, query, variables):
    t0 = time.time()
    try:
        r = S.post(URL, json={"query": query, "variables": variables}, timeout=90)
        (OUT / f"{name}.json").write_bytes(r.content)
        entry = {"name": name, "status": r.status_code, "bytes": len(r.content), "secs": round(time.time() - t0, 1)}
        log.append(entry); print(json.dumps(entry))
        time.sleep(0.5)
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.append({"name": name, "error": str(e)}); print(name, e)
        return {}


GAME_FIELDS = """gameId: id entryKind title summary sportId date time scheduledAt timezone opponentName
  homeTeamName awayTeamName isHome homeScore awayScore status result isNeutralSite location sport metadata
  hasBoxscore boxscoreGameId gameType eventType eventTypeName seasonPhase isPostseason isTournament
  isAssociation isNational isRegion isConference isDivision countsInOverallRecord countsInNjcaaRecord
  official countsAsPlayed recordOutcome"""

seasons = gql("nj_seasons", "query($t: ID!) { listTenantSeasons(tenantId: $t) { current seasons { seasonId season current status archival } } }", {"t": T})
cur = ((seasons.get("data") or {}).get("listTenantSeasons") or {}).get("current") or "2026-27"
print("current season:", cur)

sports = gql("nj_sports", "query($t: ID!, $s: String) { listTeamSports(tenantId: $t, season: $s) { supported groups { id sport sportName gender sportId genderId segment seasonSegmentId } } }", {"t": T, "s": cur})
groups = ((sports.get("data") or {}).get("listTeamSports") or {}).get("groups") or []
soccer = [g for g in groups if "soccer" in (g.get("sportName") or "").lower() or "soc" in (g.get("sport") or "").lower()]
print("soccer groups:", soccer)
codes = list(dict.fromkeys([g["sport"] for g in soccer if (g.get("gender") or "").lower().startswith("m")] or ["msoc"]))

TEAMS_Q = """query($t: ID!, $sport: String!, $season: String!) { listTeams(tenantId: $t, limit: 5000, sport: $sport, season: $season) {
  id tenantId teamId sportId seasonId genderId schoolId schoolName officialSchoolName displayName name sport season gender
  teamType teamLevel regionId regionName conferenceId conferenceName logo { url } metadata } }"""
SCHED_Q = "query($t: ID!, $sport: String, $season: String, $limit: Int) { schedule(tenantId: $t, sport: $sport, season: $season, limit: $limit) { games { " + GAME_FIELDS + " } } }"

for code in codes:
    gql(f"nj_teams_{code}", TEAMS_Q, {"t": T, "sport": code, "season": cur})
    sched = gql(f"nj_schedule_{code}", SCHED_Q, {"t": T, "sport": code, "season": cur, "limit": 500})
    games = (((sched.get("data") or {}).get("schedule") or {}).get("games")) or []
    print(code, "games:", len(games))
    done = [g for g in games if g.get("hasBoxscore")]
    for i, g in enumerate(done[-2:]):
        gid = g.get("boxscoreGameId") or g.get("gameId")
        gql(f"nj_box_{code}_{i}", """query($t: ID!, $g: ID!) { getGame(tenantId: $t, id: $g) { id tenantId homeTeamId awayTeamId sport date time
          scheduledAt timezone homeTeamName awayTeamName homeScore awayScore status location hasBoxscore metadata }
          gameBoxscoreDetails(tenantId: $t, gameId: $g) { gameId game hasBoxscore teams { teamSeasonId tenantId division homeAway name score periods stats }
          players { playerId number name position team stats } plays } }""", {"t": T, "g": str(gid)})

gql("nj_live", """query($t: ID!) { listLiveGamesForTenant(tenantId: $t) { gameId tenantId status period sport clock { timeRemaining }
  home { score name shortName color } away { score name shortName color } recentEvents { ts type summary team points } lastUpdated } }""", {"t": T})

(OUT / "_log.json").write_text(json.dumps(log, indent=2))

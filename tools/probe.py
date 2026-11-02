"""Probe data sources from GitHub's servers and save raw responses.

Current target: playoff / conference tournament brackets.
- NJCAA: published championships (national tenant and region tenants).
- ESPN public scoreboard for college men's soccer (game notes such as "ACC Tournament - Quarterfinal").
"""
import json
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"})
log = []


def save(name, r):
    (OUT / f"{name}.json").write_bytes(r.content)
    e = {"name": name, "status": r.status_code, "bytes": len(r.content)}
    log.append(e); print(json.dumps(e))


GQL = "https://public-graphql-api.ardorsportshub.com/public/graphql"
NJ = "404933495157163011"
H = {"Origin": "https://www.njcaa.org", "Referer": "https://www.njcaa.org/"}
CH = "id tenantId title seasonId seasonCode sportId sportCode genderId genderCode divisionId divisionCode lifecycle startsOn endsOn locationName publishedAt completedAt stageCount"
ONE = """query($t: ID!, $c: ID!) { publishedChampionship(tenantId: $t, championshipId: $c) { championship { %s }
  stages { id name sortOrder fieldSize format lifecycle matches { id gameNumber roundNumber roundName sortOrder gameScheduleId
  scheduledStartAt gameStatus homeScore awayScore bye state slots { position teamSeasonId teamTenantId teamName teamShortName logoUrl seed sourceLabel } } } } }""" % CH


def gql(q, v):
    r = S.post(GQL, headers=H, json={"query": q, "variables": v}, timeout=60)
    time.sleep(0.3)
    return r


r = gql("query($t: ID!) { publishedChampionships(tenantId: $t) { championships { %s } } }" % CH, {"t": NJ})
save("nj_champs", r)
champs = (r.json().get("data") or {}).get("publishedChampionships", {}).get("championships") or []
msoc = [c for c in champs if (c.get("sportCode") or "").lower() in ("msoc", "soc") or "soccer" in (c.get("title") or "").lower()]
print("njcaa champs:", len(champs), "soccer:", [(c["title"], c["id"]) for c in msoc])
for i, c in enumerate(msoc[:4]):
    save(f"nj_champ_{i}", gql(ONE, {"t": NJ, "c": c["id"]}))

# Regions: ids from the org hierarchy; try each as a tenant for published championships.
h = gql("query($t: ID!, $s: String) { orgHierarchy(tenantId: $t, season: $s) { regions { regionId short } memberships { tenantId regionId } } }",
        {"t": NJ, "s": "2026-27"})
save("nj_hierarchy", h)
regions = ((h.json().get("data") or {}).get("orgHierarchy") or {}).get("regions") or []
found = []
for reg in regions:
    rr = gql("query($t: ID!) { publishedChampionships(tenantId: $t) { championships { %s } } }" % CH, {"t": str(reg["regionId"])})
    try:
        cs = rr.json()["data"]["publishedChampionships"]["championships"] or []
    except Exception:  # noqa: BLE001
        cs = []
    soc = [c for c in cs if "soc" in (c.get("sportCode") or "").lower() or "soccer" in (c.get("title") or "").lower()]
    found.append({"region": reg["short"], "id": reg["regionId"], "all": len(cs), "soccer": [(c["title"], c["id"], c.get("genderCode")) for c in soc]})
(OUT / "nj_region_champs.json").write_text(json.dumps(found, indent=1))
print(json.dumps(found)[:3000])
withsoc = [f for f in found if f["soccer"]]
for f in withsoc[:3]:
    save(f"nj_region_champ_{f['region']}", gql(ONE, {"t": str(f["id"]), "c": f["soccer"][0][1]}))

# ESPN college soccer scoreboard
for lg in ("usa.ncaa.m.1", "usa.ncaa.m.2"):
    for d in ("20261031", "20261101", "20261102"):
        r = S.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard?dates={d}&limit=300", timeout=30)
        save(f"espn_{lg}_{d}", r)
        time.sleep(0.5)

(OUT / "_log.json").write_text(json.dumps(log, indent=2))

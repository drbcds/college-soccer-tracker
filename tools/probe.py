"""Probe data sources from GitHub's servers and save raw responses.

The development workspace cannot reach school or NCAA sites, so this script
runs inside GitHub Actions, saves what each source returns into the output
folder, and the workflow commits that folder to the `samples` branch.

Current target: player class year (Fr/So/Jr/Sr/Gr).
- NJCAA: roster query on the public GraphQL API.
- NCAA: school athletics site (found via ncaa.com school pages), roster page.
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"})
log = []


def save(name, r, ext):
    (OUT / f"{name}{ext}").write_bytes(r.content)
    e = {"name": name, "status": r.status_code, "bytes": len(r.content), "url": r.url}
    log.append(e); print(json.dumps(e))


def get(name, url, ext=".html"):
    try:
        r = S.get(url, timeout=30)
        save(name, r, ext)
        time.sleep(0.5)
        return r
    except Exception as e:  # noqa: BLE001
        log.append({"name": name, "error": str(e), "url": url}); print(name, e)


# ---- NJCAA rosters
GQL = "https://public-graphql-api.ardorsportshub.com/public/graphql"
NJ = "404933495157163011"
H = {"Origin": "https://www.njcaa.org", "Referer": "https://www.njcaa.org/"}
teams = S.post(GQL, headers=H, json={"query": """query($t: ID!) { listTeams(tenantId: $t, limit: 5000, sport: "msoc", season: "2026-27") { id tenantId displayName } }""",
                                     "variables": {"t": NJ}}).json()["data"]["listTeams"]
ROSTER = """query($t: ID!, $team: ID!) { listPlayersForTeam(tenantId: $t, teamId: $team, limit: 1000) {
  id personId firstName lastName jersey position year redshirt isRedshirt height weight hometown active metadata } }"""
for i, t in enumerate([x for x in teams if "Ocean County" in x["displayName"] or "Western Nebraska" in x["displayName"]][:2]):
    for label, tenant in (("nj", NJ), ("school", t["tenantId"])):
        r = S.post(GQL, headers=H, json={"query": ROSTER, "variables": {"t": tenant, "team": t["id"]}})
        save(f"njroster_{i}_{label}", r, ".json")

# ---- NCAA: athletics site from ncaa.com school page, then the roster page
sites = {}
for seo in ("north-carolina", "young-harris", "marist", "salem-intl", "lynn", "cal-st-la", "west-va-wesleyan", "mercyhurst"):
    r = get(f"ncaaschool_{seo}", f"https://www.ncaa.com/schools/{seo}")
    if r is not None and r.ok:
        m = re.findall(r'href="(https?://[^"]+)"[^>]*>\s*(?:Official\s+)?(?:Athletics\s+)?Website', r.text, re.I)
        links = re.findall(r'class="[^"]*school-(?:info|links)[^"]*"[\s\S]{0,2000}', r.text)
        sites[seo] = m[0] if m else None
        (OUT / f"ncaaschool_{seo}_links.txt").write_text("\n".join(sorted(set(re.findall(r'href="(https?://[^"]+)"', r.text)))))
print(sites)
for seo, base in sites.items():
    if base:
        base = base.rstrip("/")
        get(f"roster_{seo}", f"{base}/sports/mens-soccer/roster")
        get(f"roster_{seo}_alt", f"{base}/sports/msoc/2026-27/roster")

(OUT / "_sites.json").write_text(json.dumps(sites, indent=2))
(OUT / "_log.json").write_text(json.dumps(log, indent=2))

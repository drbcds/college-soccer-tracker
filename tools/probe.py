"""Probe data sources from GitHub's servers and save raw responses.

The development workspace cannot reach school or NCAA sites, so this script
runs inside GitHub Actions, saves what each source returns into the output
folder, and the workflow commits that folder to the `samples` branch.

Current target: NJCAA (junior college). njcaa.org runs on "Ardor SportsHub":
site settings live at /data/site.json and point to a public GraphQL API.
"""
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                  "Origin": "https://www.njcaa.org", "Referer": "https://www.njcaa.org/"})
log = []


def record(name, url, r=None, err=None, ext=None):
    entry = {"name": name, "url": url}
    if r is not None:
        ct = r.headers.get("content-type", "")
        ext = ext or (".json" if "json" in ct else ".js" if "javascript" in ct else ".html")
        (OUT / f"{name}{ext}").write_bytes(r.content)
        entry.update(status=r.status_code, bytes=len(r.content), ctype=ct)
    if err:
        entry["error"] = str(err)
    log.append(entry)
    print(json.dumps(entry))


def get(name, url):
    try:
        r = S.get(url, timeout=30)
        record(name, url, r)
        time.sleep(0.5)
        return r
    except Exception as e:  # noqa: BLE001
        record(name, url, err=e)


def gql(name, url, query, variables=None):
    try:
        r = S.post(url, json={"query": query, "variables": variables or {}}, timeout=60)
        record(name, url, r, ext=".json")
        time.sleep(0.5)
        return r
    except Exception as e:  # noqa: BLE001
        record(name, url, err=e)


site = get("njcaa_site_config", "https://www.njcaa.org/data/site.json")
cfg = {}
try:
    cfg = site.json()
except Exception:  # noqa: BLE001
    pass
settings = {**cfg, **(cfg.get("settings") or {})}
gurl, tenant = settings.get("publicGraphqlUrl"), settings.get("tenantId")
read_api = settings.get("readApiUrl")
print("graphql:", gurl, "tenant:", tenant)

# Every URL mentioned in the config (remote modules, APIs)
blob = json.dumps(cfg)
urls = sorted(set(re.findall(r'https?://[^"\\\s]+', blob)))
(OUT / "njcaa_config_urls.txt").write_text("\n".join(urls))

# Download remote module code (schedule/stats widgets) to learn their queries
for i, u in enumerate(u for u in urls if re.search(r"remoteEntry|\.js(\?|$)|mf-manifest", u)):
    r = get(f"njcaa_remote_{i}", u)
    if r is not None and r.ok and "json" in r.headers.get("content-type", ""):
        try:
            man = r.json()
            base = u.rsplit("/", 1)[0]
            assets = list(dict.fromkeys(re.findall(r'"([^"]+\.js)"', json.dumps(man))))
            for j, a in enumerate(assets):
                get(f"njcaa_remote_{i}_asset_{j}", urllib.parse.urljoin(base + "/", a))
        except Exception:  # noqa: BLE001
            pass

if gurl:
    INTRO = """query { __schema { queryType { name } types { name kind
      fields { name args { name type { name kind ofType { name kind ofType { name kind } } } }
               type { name kind ofType { name kind ofType { name kind } } } } } } }"""
    gql("njcaa_gql_introspection", gurl, INTRO)
    gql("njcaa_gql_teams", gurl,
        "query($t: ID!) { listTeams(tenantId: $t, sport: \"soccer\", gender: \"men\", season: \"2026\", limit: 5) { id } }",
        {"t": tenant})

if read_api:
    for path in ("", "public/schedule?sport=msoc&season=2026-27", "schedule?sport=msoc&season=2026-27",
                 f"public/tenants/{tenant}/schedule?sport=msoc", f"tenants/{tenant}/sports"):
        get("njcaa_read_" + re.sub(r"\W+", "_", path)[:50], read_api + path)

(OUT / "_log.json").write_text(json.dumps(log, indent=2))

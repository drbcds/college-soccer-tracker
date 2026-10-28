"""Probe data sources from GitHub's servers and save raw responses.

Current target: why NCAA school-site lookup (scraper/rosters.py) found no sites.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from scraper import rosters  # noqa: E402

import requests  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
s = requests.Session()
s.headers.update({"User-Agent": rosters.UA, "Accept-Language": "en-US,en;q=0.9"})
log = []
for i, seo in enumerate(["villanova", "temple", "american", "rutgers", "marist", "young-harris", "lynn", "duke"]):
    r = s.get(f"https://www.ncaa.com/schools/{seo}", timeout=30)
    m = rosters.SITE_RE.search(r.text)
    e = {"seo": seo, "status": r.status_code, "bytes": len(r.content), "final": r.url, "site": m.group(1) if m else None}
    if i < 2:
        (OUT / f"school_{seo}.html").write_bytes(r.content)
    if e["site"]:
        rr = s.get(f"{e['site'].rstrip('/')}/sports/mens-soccer/roster", timeout=30)
        e.update(roster_status=rr.status_code, roster_bytes=len(rr.content), players=len(rosters.parse_sidearm(rr.text)))
    log.append(e); print(json.dumps(e))
    time.sleep(0.4)
(OUT / "_log.json").write_text(json.dumps(log, indent=2))

"""Probe data sources from GitHub's servers and save raw responses.

Current target: conference tournament labels on school schedule pages.
"""
import json
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
                  "Accept-Language": "en-US,en;q=0.9"})
SITES = json.loads(Path("tools/probe_sites.json").read_text())
log = []
for seo, base in SITES.items():
    for path in ("/sports/mens-soccer/schedule/2026", "/sports/mens-soccer/schedule"):
        try:
            r = S.get(base.rstrip("/") + path, timeout=30)
        except Exception as e:  # noqa: BLE001
            log.append({"seo": seo, "error": str(e)}); continue
        log.append({"seo": seo, "path": path, "status": r.status_code, "bytes": len(r.content), "final": r.url})
        if r.ok and len(r.content) > 20000:
            (OUT / f"sched_{seo}.html").write_bytes(r.content)
            break
        time.sleep(0.3)
    time.sleep(0.3)
(OUT / "_log.json").write_text(json.dumps(log, indent=1))

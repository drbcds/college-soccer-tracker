"""Probe data sources from GitHub's servers and save raw responses.

The development workspace cannot reach school or NCAA sites, so this script
runs inside GitHub Actions, saves what each source returns into the output
folder, and the workflow commits that folder to the `samples` branch.

Current target: NJCAA (junior college). njcaa.org is a JavaScript app, so we
download its bundle and look for the API it calls.
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
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
log = []


def get(name, url, **kw):
    entry = {"name": name, "url": url}
    try:
        r = S.get(url, timeout=30, **kw)
        ct = r.headers.get("content-type", "")
        ext = ".json" if "json" in ct else ".js" if "javascript" in ct else ".xml" if "xml" in ct else ".html"
        (OUT / f"{name}{ext}").write_bytes(r.content)
        entry.update(status=r.status_code, bytes=len(r.content), ctype=ct, final_url=r.url)
        time.sleep(0.7)
        log.append(entry); print(json.dumps(entry))
        return r
    except Exception as e:  # noqa: BLE001
        entry["error"] = str(e); log.append(entry); print(json.dumps(entry))
        return None


home = get("njcaa_home", "https://www.njcaa.org/")
urls = set()
if home is not None and home.ok:
    for src in re.findall(r'src="([^"]+\.js)"', home.text):
        js = get("njcaa_bundle_" + re.sub(r"\W+", "_", src)[-40:], urllib.parse.urljoin(home.url, src))
        if js is not None and js.ok:
            urls |= set(re.findall(r'https?://[A-Za-z0-9.\-]+(?:/[A-Za-z0-9_\-./{}$:%]*)?', js.text))
            # relative API paths
            urls |= {"REL " + m for m in set(re.findall(r'["\'`](/api/[^"\'`]{2,120})["\'`]', js.text))}
            for m in set(re.findall(r'["\'`]([^"\'`]{0,80}(?:soccer|msoc|boxscore|schedule|scoreboard|stats)[^"\'`]{0,80})["\'`]', js.text, re.I)):
                urls.add("STR " + m)
(OUT / "njcaa_urls.txt").write_text("\n".join(sorted(urls)))

# Known PrestoSports-style league pages used by JUCO conferences
for name, url in {
    "kjccc_msoc_schedule": "https://www.kjccc.org/sports/msoc/2026-27/schedule",
    "kjccc_msoc_index": "https://www.kjccc.org/sports/msoc/index",
    "northeast_msoc_schedule": "https://northeasthawks.com/sports/msoc/2026-27/schedule",
    "njcaa_stats_site": "https://njcaastats.prestosports.com/sports/msoc/2026-27/div1/schedule",
}.items():
    r = get(name, url)
    if r is not None and r.ok:
        links = sorted(set(re.findall(r'href="([^"]*boxscores/[^"]*)"', r.text)))
        (OUT / f"{name}_boxlinks.txt").write_text("\n".join(links))
        if links:
            get(name + "_boxscore", urllib.parse.urljoin(r.url, links[-1]))

(OUT / "_log.json").write_text(json.dumps(log, indent=2))

"""Probe data sources from GitHub's servers and save raw responses.

The development workspace cannot reach school or NCAA sites, so this script
runs inside GitHub Actions, saves what each source returns into ./samples,
and the workflow commits that folder to the `samples` branch for inspection.
"""
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

log = []


def save(name, url, resp=None, err=None):
    entry = {"name": name, "url": url}
    if resp is not None:
        entry.update(status=resp.status_code, bytes=len(resp.content),
                     ctype=resp.headers.get("content-type", ""),
                     final_url=resp.url)
        ext = ".json" if "json" in entry["ctype"] else ".html"
        if "xml" in entry["ctype"]:
            ext = ".xml"
        if "pdf" in entry["ctype"]:
            ext = ".pdf"
        (OUT / f"{name}{ext}").write_bytes(resp.content)
    if err:
        entry["error"] = str(err)
    log.append(entry)
    print(json.dumps(entry))


def get(name, url, **kw):
    try:
        r = S.get(url, timeout=30, **kw)
        save(name, url, r)
        time.sleep(1)
        return r
    except Exception as e:  # noqa: BLE001
        save(name, url, err=e)
        return None


# ---------- NCAA.com GraphQL (D1 / D2) ----------
GQL = "https://sdataprod.ncaa.com/"
SCOREBOARD_HASH = "7287cda610a9326931931080cb3a604828febe6fe3c9016a7e4a36db99efdb7c"
GAME_HASH = "93a02c7193c89d85bcdda8c1784925d9b64657f73ef584382e2297af555acd4b"
BOX_HASH = "c9070c4e5a76468a4025896df89f8a7b22be8275c54a22ff79619cbb27d63d7d"
TEAMSTATS_HASH = "d3009ee734557a3af9b80a1fd0326575799094e8046a4188c6aebea7072ea7bf"
PBP_HASH = "57f922d56d60d88326b62202b3d88e8cd3cfb6687931bc0b5b3dfab089b84faa"
SCORING_HASH = "7f86673d4875cd18102b7fa598e2bc5da3f49d05a1c15b1add0e2367ee890198"


def gql_url(h, variables, extra=""):
    ext = json.dumps({"persistedQuery": {"version": 1, "sha256Hash": h}}, separators=(",", ":"))
    var = json.dumps(variables, separators=(",", ":"))
    return f"{GQL}?{extra}extensions={urllib.parse.quote(ext)}&variables={urllib.parse.quote(var)}"


contest_ids = {}
for div in (1, 2):
    for date in ("2026/10/25", "2026/10/28"):
        r = get(f"ncaa_scoreboard_d{div}_{date.replace('/', '')}",
                gql_url(SCOREBOARD_HASH, {"sportCode": "MSO", "division": div,
                                          "seasonYear": 2026, "contestDate": date}))
        try:
            contests = r.json()["data"]["contests"]
            final = [c for c in contests if c.get("gameState") == "F"] or contests
            if final:
                contest_ids[f"d{div}"] = final[0]["contestId"]
        except Exception:  # noqa: BLE001
            pass

for div, cid in contest_ids.items():
    get(f"ncaa_game_{div}", gql_url(GAME_HASH, {"id": str(cid), "week": None, "staticTestEnv": None},
                                    "meta=GetGamecenterGameById_web&"))
    get(f"ncaa_box_{div}", gql_url(BOX_HASH, {"contestId": str(cid), "staticTestEnv": None}))
    get(f"ncaa_teamstats_{div}", gql_url(TEAMSTATS_HASH, {"contestId": str(cid), "staticTestEnv": None}))
    get(f"ncaa_pbp_{div}", gql_url(PBP_HASH, {"contestId": str(cid), "staticTestEnv": None}))
    get(f"ncaa_scoring_{div}", gql_url(SCORING_HASH, {"contestId": str(cid), "staticTestEnv": None}))
    get(f"ncaa_gamepage_{div}", f"https://www.ncaa.com/game/{cid}/boxscore")

get("ncaa_schools_json", "https://www.ncaa.com/json/schools")
get("ncaa_scoreboard_page_d1", "https://www.ncaa.com/scoreboard/soccer-men/d1")

# ---------- Sidearm school sites ----------
SIDEARM = {
    "unc": "https://goheels.com",
    "georgetown": "https://guhoyas.com",
    "duke": "https://goduke.com",
    "vcu": "https://vcuathletics.com",
    "queens": "https://queensathletics.com",
}
for key, base in SIDEARM.items():
    r = get(f"sidearm_{key}_schedule", f"{base}/sports/mens-soccer/schedule/2026")
    get(f"sidearm_{key}_roster", f"{base}/sports/mens-soccer/roster/2026")
    if r is not None and r.ok:
        links = sorted(set(re.findall(r'href="([^"]*box[^"]*score[^"]*)"', r.text, re.I)))
        (OUT / f"sidearm_{key}_boxlinks.txt").write_text("\n".join(links))
        if links:
            link = urllib.parse.urljoin(base, links[-1])
            get(f"sidearm_{key}_boxscore", link)
    # candidate JSON endpoints used by Sidearm sites
    get(f"sidearm_{key}_api_sports", f"{base}/api/v2/Sports")
    get(f"sidearm_{key}_calendar_svc",
        f"{base}/services/responsive-calendar.ashx?type=events&sport=0&location=all&date=10/1/2026")

# ---------- NJCAA (PrestoSports league site) ----------
for path in (
    "https://www.njcaa.org/sports/msoc/2026-27/div1/schedule",
    "https://www.njcaa.org/sports/msoc/2026-27/div3/schedule",
    "https://www.njcaa.org/sports/msoc/2026-27/div1/teams",
    "https://www.njcaa.org/sports/msoc/index",
):
    name = "njcaa_" + re.sub(r"[^a-z0-9]+", "_", path.split("/sports/")[1]).strip("_")
    r = get(name, path)
    if r is not None and r.ok:
        links = sorted(set(re.findall(r'href="([^"]*boxscores?/[^"]*)"', r.text)))
        (OUT / f"{name}_boxlinks.txt").write_text("\n".join(links))
        if links:
            get(name + "_boxscore", urllib.parse.urljoin(path, links[-1]))

(OUT / "_log.json").write_text(json.dumps(log, indent=2))
print("done", len(log))

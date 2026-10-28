"""Player class year (Fr/So/Jr/Sr/Gr) and hometown from team rosters.

- NJCAA: the public GraphQL API returns each team's roster with class year.
- NCAA: NCAA.com's school page links to the school's athletics website; most of
  those run on Sidearm Sports, whose men's soccer roster page lists class year.
  Schools on other platforms (or that block automated requests) are skipped.
"""
from __future__ import annotations

import html as htmlmod
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from .db import upsert
from .ingest import now, slug

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

CLASS_RE = re.compile(
    r"\b(?P<rs>R-?S?-?\s?|Redshirt\s+|RS\s+)?(?P<yr>Freshman|Fr\.?|FR|Sophomore|So\.?|SO|Junior|Jr\.?|JR|Senior|Sr\.?|SR|"
    r"Graduate(?:\s+Student)?|Grad\.?|Gr\.?|GR|GS|5th|Fifth)(?![a-z])")


def norm_class(text: str | None) -> str | None:
    if not text:
        return None
    m = CLASS_RE.search(text.strip())
    if not m:
        return None
    y = m.group("yr").lower().rstrip(".")
    base = ("Fr" if y.startswith("f") and y != "fifth" else
            "So" if y.startswith("so") else
            "Jr" if y.startswith("j") else
            "Sr" if y.startswith("se") or y == "sr" else
            "Gr")
    return base


# ------------------------------------------------------------------ NJCAA
NJ_ROSTER_Q = """query($t: ID!, $team: ID!) { listPlayersForTeam(tenantId: $t, teamId: $team, limit: 1000) {
  firstName lastName jersey position year hometown height } }"""


def fetch_njcaa(con, client, max_age_days: int = 6) -> int:
    from .njcaa import NJCAA_TENANT
    teams = con.execute("SELECT seo, ncaa_id, roster_at FROM teams WHERE assoc='njcaa' AND ncaa_id IS NOT NULL").fetchall()
    n = 0
    for t in teams:
        if _fresh(t["roster_at"], max_age_days):
            continue
        try:
            players = client.q(NJ_ROSTER_Q, {"t": NJCAA_TENANT, "team": str(t["ncaa_id"])}).get("listPlayersForTeam") or []
        except Exception as e:  # noqa: BLE001
            log.warning("njcaa roster %s: %s", t["seo"], e)
            continue
        rows = [{"name": f"{p.get('firstName') or ''} {p.get('lastName') or ''}".strip(), "class_year": norm_class((p.get("year") or "").upper()),
                 "number": p.get("jersey"), "position": p.get("position"), "hometown": p.get("hometown"),
                 "height": p.get("height")} for p in players]
        _store(con, t["seo"], rows)
        n += 1
    log.info("rosters: %d NJCAA teams refreshed", n)
    return n


# ------------------------------------------------------------------ NCAA (school sites)
SITE_RE = re.compile(r'<a\s+href="(https?://[^"]+)"[^>]*>\s*<span class="icon-web">', re.I)
LINK_RE = re.compile(r'href="([^"]*(?:mens-soccer|msoc|m-soccer)[^"]*/roster/([a-z0-9-]+)/(\d+))"', re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _fresh(stamp: str | None, days: int) -> bool:
    if not stamp:
        return False
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t < timedelta(days=days)


def parse_sidearm(page: str) -> list[dict]:
    """Players and class years from a Sidearm roster page (classic or 'nextgen' layout)."""
    first: dict[str, tuple[int, str]] = {}
    for m in LINK_RE.finditer(page):
        pid = m.group(3)
        if pid not in first:
            first[pid] = (m.start(), m.group(2))
    order = sorted(first.items(), key=lambda kv: kv[1][0])
    out = []
    for i, (pid, (pos, pslug)) in enumerate(order):
        end = order[i + 1][1][0] if i + 1 < len(order) else pos + 6000
        seg = page[pos:min(end, pos + 6000)]
        text = htmlmod.unescape(TAG_RE.sub(" ", seg))
        text = re.sub(r"\s+", " ", text)
        name = " ".join(w.capitalize() for w in pslug.split("-"))
        # Remove the player's own name first so a 'Jr.' suffix isn't read as Junior.
        for w in pslug.split("-"):
            text = re.sub(rf"\b{re.escape(w)}\b\.?", " ", text, flags=re.I)
        text = re.sub(r"\bJr\.?\s+(?=(Fr|So|Jr|Sr|Gr|Freshman|Sophomore|Junior|Senior|Graduate)\b)", " ", text)
        out.append({"slug": pslug, "name": name, "class_year": norm_class(text)})
    return out


def find_site(session, seo: str) -> str | None:
    r = session.get(f"https://www.ncaa.com/schools/{seo}", timeout=30)
    if not r.ok:
        return None
    m = SITE_RE.search(r.text)
    return m.group(1).rstrip("/") if m else None


def fetch_ncaa(con, max_age_days: int = 6, max_minutes: float = 20) -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    teams = con.execute(
        "SELECT seo, site, roster_at FROM teams WHERE COALESCE(assoc,'ncaa')='ncaa' AND division IN (1, 2)").fetchall()
    started, n, found = time.monotonic(), 0, 0
    for t in teams:
        if time.monotonic() - started > max_minutes * 60:
            break
        # site NULL = never looked up; '' = looked up, none found.
        if t["site"] is not None and _fresh(t["roster_at"], max_age_days):
            continue
        try:
            site = t["site"] if t["site"] is not None else (find_site(s, t["seo"]) or "")
            if site != t["site"]:
                con.execute("UPDATE teams SET site=? WHERE seo=?", (site, t["seo"]))
            players = []
            if site:
                r = s.get(f"{site}/sports/mens-soccer/roster", timeout=30)
                if r.ok:
                    players = parse_sidearm(r.text)
            _store(con, t["seo"], players)
            n += 1
            found += bool(players)
        except requests.RequestException as e:
            log.warning("roster %s: %s", t["seo"], e)
            con.execute("UPDATE teams SET roster_at=? WHERE seo=?", (now(), t["seo"]))
        time.sleep(0.4)
    con.commit()
    log.info("rosters: %d NCAA teams checked, %d with class years", n, found)
    return n


def _store(con, team: str, players: list[dict]) -> None:
    if players:
        con.execute("DELETE FROM rosters WHERE team=?", (team,))
    for p in players:
        if not p.get("name"):
            continue
        upsert(con, "rosters", {
            "team": team, "slug": p.get("slug") or slug(p["name"]), "name": p["name"],
            "class_year": p.get("class_year"), "number": str(p.get("number") or "") or None,
            "position": p.get("position"), "hometown": p.get("hometown"), "height": p.get("height"),
        }, ("team", "slug"))
    con.execute("UPDATE teams SET roster_at=? WHERE seo=?", (now(), team))
    con.commit()

"""Conference tournament labels from school schedule pages (NCAA teams).

NCAA.com doesn't say which games are conference tournament games. Most schools'
Sidearm schedule pages do: the newer layout groups tournament games under a
heading ("Big South Championship First Round"), the older one shows a coloured
tournament tag, and both may list future rounds with dates ("Atlantic 10
Quarterfinal", Nov 6). We store (team, date, label) and the bracket builder
uses them to know when each conference's tournament starts and what it's called.
"""
from __future__ import annotations

import html as htmlmod
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from .db import upsert
from .ingest import now
from .rosters import UA, _fresh

log = logging.getLogger(__name__)

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TOURNEY_WORDS = re.compile(r"tournament|championship|quarterfinal|semifinal|semi-final|quarter-final|first round|"
                           r"second round|opening round|play-?in|\bfinal\b|\bfinals\b", re.I)
NOT_TOURNEY = re.compile(r"exhibition|scrimmage|classic\b|invitational|showcase|kickoff|kick-off|tip-?off|cup\b", re.I)
TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(TAG.sub(" ", re.sub(r"<!--.*?-->", "", fragment, flags=re.S)))).strip()


def _date(text: str, season: int) -> str | None:
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b", text or "", re.I)
    if not m:
        m2 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text or "")
        if m2:
            return f"{int(m2.group(3)):04d}-{int(m2.group(1)):02d}-{int(m2.group(2)):02d}"
        return None
    mon = MONTHS[m.group(1).lower()[:3]]
    year = season if mon >= 7 else season + 1
    try:
        return date(year, mon, int(m.group(2))).isoformat()
    except ValueError:
        return None


def is_tourney_label(label: str) -> bool:
    return bool(label) and bool(TOURNEY_WORDS.search(label)) and not NOT_TOURNEY.search(label)


def parse_schedule(page: str, season: int) -> list[dict]:
    """[{date, label}] for games the page tags with a tournament."""
    out = []
    # Newer layout: <li list-item> may start with a tournament heading followed by its game cards.
    if "schedule-view-type-list__list-item" in page:
        parts = re.split(r'<li[^>]*data-test-id="schedule-view-type-list__list-item"', page)[1:]
        for li in parts:
            m = re.search(r'data-test-id="schedule-view-type-list__tournament-title"[^>]*>([\s\S]*?)</(?:span|a)>', li)
            if not m:
                m2 = re.search(r'aria-label="([^"]+?) website, opens in a new tab"[^>]*data-test-id="schedule-view-type-list__tournament-title"', li)
                label = htmlmod.unescape(m2.group(1)) if m2 else ""
            else:
                label = _text(m.group(1))
            if not is_tourney_label(label):
                continue
            for d in re.findall(r'data-test-id="s-game-card-standard__header-game-date"[^>]*>([\s\S]{0,200}?)</', li):
                iso = _date(_text(d), season)
                if iso:
                    out.append({"date": iso, "label": label})
    # Older layout: one <li class="sidearm-schedule-game ..."> per game. Tournament games carry a
    # 'tourney' class and are preceded by a separator <li> holding the tournament name.
    if "sidearm-schedule-game-opponent-date" in page:
        games = list(re.finditer(r'<li[^>]*class="sidearm-schedule-game ([^"]*)"', page))
        prev_end = 0
        for i, m in enumerate(games):
            classes = m.group(1).split()
            between = page[prev_end:m.start()]
            prev_end = m.end()
            if "tourney" not in classes:
                continue
            label = ""
            for frag in reversed(re.findall(r"<li[^>]*>([\s\S]{0,600}?)</li>", between[-3000:])):
                t = _text(frag)
                if is_tourney_label(t) and len(t) < 90:
                    label = t
                    break
            if not label and out and out[-1].get("_classic"):
                label = out[-1]["label"]      # several games under one tournament heading
            nxt = games[i + 1].start() if i + 1 < len(games) else m.end() + 20000
            dm = re.search(r'sidearm-schedule-game-opponent-date[^>]*>([\s\S]{0,300}?)</div>', page[m.end():nxt])
            iso = _date(_text(dm.group(1)), season) if dm else None
            if iso:
                out.append({"date": iso, "label": label or "Conference Tournament", "_classic": True})
    # Structured data: placeholder games such as "Vs Atlantic 10 Quarterfinal" on 2026-11-06.
    for name, start in re.findall(r'"name":"\s*(?:Vs\.?|At|@)?\s*([^"]{3,90})","url":[^{}]*?"startDate":"(\d{4}-\d{2}-\d{2})', page):
        if is_tourney_label(name):
            out.append({"date": start, "label": htmlmod.unescape(name).strip()})
    seen, uniq = set(), []
    for e in out:
        k = (e["date"], e["label"].lower())
        if k not in seen:
            seen.add(k)
            uniq.append({"date": e["date"], "label": e["label"]})
    return uniq


def in_playoff_window(today: date | None = None) -> bool:
    today = today or datetime.now(timezone.utc).date()
    return (today.month, today.day) >= (10, 15) and (today.month, today.day) <= (12, 20)


def fetch(con, season: int, max_minutes: float = 5, max_age_hours: float = 12) -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    teams = con.execute(
        "SELECT seo, site, sched_at FROM teams WHERE COALESCE(assoc,'ncaa')='ncaa' AND division IN (1,2) "
        "AND site IS NOT NULL AND site!='' ORDER BY COALESCE(sched_at,'')").fetchall()
    started, n, labelled = time.monotonic(), 0, 0
    for t in teams:
        if time.monotonic() - started > max_minutes * 60:
            break
        if _fresh(t["sched_at"], max_age_hours / 24):
            continue
        entries = []
        for path in (f"/sports/mens-soccer/schedule/{season}", "/sports/mens-soccer/schedule"):
            try:
                r = s.get(t["site"].rstrip("/") + path, timeout=30)
            except requests.RequestException:
                break
            if r.ok and len(r.content) > 20000:
                entries = parse_schedule(r.text, season)
                break
            time.sleep(0.3)
        con.execute("DELETE FROM tourney_labels WHERE team=?", (t["seo"],))
        for e in entries:
            upsert(con, "tourney_labels", {"team": t["seo"], "date": e["date"], "label": e["label"]}, ("team", "date", "label"))
        con.execute("UPDATE teams SET sched_at=? WHERE seo=?", (now(), t["seo"]))
        con.commit()
        n += 1
        labelled += bool(entries)
        time.sleep(0.4)
    log.info("schedules: %d team pages read, %d with tournament labels", n, labelled)
    return n

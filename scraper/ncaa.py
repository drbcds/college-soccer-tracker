"""Client for NCAA.com's public data feed (sdataprod.ncaa.com GraphQL).

Covers every NCAA D1 and D2 men's soccer game: schedule, live score and clock,
player box scores, scoring summary and play-by-play.

The feed uses "persisted queries": each query is addressed by a SHA-256 hash
that NCAA.com's own website sends. If NCAA.com redeploys and a hash stops
working, the error mentions PersistedQueryNotFound; the fix is to copy the new
hash from the network requests of an ncaa.com game page.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.parse

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://sdataprod.ncaa.com/"
SPORT_CODE = "MSO"  # men's soccer

HASHES = {
    "scoreboard": "7287cda610a9326931931080cb3a604828febe6fe3c9016a7e4a36db99efdb7c",
    "game": "93a02c7193c89d85bcdda8c1784925d9b64657f73ef584382e2297af555acd4b",
    "boxscore": "c9070c4e5a76468a4025896df89f8a7b22be8275c54a22ff79619cbb27d63d7d",
    "pbp": "57f922d56d60d88326b62202b3d88e8cd3cfb6687931bc0b5b3dfab089b84faa",
    "scoring": "7f86673d4875cd18102b7fa598e2bc5da3f49d05a1c15b1add0e2367ee890198",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


class FeedError(RuntimeError):
    pass


class NcaaClient:
    def __init__(self, min_interval: float = 0.35):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.min_interval = min_interval
        self._last = 0.0
        self.requests_made = 0

    # -- low level -------------------------------------------------------
    def _query(self, name: str, variables: dict, meta: str | None = None) -> dict:
        ext = json.dumps({"persistedQuery": {"version": 1, "sha256Hash": HASHES[name]}},
                         separators=(",", ":"))
        var = json.dumps(variables, separators=(",", ":"))
        url = f"{ENDPOINT}?"
        if meta:
            url += f"meta={meta}&"
        url += f"extensions={urllib.parse.quote(ext)}&variables={urllib.parse.quote(var)}"

        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.get(url, timeout=30)
                self.requests_made += 1
            except requests.RequestException as e:
                log.warning("%s request failed (%s), retrying", name, e)
                time.sleep(2 ** attempt + random.random())
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 2 + random.random())
                continue
            if r.status_code != 200:
                raise FeedError(f"{name}: HTTP {r.status_code}")
            body = r.json()
            if body.get("errors"):
                raise FeedError(f"{name}: {body['errors'][0].get('message')}")
            return body.get("data") or {}
        raise FeedError(f"{name}: gave up after retries")

    # -- queries ---------------------------------------------------------
    def scoreboard(self, division: int, date: str, season: int) -> list[dict]:
        """All contests on a date. date = 'YYYY/MM/DD' (US date)."""
        data = self._query("scoreboard", {"sportCode": SPORT_CODE, "division": division,
                                          "seasonYear": season, "contestDate": date})
        return data.get("contests") or []

    def game(self, contest_id: int | str) -> dict | None:
        data = self._query("game", {"id": str(contest_id), "week": None, "staticTestEnv": None},
                           meta="GetGamecenterGameById_web")
        contests = data.get("contests") or []
        return contests[0] if contests else None

    def boxscore(self, contest_id: int | str) -> dict | None:
        return self._query("boxscore", {"contestId": str(contest_id), "staticTestEnv": None}).get("boxscore")

    def pbp(self, contest_id: int | str) -> dict | None:
        return self._query("pbp", {"contestId": str(contest_id), "staticTestEnv": None}).get("playbyplay")

    def scoring(self, contest_id: int | str) -> dict | None:
        return self._query("scoring", {"contestId": str(contest_id), "staticTestEnv": None}).get("scoringSummary")

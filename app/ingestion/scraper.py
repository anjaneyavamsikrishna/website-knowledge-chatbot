"""Polite fetcher restricted to the approved URL list.

* identifies itself with a descriptive User-Agent
* checks robots.txt (and honours Crawl-delay if it is larger than our delay)
* ~1 request per second, timeouts, bounded retries with backoff
* NEVER follows links: it can only fetch URLs that were passed in as approved
* a failure is returned as data (FetchResult.ok = False), never raised, so one bad page
  cannot stop the others
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    ok: bool
    html: str = ""
    status_code: int | None = None
    error: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class ApprovedScraper:
    def __init__(
        self,
        approved_urls: list[str],
        *,
        user_agent: str,
        delay_s: float = 1.0,
        timeout_s: float = 20.0,
        max_retries: int = 2,
        session=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self.approved = set(approved_urls)
        self.user_agent = user_agent
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self._robots: dict[str, RobotFileParser | None] = {}
        self._agent_token = user_agent.split("/")[0].split()[0]

    # ------------------------------------------------------------------ rate limiting
    def _wait_turn(self, delay: float | None = None) -> None:
        delay = self.delay_s if delay is None else delay
        if self._last_request_at is not None:
            wait = delay - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _headers(self) -> dict:
        return {"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"}

    # ------------------------------------------------------------------------ robots
    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        parser: RobotFileParser | None
        try:
            self._wait_turn()
            resp = self.session.get(robots_url, headers=self._headers(), timeout=self.timeout_s)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.content.decode("utf-8", errors="replace").splitlines())
            elif 400 <= resp.status_code < 500:
                parser = RobotFileParser()
                parser.parse([])  # no robots.txt => everything allowed
            else:
                log.warning("robots.txt returned HTTP %s; refusing to crawl until it is available", resp.status_code)
                parser = None
        except requests.RequestException as exc:
            log.warning("Could not fetch robots.txt (%s); refusing to crawl", exc.__class__.__name__)
            parser = None
        self._robots[origin] = parser
        return parser

    def _robots_allows(self, url: str) -> tuple[bool, str | None]:
        parser = self._robots_for(url)
        if parser is None:
            return False, "robots.txt unavailable; not crawling"
        if not parser.can_fetch(self._agent_token, url):
            return False, "disallowed by robots.txt"
        return True, None

    # -------------------------------------------------------------------------- fetch
    def fetch(self, url: str) -> FetchResult:
        if url not in self.approved:
            raise ValueError(f"URL is not in the approved source list: {url}")
        allowed, reason = self._robots_allows(url)
        if not allowed:
            log.error("Skipping %s: %s", url, reason)
            return FetchResult(url=url, ok=False, error=reason)

        crawl_delay = None
        parser = self._robots_for(url)
        if parser is not None:
            crawl_delay = parser.crawl_delay(self._agent_token)
        delay = max(self.delay_s, float(crawl_delay)) if crawl_delay else self.delay_s

        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            self._wait_turn(delay)
            try:
                resp = self.session.get(url, headers=self._headers(), timeout=self.timeout_s, allow_redirects=True)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = f"network error: {exc.__class__.__name__}"
            else:
                if resp.status_code == 200:
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if ctype and "html" not in ctype:
                        return FetchResult(url=url, ok=False, status_code=200, error=f"unexpected content type: {ctype}")
                    encoding = resp.encoding
                    if not encoding or encoding.lower() == "iso-8859-1":
                        encoding = "utf-8"
                    html = resp.content.decode(encoding, errors="replace")
                    return FetchResult(url=url, ok=True, html=html, status_code=200)
                if resp.status_code in RETRY_STATUS:
                    last_error = f"HTTP {resp.status_code}"
                else:
                    return FetchResult(url=url, ok=False, status_code=resp.status_code, error=f"HTTP {resp.status_code}")
            if attempt < self.max_retries:
                self._sleep(2 ** attempt)
        log.error("Giving up on %s: %s", url, last_error)
        return FetchResult(url=url, ok=False, error=last_error)

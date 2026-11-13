from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from app.core.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    configured = get_container(request).settings.admin_token
    if not configured:
        raise HTTPException(status_code=503, detail="Admin endpoints are disabled: ADMIN_TOKEN is not configured.")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token header.")


class RateLimiter:
    """Tiny in-memory sliding-window limiter per client IP (protects LLM credits)."""

    def __init__(self, limit_per_minute: int, clock=time.monotonic):
        self.limit, self._clock = limit_per_minute, clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")
            q.append(now)


def rate_limited(request: Request) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    limiter.check(request.client.host if request.client else "unknown")

"""In-memory, per-session chat history with TTL and size cap.
Single-process by design (documented limitation): history is lost on restart and is not
shared between workers. Only (user text, assistant text) pairs are stored."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    turns: list[tuple[str, str]] = field(default_factory=list)
    disclaimer_shown: bool = False
    last_active: float = field(default_factory=time.monotonic)

    def recent(self, n: int) -> list[tuple[str, str]]:
        return self.turns[-n:] if n > 0 else []

    def add(self, user: str, assistant: str, max_turns: int = 20) -> None:
        self.turns.append((user, assistant))
        del self.turns[:-max_turns]
        self.last_active = time.monotonic()


class SessionStore:
    def __init__(self, ttl_s: int = 7200, max_sessions: int = 1000, clock=time.monotonic):
        self.ttl_s, self.max_sessions, self._clock = ttl_s, max_sessions, clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        with self._lock:
            now = self._clock()
            for sid in [s for s, v in self._sessions.items() if now - v.last_active > self.ttl_s]:
                del self._sessions[sid]
            session = self._sessions.get(session_id)
            if session is None:
                if len(self._sessions) >= self.max_sessions:  # evict the least recently active
                    oldest = min(self._sessions.values(), key=lambda s: s.last_active)
                    del self._sessions[oldest.session_id]
                session = Session(session_id, last_active=now)
                self._sessions[session_id] = session
            session.last_active = now
            return session

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)

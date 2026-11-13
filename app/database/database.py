from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.database.models import SCHEMA


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Opens a short-lived connection per operation (safe across FastAPI worker threads)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------ unanswered questions
    def log_unanswered(self, *, question: str, session_id: str | None, retrieval_result: dict, reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO unanswered_questions (question, session_id, created_at, retrieval_result, fallback_reason) VALUES (?,?,?,?,?)",
                (question[:500], session_id, utcnow(), json.dumps(retrieval_result)[:4000], reason[:300]),
            )

    def list_unanswered(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM unanswered_questions ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["retrieval_result"] = json.loads(d["retrieval_result"] or "{}")
            except json.JSONDecodeError:
                pass
            out.append(d)
        return out

    # ------------------------------------------------------------------ source status
    def get_source_status(self, url: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM source_status WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None

    def list_source_status(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM source_status").fetchall()]

    def save_source_result(
        self, *, url: str, label: str, page_type: str, ok: bool, content_hash: str | None,
        chunk_count: int | None, error: str | None,
    ) -> None:
        """On failure the previous hash/chunk_count/last_success are kept (old content stays live)."""
        now = utcnow()
        previous = self.get_source_status(url)
        with self._conn() as conn:
            if ok:
                conn.execute(
                    """INSERT INTO source_status (url,label,page_type,status,content_hash,chunk_count,last_checked_at,last_success_at,last_error)
                       VALUES (?,?,?,?,?,?,?,?,NULL)
                       ON CONFLICT(url) DO UPDATE SET label=excluded.label,page_type=excluded.page_type,status='ok',
                         content_hash=excluded.content_hash,chunk_count=excluded.chunk_count,
                         last_checked_at=excluded.last_checked_at,last_success_at=excluded.last_success_at,last_error=NULL""",
                    (url, label, page_type, "ok", content_hash, chunk_count, now, now),
                )
            else:
                conn.execute(
                    """INSERT INTO source_status (url,label,page_type,status,content_hash,chunk_count,last_checked_at,last_success_at,last_error)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(url) DO UPDATE SET label=excluded.label,page_type=excluded.page_type,status='error',
                         last_checked_at=excluded.last_checked_at,last_error=excluded.last_error""",
                    (url, label, page_type, "error",
                     previous["content_hash"] if previous else None,
                     previous["chunk_count"] if previous else 0, now,
                     previous["last_success_at"] if previous else None, error),
                )

    # ------------------------------------------------------------------ refresh runs
    def record_refresh_run(self, *, started_at: str, finished_at: str, trigger: str, summary: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO refresh_runs (started_at,finished_at,trigger,updated,unchanged,failed,summary_json) VALUES (?,?,?,?,?,?,?)",
                (started_at, finished_at, trigger, summary["updated"], summary["unchanged"], summary["failed"], json.dumps(summary)),
            )

    def last_refresh_run(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM refresh_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["summary"] = json.loads(d.pop("summary_json") or "{}")
        return d

    # ------------------------------------------------------------------------ leads
    def add_lead(self, *, name: str, email: str, consent: bool, session_id: str | None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO leads (name,email,consent,session_id,created_at) VALUES (?,?,?,?,?)",
                (name, email, int(consent), session_id, utcnow()),
            )
            return int(cur.lastrowid)

    def list_leads(self, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

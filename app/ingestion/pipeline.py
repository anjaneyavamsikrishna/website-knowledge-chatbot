"""Fetch -> extract -> chunk -> embed -> store, one approved URL at a time.

Refresh safety guarantees
* a page that fails to fetch/extract/embed leaves its existing chunks untouched
* unchanged pages (same content hash) are not re-embedded
* new chunks are upserted BEFORE stale ones are deleted (no window with missing data)
* deterministic chunk ids + upsert => no duplicate vectors
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from app.core.errors import EmbeddingError, ProviderFatalError
from app.database.database import Database
from app.ingestion.chunker import chunk_page
from app.ingestion.extractor import ExtractionError, extract_page
from app.ingestion.sources import Source
from app.rag.embeddings import Embedder
from app.rag.vector_store import VectorStore

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IngestionPipeline:
    def __init__(
        self, *, sources: list[Source], scraper, store: VectorStore, embedder: Embedder, db: Database,
        chunk_max_chars: int = 1100, min_page_chars: int = 200, chunk_min_chars: int = 400,
        embed_batch: int = 32,
    ):
        self.sources = sources
        self.scraper = scraper
        self.store = store
        self.embedder = embedder
        self.db = db
        self.chunk_max_chars = chunk_max_chars
        self.min_page_chars = min_page_chars
        self.chunk_min_chars = chunk_min_chars
        self.embed_batch = max(1, embed_batch)

    def run(self, *, force: bool = False, trigger: str = "manual") -> dict:
        started = _now()
        results: list[dict] = []
        fatal: str | None = None
        for src in self.sources:
            if fatal:  # a provider problem (no credits, bad key) affects every page: stop wasting requests
                self.db.save_source_result(url=src.url, label=src.label, page_type=src.page_type, ok=False,
                                           content_hash=None, chunk_count=None, error=f"skipped after provider error: {fatal}")
                results.append({"url": src.url, "label": src.label, "status": "failed", "chunks": 0,
                                "error": f"skipped after provider error: {fatal}"})
                continue
            try:
                results.append(self._ingest_one(src, force))
            except (ProviderFatalError, EmbeddingError) as exc:  # affects every page: stop; progress is kept
                fatal = str(exc)
                log.error("ABORTING ingestion: %s", fatal)
                results.append(self._fail(src, fatal))
        summary = {
            "updated": sum(r["status"] == "updated" for r in results),
            "unchanged": sum(r["status"] == "unchanged" for r in results),
            "failed": sum(r["status"] == "failed" for r in results),
            "results": results,
            "started_at": started,
            "finished_at": _now(),
            "total_chunks": self.store.count(),
        }
        self.db.record_refresh_run(started_at=started, finished_at=summary["finished_at"], trigger=trigger, summary=summary)
        log.info("Ingestion finished: %s updated, %s unchanged, %s failed", summary["updated"], summary["unchanged"], summary["failed"])
        return summary

    def _fail(self, src: Source, error: str) -> dict:
        log.error("Ingestion failed for %s: %s", src.url, error)
        self.db.save_source_result(url=src.url, label=src.label, page_type=src.page_type, ok=False, content_hash=None, chunk_count=None, error=error)
        return {"url": src.url, "label": src.label, "status": "failed", "chunks": 0, "error": error}

    def _ingest_one(self, src: Source, force: bool) -> dict:
        fetched = self.scraper.fetch(src.url)
        if not fetched.ok:
            return self._fail(src, fetched.error or "fetch failed")
        try:
            page = extract_page(
                fetched.html, url=src.url, page_type=src.page_type,
                content_selector=src.content_selector, min_chars=self.min_page_chars,
            )
        except ExtractionError as exc:
            return self._fail(src, f"extraction failed: {exc}")
        except Exception as exc:  # parser bug on odd markup must not stop other pages
            log.exception("Unexpected extraction error for %s", src.url)
            return self._fail(src, f"extraction error: {exc.__class__.__name__}")

        content_hash = hashlib.sha256(page.text.encode("utf-8")).hexdigest()
        previous = self.db.get_source_status(src.url)
        existing_ids = self.store.ids_for_source(src.url)
        if not force and previous and previous.get("content_hash") == content_hash and existing_ids:
            self.db.save_source_result(url=src.url, label=src.label, page_type=src.page_type, ok=True,
                                       content_hash=content_hash, chunk_count=len(existing_ids), error=None)
            return {"url": src.url, "label": src.label, "status": "unchanged", "chunks": len(existing_ids), "error": None}

        chunks = chunk_page(page, max_chars=self.chunk_max_chars, extracted_at=fetched.fetched_at, content_version=content_hash[:12],
                            min_chunk_chars=self.chunk_min_chars)
        if not chunks:
            return self._fail(src, "no chunks produced")
        existing = set(existing_ids)
        new_ids = {c.chunk_id for c in chunks}
        to_embed = [c for c in chunks if force or c.chunk_id not in existing]
        reused = [c for c in chunks if c.chunk_id in existing and not force]
        try:
            # Embed batch by batch and store each batch immediately: if a rate limit interrupts the run,
            # what is done is kept and the next run only embeds the missing chunks (resumable).
            for i in range(0, len(to_embed), self.embed_batch):
                batch = to_embed[i : i + self.embed_batch]
                vectors = self.embedder.embed_documents([c.text for c in batch])
                self.store.upsert([c.chunk_id for c in batch], [c.text for c in batch], vectors, [c.metadata for c in batch])
            self.store.update_metadata([c.chunk_id for c in reused], [c.metadata for c in reused])
            self.store.delete_ids([i for i in existing_ids if i not in new_ids])
        except (ProviderFatalError, EmbeddingError):
            raise
        except Exception as exc:
            log.error("Embedding/storage failed for %s: %s: %s", src.url, exc.__class__.__name__, exc)
            return self._fail(src, f"embedding/storage failed: {exc.__class__.__name__}: {exc}")
        log.info("%s: %s new chunk(s) embedded, %s reused", src.url, len(to_embed), len(reused))

        self.db.save_source_result(url=src.url, label=src.label, page_type=src.page_type, ok=True,
                                   content_hash=content_hash, chunk_count=len(chunks), error=None)
        return {"url": src.url, "label": src.label, "status": "updated", "chunks": len(chunks), "error": None}

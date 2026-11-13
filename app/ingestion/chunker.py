"""Section-aware chunking with price protection.

* chunks follow the page's heading structure (never fixed-size slices across sections)
* every chunk is prefixed with the page title and section path so it is self-describing
* a price block is kept together with the short lead-in block before it
* long blocks are split on sentence boundaries; a sentence holding a price is never cut
* chunk ids are deterministic (re-ingesting identical content yields identical ids)
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.core.textutils import has_price
from app.ingestion.extractor import Block, ExtractedPage


@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: dict


def _units(blocks: list[Block]) -> list[list[str]]:
    """Group blocks so that a price block stays with the short block that introduces it."""
    units: list[list[str]] = []
    for b in blocks:
        t = b.text
        if (
            has_price(t)
            and units
            and len(units[-1]) == 1
            and not has_price(units[-1][0])
            and len(units[-1][0]) <= 200
        ):
            units[-1].append(t)
        else:
            units.append([t])
    return units


def _split_long(text: str, budget: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > budget:  # extreme case: a single huge sentence -> split on spaces
            cut = s.rfind(" ", 0, budget)
            cut = cut if cut > 0 else budget
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > budget:
            pieces.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def chunk_page(page: ExtractedPage, *, max_chars: int, extracted_at: str, content_version: str,
               min_chunk_chars: int = 400) -> list[Chunk]:
    """Chunks follow headings, but tiny sections are merged with the following ones (up to max_chars) so a page
    with 130 one-line sections does not become 130 near-empty vectors. Merged sections are labelled inline with
    '[Section: ...]'. Price units are never split."""
    url_tag = hashlib.sha1(page.url.encode()).hexdigest()[:6]
    budget = max(300, max_chars - len(page.title) - 100)
    chunks: list[Chunk] = []
    seen_ids: set[str] = set()

    def emit(first_path: str, parts: list[str]) -> None:
        body = "\n".join(parts).strip()
        if not body:
            return
        text = f"{page.title}\nSection: {first_path}\n{body}"
        # id depends only on page + text, NOT on position: editing one paragraph leaves every other
        # chunk id unchanged, so unchanged chunks are never re-embedded.
        chunk_id = f"{page.page_type}-{url_tag}-{hashlib.sha1(text.encode()).hexdigest()[:12]}"
        if chunk_id in seen_ids:
            return
        seen_ids.add(chunk_id)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                text=text,
                metadata={
                    "source_url": page.url,
                    "page_title": page.title,
                    "page_type": page.page_type,
                    "chunk_id": chunk_id,
                    "extracted_at": extracted_at,
                    "content_version": content_version,
                    "section": first_path,
                    "chunk_index": len(chunks),
                },
            )
        )

    parts: list[str] = []
    length = 0
    first_path = last_path = ""

    def flush() -> None:
        nonlocal parts, length, first_path, last_path
        if parts:
            emit(first_path, parts)
        parts, length, first_path, last_path = [], 0, "", ""

    for section in page.sections:
        pieces: list[str] = []
        for unit in _units(section.blocks):
            joined = "\n".join(unit)
            if len(joined) <= budget:
                pieces.append(joined)
            else:
                for t in unit:
                    pieces.extend(_split_long(t, budget) if len(t) > budget else [t])
        if parts and length >= min_chunk_chars:
            flush()  # current chunk is already substantial: start a fresh one at the section boundary
        for piece in pieces:
            text = piece
            if parts and last_path != section.heading_path:
                text = f"[Section: {section.heading_path}]\n{piece}"
            add = len(text) + (1 if parts else 0)
            if parts and length + add > budget:
                flush()
                text, add = piece, len(piece)
            if not parts:
                first_path = section.heading_path
            parts.append(text)
            length += add
            last_path = section.heading_path
    flush()
    return chunks

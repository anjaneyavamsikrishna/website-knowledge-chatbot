"""Approved source list. Only URLs in config/sources.json may ever be fetched."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PAGE_TYPES = {"clinic_profile", "treatment_package", "clinic_videos", "price_list", "price_comparison"}


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    page_type: str
    url: str
    content_selector: str | None = None  # optional CSS selector to scope extraction


def load_sources(path: Path) -> list[Source]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed_hosts = set(data.get("allowed_hosts", []))
    sources: list[Source] = []
    seen: set[str] = set()
    for raw in data["sources"]:
        url = raw["url"].strip()
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError(f"Source must use https: {url}")
        if allowed_hosts and parsed.netloc not in allowed_hosts:
            raise ValueError(f"Host not allowed for source: {url}")
        if raw["page_type"] not in PAGE_TYPES:
            raise ValueError(f"Unknown page_type '{raw['page_type']}' for {url}")
        if url in seen:
            raise ValueError(f"Duplicate source URL: {url}")
        seen.add(url)
        sources.append(
            Source(
                id=raw["id"], label=raw["label"], page_type=raw["page_type"],
                url=url, content_selector=raw.get("content_selector"),
            )
        )
    if not sources:
        raise ValueError("No sources configured")
    return sources

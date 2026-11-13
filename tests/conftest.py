from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.container import build_container
from app.core.prompts import ANALYZER_SYSTEM, GENERATOR_SYSTEM
from app.ingestion.scraper import FetchResult
from app.rag.embeddings import HashEmbedder

FIX = Path(__file__).parent / "fixtures"
PKG_URL = "https://www.placidway.com/package/7902/Demo-Adrenal-Package"
PRICE_URL = "https://www.placidway.com/price-list/5/Demo-Clinic"
PROFILE_URL = "https://www.placidway.com/profile/5/Demo-Clinic"

SOURCES_JSON = {
    "allowed_hosts": ["www.placidway.com"],
    "sources": [
        {"id": "profile", "label": "Profile", "page_type": "clinic_profile", "url": PROFILE_URL},
        {"id": "pkg", "label": "Package", "page_type": "treatment_package", "url": PKG_URL},
        {"id": "prices", "label": "Prices", "page_type": "price_list", "url": PRICE_URL},
    ],
}
HTML = {
    PROFILE_URL: (FIX / "profile.html").read_text(),
    PKG_URL: (FIX / "package.html").read_text(),
    PRICE_URL: (FIX / "price_list.html").read_text(),
}


class FakeScraper:
    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = dict(HTML if pages is None else pages)
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url in self.fail or url not in self.pages:
            return FetchResult(url=url, ok=False, error="HTTP 503")
        return FetchResult(url=url, ok=True, html=self.pages[url], status_code=200)


class ScriptedLLM:
    """Fake LLM. Routes by system prompt: analyzer vs generator. Records every prompt."""

    def __init__(self):
        self.analysis: dict = {"language": "en", "standalone_question": None, "intent": "general_info", "medical_context": False}
        self.generation: dict = {"answerable": True, "answer": "ok", "used_sources": ["S1"]}
        self.analyzer_prompts: list[str] = []
        self.generator_prompts: list[str] = []

    def complete(self, system, user, *, json_mode=False, max_tokens=None):
        if system == ANALYZER_SYSTEM:
            self.analyzer_prompts.append(user)
            data = dict(self.analysis)
            if not data.get("standalone_question"):
                data["standalone_question"] = user.split("LATEST USER MESSAGE:\n")[-1]
            return json.dumps(data)
        assert system == GENERATOR_SYSTEM
        self.generator_prompts.append(user)
        return json.dumps(self.generation)


@pytest.fixture
def settings(tmp_path):
    src = tmp_path / "sources.json"
    src.write_text(json.dumps(SOURCES_JSON))
    return Settings(
        data_dir=tmp_path / "data", sources_file=src, embedding_provider="hash", llm_api_key="",
        similarity_threshold=0.20, admin_token="secret-admin", rate_limit_per_minute=1000,
        min_page_chars=100, _env_file=None,
    )


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def scraper():
    return FakeScraper()


@pytest.fixture
def container(settings, llm, scraper):
    c = build_container(settings, llm=llm, embedder=HashEmbedder(), scraper=scraper)
    return c


@pytest.fixture
def ingested(container):
    report = container.refresh_service.refresh(trigger="test")
    assert report["failed"] == 0, report
    return container

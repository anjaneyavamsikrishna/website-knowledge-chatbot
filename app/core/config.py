"""Application configuration. Every tunable value comes from environment variables
(or a local .env file). No secrets live in the source code."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- general -------------------------------------------------------------
    app_name: str = "PlacidWay Website Knowledge Chatbot"
    log_level: str = "INFO"
    admin_token: str = ""  # empty => admin endpoints are disabled (503)
    contact_url: str = "https://www.placidway.com/"  # set to the real quote/contact page
    auto_ingest_on_startup: bool = False  # build the KB at boot when it is empty

    # --- LLM (OpenAI-compatible API or Anthropic) ---------------------------
    llm_provider: str = "openai"  # openai | gemini (native Google API) | anthropic
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_base_url: str = ""  # e.g. Gemini/Groq/Ollama OpenAI-compatible endpoint
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1200  # generous: some models spend part of this budget on hidden reasoning
    llm_timeout_s: float = 30.0
    llm_min_interval_s: float = 0.0  # pause between model calls; use ~6 on free tiers (2 calls per question)

    # --- embeddings -----------------------------------------------------------
    embedding_provider: str = "openai"  # openai | sentence-transformers | hash (tests only)
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""  # falls back to llm_api_key
    embedding_base_url: str = ""
    embedding_batch_size: int = 32
    embedding_min_interval_s: float = 1.0  # pause between embedding requests (free-tier friendly)

    # --- retrieval ------------------------------------------------------------
    retrieval_top_k: int = 5
    retrieval_candidates: int = 12
    similarity_threshold: float = 0.30  # cosine similarity gate; calibrate per model
    max_context_chars: int = 9000

    # --- ingestion ------------------------------------------------------------
    sources_file: Path = BASE_DIR / "config" / "sources.json"
    crawler_user_agent: str = (
        "PlacidWayAssessmentBot/1.0 (educational assessment; contact: your-email@example.com)"
    )
    request_delay_s: float = 1.0
    request_timeout_s: float = 20.0
    request_max_retries: int = 2
    chunk_max_chars: int = 1100
    chunk_min_chars: int = 400  # smaller chunks are merged with the following section(s)
    min_page_chars: int = 200

    # --- storage --------------------------------------------------------------
    data_dir: Path = BASE_DIR / "data"

    # --- chat -----------------------------------------------------------------
    max_history_turns: int = 6
    session_ttl_s: int = 7200
    max_sessions: int = 1000
    max_question_chars: int = 1000
    rate_limit_per_minute: int = 20

    @field_validator("data_dir", "sources_file", mode="after")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (BASE_DIR / v)

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma_db"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "app.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
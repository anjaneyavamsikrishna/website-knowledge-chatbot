"""Wires the application together (also reused by CLI scripts and tests)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.database.database import Database
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.scraper import ApprovedScraper
from app.ingestion.sources import Source, load_sources
from app.rag.embeddings import Embedder, build_embedder
from app.rag.generator import AnswerGenerator
from app.rag.llm import LLMClient, build_llm
from app.rag.query_analyzer import QueryAnalyzer
from app.rag.retriever import Retriever
from app.rag.vector_store import VectorStore, collection_name_for
from app.services.chat_service import ChatService
from app.services.refresh_service import RefreshService
from app.services.sessions import SessionStore

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    store: VectorStore
    embedder: Embedder
    llm: LLMClient | None
    sources: list[Source]
    retriever: Retriever
    sessions: SessionStore
    chat_service: ChatService
    refresh_service: RefreshService


def build_container(settings: Settings | None = None, *, llm=None, embedder=None, scraper=None) -> Container:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.sqlite_path)
    embedder = embedder or build_embedder(settings)
    store = VectorStore(settings.chroma_dir, collection_name_for(embedder.name))
    llm = llm if llm is not None else build_llm(settings)
    sources = load_sources(settings.sources_file)
    scraper = scraper or ApprovedScraper(
        [s.url for s in sources], user_agent=settings.crawler_user_agent, delay_s=settings.request_delay_s,
        timeout_s=settings.request_timeout_s, max_retries=settings.request_max_retries,
    )
    retriever = Retriever(
        store, embedder, threshold=settings.similarity_threshold,
        top_k=settings.retrieval_top_k, candidates=settings.retrieval_candidates,
    )
    sessions = SessionStore(settings.session_ttl_s, settings.max_sessions)
    chat = ChatService(
        settings=settings, retriever=retriever, analyzer=QueryAnalyzer(llm),
        generator=AnswerGenerator(llm, max_context_chars=settings.max_context_chars), sessions=sessions, db=db,
    )
    pipeline = IngestionPipeline(
        sources=sources, scraper=scraper, store=store, embedder=embedder, db=db,
        chunk_max_chars=settings.chunk_max_chars, min_page_chars=settings.min_page_chars,
        chunk_min_chars=settings.chunk_min_chars, embed_batch=settings.embedding_batch_size,
    )
    return Container(settings, db, store, embedder, llm, sources, retriever, sessions, chat, RefreshService(pipeline))

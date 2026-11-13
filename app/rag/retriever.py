"""Hybrid retrieval with a hard relevance gate.

1. semantic search in ChromaDB (cosine similarity), optionally filtered by page_type
2. GATE: only passages with similarity >= threshold survive (this is what makes
   "I don't know" possible: if nothing survives, the LLM is never called)
3. survivors are re-ranked by Reciprocal Rank Fusion of semantic rank + BM25 keyword rank
   (keywords rescue exact terms such as treatment names and numbers)
If a metadata-filtered search finds nothing relevant, it retries unfiltered.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from app.rag.embeddings import Embedder, tokenize
from app.rag.vector_store import VectorStore

# Which page types are most likely to hold the answer for each intent (None = search all).
INTENT_PAGE_TYPES: dict[str, list[str] | None] = {
    "cost": ["price_list", "price_comparison", "treatment_package", "clinic_profile"],
    "comparison": ["price_comparison", "price_list", "treatment_package"],
    "package_details": ["treatment_package", "clinic_profile"],
    "clinic_info": ["clinic_profile", "clinic_videos", "treatment_package"],
    "videos": ["clinic_videos"],
}


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float  # cosine similarity
    metadata: dict


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    top_score: float = 0.0
    used_filter: bool = False
    filter_page_types: list[str] | None = None
    reason: str = "ok"  # ok | empty_kb | below_threshold
    candidates: list[dict] = field(default_factory=list)  # for logging/debugging

    def log_dict(self) -> dict:
        return {
            "reason": self.reason, "top_score": round(self.top_score, 4), "used_filter": self.used_filter,
            "candidates": self.candidates[:8],
        }


def bm25_scores(query_tokens: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in set(query_tokens):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avgdl))
        scores.append(s)
    return scores


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, *, threshold: float, top_k: int, candidates: int):
        self.store, self.embedder = store, embedder
        self.threshold, self.top_k, self.n_candidates = threshold, top_k, candidates

    def is_empty(self) -> bool:
        return self.store.count() == 0

    def retrieve(self, query: str, page_types: list[str] | None = None) -> RetrievalResult:
        if self.is_empty():
            return RetrievalResult(reason="empty_kb")
        embedding = self.embedder.embed_query(query)

        if page_types:
            result = self._search(query, embedding, page_types)
            if result.chunks:
                return result
            fallback = self._search(query, embedding, None)
            fallback.candidates = result.candidates + fallback.candidates
            return fallback
        return self._search(query, embedding, None)

    def _search(self, query: str, embedding: list[float], page_types: list[str] | None) -> RetrievalResult:
        where = {"page_type": {"$in": page_types}} if page_types else None
        hits = self.store.query(embedding, self.n_candidates, where)
        scored = [
            RetrievedChunk(h["id"], h["text"], 1.0 - float(h["distance"]), h["metadata"]) for h in hits
        ]
        debug = [
            {"chunk_id": c.chunk_id, "url": c.metadata.get("source_url"), "score": round(c.score, 4), "filtered": bool(page_types)}
            for c in scored
        ]
        passing = [c for c in scored if c.score >= self.threshold]
        if not passing:
            return RetrievalResult(
                top_score=max((c.score for c in scored), default=0.0), used_filter=bool(page_types),
                filter_page_types=page_types, reason="below_threshold", candidates=debug,
            )

        # keyword rank over the same pool (BM25 on the whole allowed corpus => meaningful IDF)
        corpus = self.store.all_documents(where)
        tokens_by_id = {d["id"]: tokenize(d["text"]) for d in corpus}
        ids = list(tokens_by_id)
        lex = dict(zip(ids, bm25_scores(tokenize(query), [tokens_by_id[i] for i in ids])))
        lex_rank = {cid: r for r, cid in enumerate(sorted((c.chunk_id for c in passing), key=lambda i: -lex.get(i, 0.0)))}
        sem_rank = {c.chunk_id: r for r, c in enumerate(sorted(passing, key=lambda c: -c.score))}
        fused = sorted(passing, key=lambda c: -(1 / (60 + sem_rank[c.chunk_id]) + 1 / (60 + lex_rank[c.chunk_id])))
        top = fused[: self.top_k]
        return RetrievalResult(
            chunks=top, top_score=max(c.score for c in top), used_filter=bool(page_types),
            filter_page_types=page_types, reason="ok", candidates=debug,
        )

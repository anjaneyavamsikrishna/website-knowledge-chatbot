"""ChromaDB wrapper. Vectors are supplied explicitly (Chroma never embeds on its own),
and the collection name is derived from the embedding model so switching models can never
mix incompatible vectors."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def collection_name_for(embedder_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", embedder_name).strip("_")[:40]
    return f"pw_{slug}_{hashlib.sha1(embedder_name.encode()).hexdigest()[:6]}"


class VectorStore:
    def __init__(self, persist_dir: Path, collection_name: str):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_dir), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._col = self._client.get_or_create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
        self.collection_name = collection_name

    def count(self) -> int:
        return self._col.count()

    def upsert(self, ids: list[str], documents: list[str], embeddings: list[list[float]], metadatas: list[dict]) -> None:
        if ids:
            self._col.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    def update_metadata(self, ids: list[str], metadatas: list[dict]) -> None:
        if ids:
            self._col.update(ids=ids, metadatas=metadatas)

    def ids_for_source(self, url: str) -> list[str]:
        return list(self._col.get(where={"source_url": url}, include=[])["ids"])

    def delete_ids(self, ids: list[str]) -> None:
        if ids:
            self._col.delete(ids=ids)

    def query(self, embedding: list[float], n_results: int, where: dict | None = None) -> list[dict]:
        total = self._col.count()
        if total == 0:
            return []
        res = self._col.query(
            query_embeddings=[embedding],
            n_results=max(1, min(n_results, total)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            {"id": i, "text": d, "metadata": m, "distance": dist}
            for i, d, m, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    def all_documents(self, where: dict | None = None) -> list[dict]:
        res = self._col.get(where=where, include=["documents", "metadatas"])
        return [{"id": i, "text": d, "metadata": m} for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]

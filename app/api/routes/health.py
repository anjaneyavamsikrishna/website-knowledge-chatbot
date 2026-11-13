from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.core.container import Container

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(container: Container = Depends(get_container)) -> dict:
    chunks = container.store.count()
    last = container.db.last_refresh_run()
    return {
        "status": "ok",
        "knowledge_base_ready": chunks > 0,
        "chunks": chunks,
        "llm_configured": container.llm is not None,
        "embedding_model": container.embedder.name,
        "refresh_running": container.refresh_service.running,
        "last_refresh": None if not last else {
            "finished_at": last["finished_at"], "updated": last["updated"],
            "unchanged": last["unchanged"], "failed": last["failed"],
        },
    }


@router.get("/sources")
def sources(container: Container = Depends(get_container)) -> list[dict]:
    status = {s["url"]: s for s in container.db.list_source_status()}
    out = []
    for src in container.sources:
        st = status.get(src.url)
        out.append({
            "id": src.id, "label": src.label, "page_type": src.page_type, "url": src.url,
            "status": st["status"] if st else "never",
            "chunk_count": st["chunk_count"] if st else 0,
            "last_success_at": st["last_success_at"] if st else None,
            "last_checked_at": st["last_checked_at"] if st else None,
            "last_error": st["last_error"] if st else None,
            "content_version": (st["content_hash"] or "")[:12] if st else None,
        })
    return out

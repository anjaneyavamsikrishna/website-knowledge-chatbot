from fastapi import APIRouter, Depends

from app.api.deps import get_container, require_admin
from app.core.container import Container

router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/ingest", dependencies=[Depends(require_admin)])
def ingest(force: bool = False, container: Container = Depends(get_container)) -> dict:
    """Refresh the knowledge base from the approved URLs only. `force=true` re-embeds unchanged pages."""
    return container.refresh_service.refresh(force=force, trigger="api")

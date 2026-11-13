from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_admin
from app.core.container import Container

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/logs/unanswered")
def unanswered(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               container: Container = Depends(get_container)) -> dict:
    items = container.db.list_unanswered(limit, offset)
    return {"count": len(items), "items": items}


@router.get("/logs/leads")
def leads(limit: int = Query(100, ge=1, le=500), container: Container = Depends(get_container)) -> dict:
    items = container.db.list_leads(limit)
    return {"count": len(items), "items": items}

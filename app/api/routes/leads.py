"""Lead capture is deliberately separate from the RAG pipeline. Leads are stored locally in
SQLite only; nothing is sent to PlacidWay (there is no real integration), and the response
says so honestly."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container, rate_limited
from app.api.schemas import LeadRequest, LeadResponse
from app.core.container import Container

router = APIRouter(prefix="/api", tags=["leads"])


@router.post("/leads", response_model=LeadResponse, dependencies=[Depends(rate_limited)])
def create_lead(body: LeadRequest, container: Container = Depends(get_container)) -> LeadResponse:
    if not body.consent:
        raise HTTPException(status_code=400, detail="Consent is required to save contact details.")
    container.db.add_lead(name=body.name, email=body.email, consent=True, session_id=body.session_id)
    return LeadResponse(
        saved=True,
        message=("Thank you. Your details were saved in this demo application. This does NOT submit a quote request "
                 "to PlacidWay, so please also contact PlacidWay directly for a free quote."),
    )

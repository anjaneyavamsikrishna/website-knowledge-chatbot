import logging
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import get_container, rate_limited
from app.api.schemas import ChatRequest, ChatResponse, SourceRef
from app.core.container import Container

router = APIRouter(prefix="/api", tags=["chat"])
log = logging.getLogger(__name__)


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(rate_limited)])
def chat(body: ChatRequest, container: Container = Depends(get_container)) -> ChatResponse:
    session_id = body.session_id or uuid.uuid4().hex
    result = container.chat_service.handle(body.question, session_id)
    return ChatResponse(
        answer=result.answer,
        sources=[SourceRef(**s) for s in result.sources],
        session_id=session_id,
        intent=result.intent,
        answered=result.answered,
        language=result.language,
        suggest_lead_capture=result.suggest_lead_capture,
        disclaimer_included=result.disclaimer_included,
        metadata=result.metadata,
    )


@router.delete("/chat/{session_id}", status_code=204)
def clear_session(session_id: str, container: Container = Depends(get_container)) -> None:
    container.sessions.clear(session_id)

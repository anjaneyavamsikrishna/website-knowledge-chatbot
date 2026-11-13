"""Chat orchestration. Order of defence (cheapest / most deterministic first):

  1. input normalisation + prompt-injection rule          (no LLM)
  2. query analysis: language, standalone rewrite, intent (1 LLM call, fail-safe)
  3. booking / personal-medical / quote / greeting / out-of-scope routing (rules + intent)
  4. retrieval with relevance gate  -> nothing relevant => canned fallback, LLM NOT called
  5. grounded generation (passages only) -> model may itself say "not answerable"
  6. post-checks: every currency amount in the answer must exist in the evidence;
     unapproved URLs are stripped; sources are mapped from labels in code
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.errors import KnowledgeBaseEmpty, LLMError, LLMUnavailable
from app.core.prompts import message
from app.database.database import Database
from app.rag.generator import AnswerGenerator, build_context
from app.rag.guardrails import (
    claim_note, detect_booking, detect_personal_medical, detect_prompt_injection,
    heuristic_language, normalize_question, redact_pii, strip_unapproved_urls, unsupported_amounts,
)
from app.rag.query_analyzer import QueryAnalyzer
from app.rag.retriever import INTENT_PAGE_TYPES, RetrievalResult, Retriever
from app.services.sessions import SessionStore

log = logging.getLogger(__name__)


@dataclass
class ChatResult:
    answer: str
    intent: str
    answered: bool  # True only when a grounded answer from the website content was produced
    language: str
    sources: list[dict] = field(default_factory=list)
    suggest_lead_capture: bool = False
    disclaimer_included: bool = False
    metadata: dict = field(default_factory=dict)


class ChatService:
    def __init__(
        self, *, settings: Settings, retriever: Retriever, analyzer: QueryAnalyzer,
        generator: AnswerGenerator, sessions: SessionStore, db: Database,
    ):
        self.s, self.retriever, self.analyzer = settings, retriever, analyzer
        self.generator, self.sessions, self.db = generator, sessions, db

    # ------------------------------------------------------------------------ helpers
    def _canned(self, key: str, lang: str) -> str:
        return message(key, lang, self.s.contact_url)

    def _log_unanswered(self, question: str, session_id: str, retrieval: RetrievalResult | None, reason: str) -> None:
        try:
            self.db.log_unanswered(
                question=redact_pii(question), session_id=session_id,
                retrieval_result=retrieval.log_dict() if retrieval else {}, reason=reason,
            )
        except Exception:  # logging must never break a chat turn
            log.exception("Failed to log unanswered question")

    def _finish(self, session, question: str, answer: str, *, intent: str, answered: bool, lang: str,
                sources=None, lead=False, disclaimer=False, metadata=None, remember=True) -> ChatResult:
        if remember:
            session.add(question, answer)
        return ChatResult(
            answer=answer, intent=intent, answered=answered, language=lang, sources=sources or [],
            suggest_lead_capture=lead, disclaimer_included=disclaimer, metadata=metadata or {},
        )

    # ------------------------------------------------------------------------- main
    def handle(self, raw_question: str, session_id: str) -> ChatResult:
        question = normalize_question(raw_question, self.s.max_question_chars)
        if not question:
            raise ValueError("empty question")
        session = self.sessions.get(session_id)

        # 1. prompt injection: refuse deterministically, do not even send it to the LLM
        if detect_prompt_injection(question):
            lang = heuristic_language(question)
            return self._finish(session, question, self._canned("injection", lang), intent="prompt_injection",
                                answered=False, lang=lang, remember=False)

        # 2. understand the question (language, standalone rewrite, intent)
        analysis = self.analyzer.analyze(question, session.recent(self.s.max_history_turns))
        lang, intent = analysis.language, analysis.intent
        meta: dict = {
            "analysis": {"standalone_question": analysis.standalone_question, "language": lang,
                         "medical_context": analysis.medical_context, "used_llm": analysis.used_llm},
        }

        # 3. routing that needs no retrieval
        if detect_booking(question) or intent == "booking":
            return self._finish(session, question, self._canned("booking", lang), intent="booking",
                                answered=False, lang=lang, lead=True, metadata=meta)
        if (detect_personal_medical(question) or detect_personal_medical(analysis.standalone_question)
                or intent == "medical_advice"):
            session.disclaimer_shown = True  # the refusal already carries the advice to see a doctor
            return self._finish(session, question, self._canned("medical_refusal", lang), intent="medical_advice",
                                answered=False, lang=lang, disclaimer=True, metadata=meta)
        if intent == "quote_or_lead":
            return self._finish(session, question, self._canned("quote", lang), intent=intent,
                                answered=False, lang=lang, lead=True, metadata=meta)
        if intent == "greeting":
            return self._finish(session, question, self._canned("greeting", lang), intent=intent,
                                answered=False, lang=lang, metadata=meta)
        if intent == "out_of_scope":
            return self._finish(session, question, self._canned("out_of_scope", lang), intent=intent,
                                answered=False, lang=lang, metadata=meta)

        # 4. retrieval + relevance gate
        if self.retriever.is_empty():
            raise KnowledgeBaseEmpty(self._canned("kb_empty", lang))
        retrieval = self.retriever.retrieve(analysis.standalone_question, INTENT_PAGE_TYPES.get(intent))
        meta["retrieval"] = {
            **retrieval.log_dict(), "threshold": self.retriever.threshold,
            "retrieved": [
                {"chunk_id": c.chunk_id, "url": c.metadata.get("source_url"), "page_type": c.metadata.get("page_type"),
                 "score": round(c.score, 4), "snippet": c.text[:240]}
                for c in retrieval.chunks
            ],
        }
        if not retrieval.chunks:
            self._log_unanswered(question, session_id, retrieval, "no_relevant_content")
            return self._finish(session, question, self._canned("fallback", lang), intent=intent,
                                answered=False, lang=lang, metadata=meta)

        # 5. grounded generation
        context_text, labelled = build_context(retrieval.chunks, self.s.max_context_chars)
        evidence = "\n".join(c.text for _, c in labelled)
        try:
            generated = self.generator.generate(
                question=question, standalone_question=analysis.standalone_question, language=lang,
                labelled_context=context_text, claim_note=claim_note(question, evidence),
            )
        except LLMError as exc:
            log.error("Generation failed: %s", exc)
            raise LLMUnavailable() from exc

        if not generated.answerable:
            self._log_unanswered(question, session_id, retrieval, "model_found_no_answer")
            return self._finish(session, question, self._canned("fallback", lang), intent=intent,
                                answered=False, lang=lang, metadata=meta)

        # 6. post-checks
        by_label = dict(labelled)
        allowed_urls = {c.metadata["source_url"] for _, c in labelled}
        answer = strip_unapproved_urls(generated.answer, allowed_urls)

        bad = unsupported_amounts(answer, evidence, question)
        if bad:
            log.warning("Blocked answer with amounts not found in evidence: %s", bad)
            self._log_unanswered(question, session_id, retrieval, f"unsupported_amount: {', '.join(bad)}")
            meta["blocked_amounts"] = bad
            return self._finish(session, question, self._canned("fallback", lang), intent=intent,
                                answered=False, lang=lang, metadata=meta)

        cited = [by_label[l] for l in generated.used_labels if l in by_label]
        if not cited:  # model forgot/garbled labels: cite the single best passage, and say so
            cited = [labelled[0][1]]
            meta["citation_fallback"] = True
        sources, seen = [], set()
        for chunk in cited:
            url = chunk.metadata["source_url"]
            if url not in seen:
                seen.add(url)
                sources.append({"url": url, "title": chunk.metadata.get("page_title", url),
                                "page_type": chunk.metadata.get("page_type", "")})

        disclaimer = False
        if analysis.medical_context and not session.disclaimer_shown:
            answer = f"{answer}\n\n{self._canned('disclaimer', lang)}"
            session.disclaimer_shown = disclaimer = True

        return self._finish(session, question, answer, intent=intent, answered=True, lang=lang,
                            sources=sources, disclaimer=disclaimer, metadata=meta)

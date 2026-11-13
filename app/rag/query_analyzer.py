"""One cheap LLM call that (a) detects the user's language, (b) rewrites follow-ups into a
standalone English question using chat history, and (c) classifies intent.
It never answers anything and fails safe: on any error the raw question is used."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.errors import LLMError
from app.core.prompts import ANALYZER_SYSTEM, INTENTS
from app.rag.guardrails import heuristic_language
from app.rag.llm import LLMClient, extract_json

log = logging.getLogger(__name__)


@dataclass
class QueryAnalysis:
    language: str
    standalone_question: str
    intent: str  # one of INTENTS or "unknown"
    medical_context: bool
    used_llm: bool


class QueryAnalyzer:
    def __init__(self, llm: LLMClient | None):
        self.llm = llm

    @staticmethod
    def _format_history(history: list[tuple[str, str]]) -> str:
        if not history:
            return "(no earlier messages)"
        lines = []
        for user, assistant in history:
            lines.append(f"User: {user[:400]}")
            lines.append(f"Assistant: {assistant[:400]}")
        return "\n".join(lines)

    def analyze(self, question: str, history: list[tuple[str, str]]) -> QueryAnalysis:
        fallback = QueryAnalysis(heuristic_language(question), question, "unknown", False, False)
        if self.llm is None:
            return fallback
        prompt = f"RECENT CONVERSATION:\n{self._format_history(history)}\n\nLATEST USER MESSAGE:\n{question}"
        try:
            data = extract_json(self.llm.complete(ANALYZER_SYSTEM, prompt, json_mode=True, max_tokens=600))
        except LLMError as exc:
            log.warning("Query analysis failed, using raw question: %s", exc)
            return fallback
        intent = str(data.get("intent", "unknown")).strip().lower()
        standalone = str(data.get("standalone_question") or "").strip()[:500] or question
        return QueryAnalysis(
            language=str(data.get("language") or fallback.language).strip().lower()[:5] or "en",
            standalone_question=standalone,
            intent=intent if intent in INTENTS else "unknown",
            medical_context=bool(data.get("medical_context", False)),
            used_llm=True,
        )

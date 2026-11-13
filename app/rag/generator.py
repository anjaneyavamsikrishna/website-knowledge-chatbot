"""Grounded answer generation. The model sees ONLY the retrieved passages (never earlier
assistant answers) and cites them by label (S1, S2...). Labels are mapped back to URLs
in code, so the model cannot invent a source URL."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.errors import LLMError
from app.core.prompts import GENERATOR_SYSTEM
from app.rag.llm import LLMClient, extract_json
from app.rag.retriever import RetrievedChunk

log = logging.getLogger(__name__)


@dataclass
class GeneratedAnswer:
    answerable: bool
    answer: str
    used_labels: list[str] = field(default_factory=list)


def build_context(chunks: list[RetrievedChunk], max_chars: int) -> tuple[str, list[tuple[str, RetrievedChunk]]]:
    parts, labelled, used = [], [], 0
    for i, chunk in enumerate(chunks, start=1):
        label = f"S{i}"
        block = (
            f"[{label}] page_type={chunk.metadata.get('page_type')} | page_title={chunk.metadata.get('page_title')} "
            f"| section={chunk.metadata.get('section')}\n{chunk.text}"
        )
        if parts and used + len(block) > max_chars:
            break
        parts.append(block)
        labelled.append((label, chunk))
        used += len(block)
    return "\n\n".join(parts), labelled


class AnswerGenerator:
    def __init__(self, llm: LLMClient | None, *, max_context_chars: int = 9000):
        self.llm = llm
        self.max_context_chars = max_context_chars

    def generate(
        self, *, question: str, standalone_question: str, language: str,
        labelled_context: str, claim_note: str | None,
    ) -> GeneratedAnswer:
        if self.llm is None:
            raise LLMError("no LLM configured")
        prompt = (
            f"RESPONSE LANGUAGE: {language}\n"
            f"ORIGINAL USER QUESTION: {question}\n"
            f"STANDALONE QUESTION (English): {standalone_question}\n"
        )
        if claim_note:
            prompt += f"\nPRICE CLAIM CHECK (computed by the system from the passages):\n{claim_note}\n"
        prompt += f"\nCONTEXT PASSAGES:\n{labelled_context}\n\nReturn the JSON object now."

        last_error: Exception | None = None
        for attempt in range(2):  # one retry on malformed JSON
            try:
                data = extract_json(self.llm.complete(GENERATOR_SYSTEM, prompt, json_mode=True))
                answerable = bool(data.get("answerable", False))
                answer = str(data.get("answer") or "").strip()
                labels = [str(x).strip().upper() for x in (data.get("used_sources") or []) if isinstance(x, (str, int))]
                return GeneratedAnswer(answerable and bool(answer), answer, labels)
            except LLMError as exc:
                last_error = exc
                log.warning("Generator attempt %s failed: %s", attempt + 1, exc)
        raise LLMError(f"generation failed: {last_error}")

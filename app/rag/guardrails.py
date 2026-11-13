"""Deterministic (non-LLM) safety checks.

These run in plain Python so they cannot be argued out of their behaviour:
  * input normalisation, prompt-injection / booking / personal-medical detection
  * price verification: amounts in an answer must exist in the retrieved evidence
  * user price-claim checking ("I heard it costs $50")
  * PII redaction for logs
All detectors are heuristics; the LLM analyzer is a second line of defence, and the
post-generation amount check is the last one.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from app.core.textutils import amounts_in, numbers_in

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_question(text: str, max_chars: int = 1000) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# --------------------------------------------------------------------------- booking
_BOOKING = [
    r"\b(book|schedule|reserve|arrange|set\s*up|make|fix)\b[^.?!]{0,40}\b(appointments?|consultations?|bookings?|reservations?|slots?)\b",
    r"\b(book|schedule|reserve)\s+me\b",
    r"\b(book|reserve|arrange)\b[^.?!]{0,30}\b(flights?|hotels?|trips?|surgery|treatment)\b",
    r"\bappointment\b[^.?!]{0,30}\b(tomorrow|today|next\s+week|this\s+week|on\s+\w+day)\b",
    r"\b(reservar|agendar|programar|pedir|sacar)\b[^.?!]{0,30}\b(cita|consulta|turno)\b",
    r"\b(réserver|prendre|fixer)\b[^.?!]{0,30}\b(rendez-vous|consultation)\b",
    r"(احجز|حجز|أريد حجز|اريد حجز)[^.?!]{0,30}(موعد|استشارة)",
]


def detect_booking(text: str) -> bool:
    return _any(_BOOKING, text)


# --------------------------------------------------------------------- prompt injection
_INJECTION = [
    r"\b(ignore|disregard|forget|override|bypass)\b[^.?!]{0,40}\b(instructions?|rules?|prompts?|guidelines?|restrictions?|system)\b",
    r"\b(reveal|show|print|repeat|leak|display)\b[^.?!]{0,30}\b(system\s+prompt|your\s+(prompt|instructions|rules)|hidden\s+prompt)\b",
    r"\byou\s+are\s+now\b",
    r"\b(pretend|act)\s+(to\s+be|as|like)\b",
    r"\b(jailbreak|dan\s+mode|developer\s+mode|do\s+anything\s+now)\b",
    r"\bnew\s+instructions?\s*:",
    r"\b(answer|respond)\b[^.?!]{0,30}\b(without|ignoring)\b[^.?!]{0,20}\b(rules|restrictions|the\s+context|sources)\b",
    r"\buse\s+your\s+(own|general|prior|training)\s+knowledge\b",
    r"\b(make\s+up|invent|fabricate|guess)\b[^.?!]{0,30}\b(price|cost|number|answer|figure)\b",
    r"\bignora\b[^.?!]{0,30}\b(instrucciones|reglas)\b",
    r"تجاهل[^.?!]{0,30}(التعليمات|القواعد)",
]


def detect_prompt_injection(text: str) -> bool:
    return _any(_INJECTION, text)


# ------------------------------------------------------------------ personal medical advice
_CONDITION = (
    r"(cancer|tumou?rs?|diabet\w*|disease|syndrome|hypertension|blood\s+pressure|heart|kidney|liver|infection|"
    r"leuk(a)?emia|lymphoma|carcinoma|adenocarcinoma|asthma|pregnan\w*|allerg\w*|medication|chemo\w*|condition|diagnos\w+)"
)
_PERSONAL_PHRASES = [
    r"\b(is|are|would|will|can|could|should|does)\b[^.?!]{0,40}\b(safe|suitable|right|ok|okay|good|recommended|effective|work|cure|treat|help)\b[^.?!]{0,40}\b(for|with|if)\s+(me|my|i|us|our)\b",
    r"\b(safe|suitable|okay|ok|dangerous|risky)\b[^.?!]{0,30}\b(for|with)\s+(my|me)\b",
    r"\b(should|shall)\s+i\b[^.?!]{0,40}\b(take|use|have|get|stop|start|skip|combine|mix|undergo|do|choose)\b",
    r"\b(can|may)\s+i\s+(take|use|combine|mix|undergo|have)\b[^.?!]{0,40}\b(with|while|if|during|despite)\b",
    rf"\bdo\s+i\s+have\s+(a\s+|an\s+)?{_CONDITION}",
    r"\b(diagnose|diagnosis)\s+me\b",
    r"\bwhich\s+treatment\s+(is\s+)?(best|right)\s+for\s+(me|my)\b",
    r"\bwill\s+(it|this|the\s+treatment)\s+(cure|heal|fix|work\s+for)\s+(me|my)\b",
    rf"\b(es\s+seguro|es\s+segura|es\s+adecuado)\b[^.?!]{{0,40}}\b(mi|mis|para\s+m[ií])\b",
    rf"\btengo\s+{_CONDITION}",
    r"(آمن|مناسب)[^.?!]{0,30}(لي|لحالتي|لمرضي|السكري)",
]
_PERSONAL_CONDITION = re.compile(
    rf"\b(i\s+have|i\s+was\s+diagnosed\s+with|i'?m\s+diagnosed\s+with|i\s+suffer\s+from|i\s+am\s+suffering\s+from|my)\s+(\w+\s+){{0,3}}?{_CONDITION}",
    re.IGNORECASE,
)
_ADVICE_CUE = re.compile(
    r"\b(safe|should|can|could|will|work|cure|treat|recommend|help|ok|okay|suitable|eligible|risk|risky)\b", re.IGNORECASE
)


def detect_personal_medical(text: str) -> bool:
    if _any(_PERSONAL_PHRASES, text):
        return True
    return bool(_PERSONAL_CONDITION.search(text) and _ADVICE_CUE.search(text))


# --------------------------------------------------------------------------- language
def heuristic_language(text: str) -> str:
    """Cheap fallback used only when the LLM analyzer is unavailable."""
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    low = text.lower()
    if "¿" in text or "¡" in text or re.search(r"\b(cuánto|cuanto|cuesta|tratamiento|precio|cuál|qué)\b", low):
        return "es"
    if re.search(r"\b(combien|coûte|traitement|prix|quel|quelle)\b", low):
        return "fr"
    return "en"


# ------------------------------------------------------------------ price verification
def unsupported_amounts(answer: str, context: str, question: str = "") -> list[str]:
    """Currency amounts that appear in the answer but in neither the evidence nor the
    user's own question (echoing the user's number back is allowed)."""
    allowed: set[Decimal] = numbers_in(context) | set(amounts_in(question))
    bad = []
    for amount in amounts_in(answer):
        if amount not in allowed:
            bad.append(f"{amount.normalize():f}")
    return sorted(set(bad))


def claim_note(question: str, context: str) -> str | None:
    """Tell the generator which amounts the USER mentioned are / are not in the evidence."""
    claimed = amounts_in(question)
    if not claimed:
        return None
    present = numbers_in(context)
    lines = []
    for amount in dict.fromkeys(claimed):
        shown = f"{amount.normalize():f}"
        if amount in present:
            lines.append(f"- The user mentioned {shown}: this number DOES appear in the passages (check the context/meaning before confirming).")
        else:
            lines.append(f"- The user mentioned {shown}: this amount does NOT appear in the passages. Do NOT confirm it.")
    return "\n".join(lines)


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def strip_unapproved_urls(answer: str, allowed_urls: set[str]) -> str:
    """Remove any URL in the answer text that did not come from retrieved sources."""
    def repl(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;:")
        return match.group(0) if url in allowed_urls else ""
    return re.sub(r"[ \t]{2,}", " ", _URL_RE.sub(repl, answer)).strip()


# ---------------------------------------------------------------------------- privacy
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def redact_pii(text: str) -> str:
    return _PHONE_RE.sub("[number]", _EMAIL_RE.sub("[email]", text))

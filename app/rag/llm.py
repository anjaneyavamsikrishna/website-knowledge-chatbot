"""Provider-agnostic LLM client (OpenAI-compatible endpoints, or Anthropic)."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Protocol

from app.core.errors import LLMError
from app.rag.embeddings import _retry_after

log = logging.getLogger(__name__)


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str: ...


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences / stray prose."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("model output did not contain a JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object")
    return data


class OpenAICompatibleLLM:
    def __init__(self, api_key: str, model: str, *, base_url: str = "", temperature: float = 0.0,
                 timeout: float = 30.0, max_tokens: int = 1200, client=None, sleep=None):
        self._sleep = sleep or time.sleep
        if client is None:
            from openai import OpenAI

            # max_retries=0: the SDK's instant retries would burn a free-tier quota; retry policy lives in complete()
            client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=0)
        self._client = client
        self.base_url = base_url
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        from openai import (APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, InternalServerError,
                            NotFoundError, PermissionDeniedError, RateLimitError)

        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        rate_waits = transient_waits = 0
        for _ in range(12):  # adapt per unsupported parameter; wait out rate limits; retry transient errors
            try:
                resp = self._client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except BadRequestError as exc:
                msg = str(exc).lower()
                if "max_tokens" in msg and "max_tokens" in kwargs:
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                elif "temperature" in msg and "temperature" in kwargs:
                    kwargs.pop("temperature")
                elif "response_format" in msg and "response_format" in kwargs:
                    kwargs.pop("response_format")
                else:
                    raise LLMError(f"LLM request rejected: {exc}") from exc
            except NotFoundError as exc:
                detail = ""
                try:
                    detail = (exc.response.text or "")[:300]
                except Exception:
                    detail = str(getattr(exc, "message", "") or exc)[:300]
                raise LLMError(
                    f"HTTP 404 from the chat endpoint for model '{self.model}' at {self.base_url or 'the default endpoint'}. "
                    f"Server said: {detail}. Either the model name is wrong or this endpoint's chat route is unavailable. "
                    "Run: python scripts/check_providers.py --try-models  (tests several models on both routes), "
                    "or set LLM_PROVIDER=gemini to use Google's native API."
                ) from exc
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise LLMError("LLM API key rejected. Check LLM_API_KEY (and LLM_BASE_URL for non-OpenAI providers).") from exc
            except RateLimitError as exc:
                text = f"{getattr(exc, 'code', '')} {exc}".lower()
                if any(k in text for k in ("insufficient_quota", "no credits remaining", "add credits", "credit balance")):
                    raise LLMError("LLM provider reports no credits/quota for this API key (add billing credit or switch provider)") from exc
                if rate_waits >= 4:
                    raise LLMError("LLM provider rate limit (HTTP 429) persisted after several waits. Free tiers allow only a few "
                                   "requests per minute: set LLM_MIN_INTERVAL_S=6 in .env, or retry in a minute.") from exc
                wait = _retry_after(exc) or min(5.0 * (2 ** rate_waits), 40.0)
                rate_waits += 1
                log.warning("LLM rate limit (429): waiting %.1fs before retry %s/4", wait, rate_waits)
                self._sleep(wait + 0.5)
            except (APIConnectionError, APITimeoutError, InternalServerError) as exc:
                if transient_waits >= 2:
                    raise LLMError(f"LLM request failed: {exc.__class__.__name__}") from exc
                self._sleep(2.0 * (2 ** transient_waits))
                transient_waits += 1
            except Exception as exc:
                text = f"{getattr(exc, 'code', '')} {exc}".lower()
                if any(k in text for k in ("insufficient_quota", "no credits remaining", "add credits", "credit balance")):
                    raise LLMError("LLM provider reports no credits/quota for this API key (add billing credit or switch provider)") from exc
                raise LLMError(f"LLM request failed: {exc.__class__.__name__}: {exc}") from exc
        raise LLMError("LLM request could not be adapted to this provider")


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, *, temperature: float = 0.0, timeout: float = 30.0, max_tokens: int = 700):
        import anthropic  # optional dependency: pip install anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens or self.max_tokens, temperature=self.temperature,
                system=system, messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc.__class__.__name__}: {exc}") from exc
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class GeminiNativeLLM:
    """Google's native Gemini REST API (generateContent). Use LLM_PROVIDER=gemini when the OpenAI-compatible
    chat route misbehaves. The API key is sent in a header, never in the URL, and never appears in messages."""

    def __init__(self, api_key: str, model: str, *, api_base: str = "https://generativelanguage.googleapis.com/v1beta",
                 temperature: float = 0.0, timeout: float = 60.0, max_tokens: int = 1200,
                 disable_thinking: bool = True, session=None, sleep=None):
        import time

        import requests

        self._http = session or requests.Session()
        self._sleep = sleep or time.sleep
        self.api_key, self.api_base = api_key, api_base.rstrip("/")
        self.model = model.removeprefix("models/")
        self.temperature, self.timeout, self.max_tokens = temperature, timeout, max_tokens
        self.disable_thinking = disable_thinking

    @staticmethod
    def _text(data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise LLMError(f"Gemini returned no answer (blockReason={reason}). The safety filter may have blocked the request.")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMError(f"Gemini returned empty text (finishReason={cand.get('finishReason')}). "
                           "If MAX_TOKENS: raise LLM_MAX_TOKENS or choose a non-reasoning model.")
        return text

    def _explain(self, code: int, body: str) -> str:
        if code == 404:
            return f"Gemini API 404 for model '{self.model}': {body}. Check LLM_MODEL (python scripts/check_providers.py --try-models)."
        if code in (401, 403):
            return f"Gemini API key rejected or not allowed (HTTP {code}): {body}"
        if code == 429:
            return f"Gemini rate limit or quota reached (HTTP 429): {body}"
        return f"Gemini API error HTTP {code}: {body}"

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        import requests

        gen: dict = {"temperature": self.temperature, "maxOutputTokens": max_tokens or self.max_tokens}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        if self.disable_thinking:
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        url = f"{self.api_base}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        waits = 0
        for _ in range(8):
            try:
                resp = self._http.post(url, headers=headers, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                if waits < 2:
                    self._sleep(2 ** waits); waits += 1
                    continue
                raise LLMError(f"Gemini request failed: {exc.__class__.__name__}") from exc
            code = resp.status_code
            if code == 200:
                try:
                    return self._text(resp.json())
                except ValueError as exc:
                    raise LLMError("Gemini returned a non-JSON response") from exc
            text = (resp.text or "")[:400]
            low = text.lower()
            if code == 400:  # unsupported option for this model: drop it and retry once per option
                if "thinking" in low and "thinkingConfig" in gen:
                    gen.pop("thinkingConfig"); continue
                if ("mime" in low or "json" in low) and "responseMimeType" in gen:
                    gen.pop("responseMimeType"); continue
            if code in (429, 500, 502, 503, 504) and waits < 4:
                hint = _retry_after(Exception(text)) if code == 429 else None
                self._sleep((hint or min(5.0 * (2 ** waits), 30.0)) + 0.5); waits += 1
                continue
            raise LLMError(self._explain(code, text))
        raise LLMError("Gemini request could not be completed")


class PacedLLM:
    """Spaces model calls at least `min_interval_s` apart (thread-safe). Keeps free-tier keys under their
    requests-per-minute limit. Each chat question makes 2 model calls (analyzer + answer)."""

    def __init__(self, inner: LLMClient, min_interval_s: float, sleep=time.sleep, monotonic=time.monotonic):
        self.inner, self.interval, self._sleep, self._mono = inner, min_interval_s, sleep, monotonic
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        with self._lock:
            now = self._mono()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self.interval
        if wait > 0:
            self._sleep(wait)
        return self.inner.complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


def _build_raw_llm(settings) -> LLMClient | None:
    """Returns None when no API key is configured (the app still starts; chat returns 503)."""
    if not settings.llm_api_key:
        return None
    provider = settings.llm_provider.lower()
    common = dict(temperature=settings.llm_temperature, timeout=settings.llm_timeout_s, max_tokens=settings.llm_max_tokens)
    if provider == "anthropic":
        return AnthropicLLM(settings.llm_api_key, settings.llm_model, **common)
    if provider == "gemini":
        return GeminiNativeLLM(settings.llm_api_key, settings.llm_model, api_base=settings.gemini_api_base,
                               temperature=settings.llm_temperature, timeout=max(settings.llm_timeout_s, 60.0),
                               max_tokens=settings.llm_max_tokens)
    if provider == "openai":
        return OpenAICompatibleLLM(settings.llm_api_key, settings.llm_model, base_url=settings.llm_base_url, **common)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


def build_llm(settings) -> LLMClient | None:
    """Returns None when no API key is configured (the app still starts; chat returns 503)."""
    llm = _build_raw_llm(settings)
    if llm is not None and settings.llm_min_interval_s > 0:
        return PacedLLM(llm, settings.llm_min_interval_s)
    return llm
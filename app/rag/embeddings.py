"""Embedding providers behind one tiny interface.

openai                -> any OpenAI-compatible embeddings endpoint (OpenAI, Gemini compat, ...)
sentence-transformers -> local multilingual model (heavy: needs torch; see README)
hash                  -> deterministic bag-of-words hashing, ONLY for offline unit tests
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Protocol

from app.core.errors import EmbeddingError, ProviderFatalError

_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in", "on", "for", "and", "or", "it", "its",
    "what", "which", "who", "how", "does", "do", "did", "can", "could", "me", "my", "i", "you", "your", "this",
    "that", "with", "at", "by", "from", "as", "about", "much", "tell", "please", "there",
}


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[^\W_]+", text.lower()) if t not in _STOP]


class HashEmbedder:
    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in tokenize(text):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) % 2 == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


QUOTA_HINT = (
    "The provider reports NO CREDITS for this API key (insufficient_quota). Add billing credit to the account, "
    "or switch to a free provider (README: 'Free / single-key setups')."
)
DAILY_HINT = (
    "The provider's DAILY free quota for embeddings is used up. Wait for the daily reset, use another API key/provider, "
    "or switch to local embeddings (EMBEDDING_PROVIDER=sentence-transformers). Progress so far is saved."
)
RATE_HINT = (
    "The provider's per-minute rate limit persisted after several retries. Progress is saved: wait a minute and run the "
    "command again (it resumes). If it keeps happening, lower EMBEDDING_BATCH_SIZE or raise EMBEDDING_MIN_INTERVAL_S."
)


def _is_quota_error(exc: Exception) -> bool:
    """True only for clear 'no credit balance' errors. NOTE: the phrase 'exceeded your current quota ... check your
    plan and billing' is also how Gemini words an ordinary per-minute rate limit, so it is NOT treated as fatal."""
    text = f"{getattr(exc, 'code', '')} {exc}".lower()
    return any(k in text for k in ("insufficient_quota", "no credits remaining", "add credits", "credit balance"))


def _is_daily_limit(exc: Exception) -> bool:
    text = f"{exc}".lower().replace(" ", "")
    return "perday" in text or "requestsperday" in text


def _retry_after(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        value = headers.get("retry-after")
        if value:
            try:
                return min(float(value), 90.0)
            except ValueError:
                pass
    for pattern in (r"retry in ([\d.]+)\s*s", r"retryDelay[\"']?\s*[:=]\s*[\"']?([\d.]+)\s*s"):
        m = re.search(pattern, str(exc), re.I)
        if m:
            return min(float(m.group(1)), 90.0)
    return None


class OpenAIEmbedder:
    """OpenAI-compatible embeddings (OpenAI, Gemini, ...) with classified errors:
       * no credits / invalid key / unknown model / daily quota -> ProviderFatalError (abort, clear message)
       * per-minute rate limits, timeouts, 5xx                  -> wait (honouring the provider's retry hint), retry
       * still failing after retries                            -> EmbeddingError (progress is saved, re-run resumes)
    Requests are also paced (min_interval_s) to stay below free-tier limits."""

    def __init__(self, api_key: str, model: str, base_url: str = "", batch_size: int = 32, max_attempts: int = 6,
                 min_interval_s: float = 0.0, client=None, sleep=time.sleep, monotonic=time.monotonic):
        if client is None:
            from openai import OpenAI

            # max_retries=0: the retry policy lives here so quota errors are never retried blindly
            client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60, max_retries=0)
        self._client, self._model = client, model
        self._batch, self._max_attempts = max(1, batch_size), max_attempts
        self._min_interval, self._sleep, self._mono = min_interval_s, sleep, monotonic
        self._last_call: float | None = None
        self.name = f"openai:{model}"

    def _pace(self) -> None:
        if self._min_interval > 0 and self._last_call is not None:
            wait = self._min_interval - (self._mono() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._mono()

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        import openai

        rate_delay, transient_delay = 5.0, 2.0
        for attempt in range(1, self._max_attempts + 1):
            self._pace()
            rate_limited = False
            try:
                resp = self._client.embeddings.create(model=self._model, input=batch)
                data = list(resp.data)
                # Some OpenAI-compatible providers return index=None; the API guarantees input order, so only
                # re-sort when EVERY item has an integer index.
                if all(isinstance(getattr(d, "index", None), int) for d in data):
                    data.sort(key=lambda d: d.index)
                vectors = [d.embedding for d in data]
                if len(vectors) != len(batch):
                    raise EmbeddingError(f"provider returned {len(vectors)} vectors for {len(batch)} inputs")
                return vectors
            except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                raise ProviderFatalError(f"Embedding API key rejected ({exc.__class__.__name__}). Check EMBEDDING_API_KEY / LLM_API_KEY.") from exc
            except openai.NotFoundError as exc:
                raise ProviderFatalError(f"Embedding model '{self._model}' not found at this endpoint. Check EMBEDDING_MODEL / EMBEDDING_BASE_URL.") from exc
            except openai.RateLimitError as exc:
                if _is_quota_error(exc):
                    raise ProviderFatalError(QUOTA_HINT) from exc
                if _is_daily_limit(exc):
                    raise ProviderFatalError(DAILY_HINT) from exc
                err, rate_limited = exc, True
                wait = _retry_after(exc) or rate_delay
                rate_delay = min(rate_delay * 2, 60.0)
            except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
                err, wait = exc, transient_delay
                transient_delay = min(transient_delay * 2, 30.0)
            if attempt == self._max_attempts:
                msg = RATE_HINT if rate_limited else f"embedding request kept failing: {err.__class__.__name__}"
                raise EmbeddingError(msg) from err
            self._sleep(wait + 0.5)
        raise EmbeddingError("embedding failed")  # unreachable

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out.extend(self._embed_batch(texts[i : i + self._batch]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class SentenceTransformerEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # optional heavy dependency

        self._model = SentenceTransformer(model)
        self._e5 = "e5" in model.lower()  # e5 models expect query:/passage: prefixes
        self.name = f"st:{model}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._e5:
            texts = [f"passage: {t}" for t in texts]
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        if self._e5:
            text = f"query: {text}"
        return self._model.encode([text], normalize_embeddings=True)[0].tolist()


def build_embedder(settings) -> Embedder:
    provider = settings.embedding_provider.lower()
    if provider == "hash":
        return HashEmbedder()
    if provider in ("sentence-transformers", "sentence_transformers", "local"):
        return SentenceTransformerEmbedder(settings.embedding_model)
    if provider == "openai":
        key = settings.embedding_api_key or settings.llm_api_key
        if not key:
            raise RuntimeError("EMBEDDING_API_KEY (or LLM_API_KEY) is required for the openai embedding provider")
        # One key for everything: if no separate EMBEDDING_API_KEY is set, the embedding calls also use
        # the LLM endpoint (LLM_BASE_URL), so a single Gemini/Groq/OpenAI key works for both.
        base_url = settings.embedding_base_url or ("" if settings.embedding_api_key else settings.llm_base_url)
        return OpenAIEmbedder(key, settings.embedding_model, base_url, batch_size=settings.embedding_batch_size,
                              min_interval_s=settings.embedding_min_interval_s)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider}")

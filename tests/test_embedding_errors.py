"""Regression tests for the 'no credits remaining' run (429 insufficient_quota on every page)."""
import httpx
import openai
import pytest

from app.core.errors import EmbeddingError, ProviderFatalError
from app.rag.embeddings import HashEmbedder, OpenAIEmbedder
from tests.conftest import HTML, PKG_URL, PRICE_URL, PROFILE_URL, FakeScraper


def api_error(cls, status, message, code=None):
    req = httpx.Request("POST", "https://api.example.com/v1/embeddings")
    resp = httpx.Response(status, request=req, json={"error": {"message": message, "code": code}})
    return cls(message, response=resp, body={"message": message, "code": code})  # SDK passes the inner error object


class FakeEmbeddingsAPI:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def create(self, model, input):
        self.calls += 1
        if self.errors:
            err = self.errors.pop(0)
            if err:
                raise err
        data = [type("D", (), {"index": i, "embedding": [1.0, 0.0]})() for i, _ in enumerate(input)]
        return type("R", (), {"data": data})()


def embedder(errors, **kw):
    api = FakeEmbeddingsAPI(errors)
    client = type("C", (), {"embeddings": api})()
    return OpenAIEmbedder("k", "m", client=client, sleep=lambda s: None, **kw), api


def test_no_credits_is_fatal_and_not_retried():
    emb, api = embedder([api_error(openai.RateLimitError, 429, "You have no credits remaining.")] * 5)
    with pytest.raises(ProviderFatalError) as e:
        emb.embed_documents(["a"])
    assert api.calls == 1 and "credit" in str(e.value).lower()


def test_insufficient_quota_code_is_fatal():
    emb, api = embedder([api_error(openai.RateLimitError, 429, "Rate limit", code="insufficient_quota")])
    with pytest.raises(ProviderFatalError):
        emb.embed_documents(["a"])
    assert api.calls == 1


def test_invalid_key_is_fatal():
    emb, _ = embedder([api_error(openai.AuthenticationError, 401, "bad key")])
    with pytest.raises(ProviderFatalError):
        emb.embed_documents(["a"])


def test_real_rate_limit_is_retried_with_backoff_then_succeeds():
    emb, api = embedder([api_error(openai.RateLimitError, 429, "Too many requests, slow down")] * 2)
    assert emb.embed_documents(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert api.calls == 3


def test_persistent_rate_limit_gives_transient_error():
    emb, _ = embedder([api_error(openai.RateLimitError, 429, "Too many requests")] * 9, max_attempts=3)
    with pytest.raises(EmbeddingError):
        emb.embed_documents(["a"])


def test_batching():
    emb, api = embedder([], batch_size=2)
    assert len(emb.embed_documents(["a", "b", "c", "d", "e"])) == 5 and api.calls == 3


class QuotaEmbedder(HashEmbedder):
    calls = 0

    def embed_documents(self, texts):
        QuotaEmbedder.calls += 1
        raise ProviderFatalError("no credits")


def test_pipeline_aborts_after_first_fatal_error(settings, llm):
    from app.core.container import build_container
    QuotaEmbedder.calls = 0
    scraper = FakeScraper()
    c = build_container(settings, llm=llm, embedder=QuotaEmbedder(), scraper=scraper)
    report = c.refresh_service.refresh()
    assert QuotaEmbedder.calls == 1                      # one failed call, not one per page
    assert len(scraper.calls) == 1                        # remaining pages were not even fetched
    assert report["failed"] == 3 and report["updated"] == 0
    assert all("no credits" in r["error"] for r in report["results"])
    assert c.store.count() == 0


class CountingEmbedder(HashEmbedder):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []

    def embed_documents(self, texts):
        self.texts.extend(texts)
        return super().embed_documents(texts)


def test_only_new_chunks_are_embedded_on_change(settings, llm):
    from app.core.container import build_container
    emb, scraper = CountingEmbedder(), FakeScraper()
    c = build_container(settings, llm=llm, embedder=emb, scraper=scraper)
    c.refresh_service.refresh()
    first = len(emb.texts)
    emb.texts.clear()

    scraper.pages[PKG_URL] = HTML[PKG_URL].replace("USD 4,500", "USD 5,100")
    c.refresh_service.refresh()
    assert 0 < len(emb.texts) < first                     # only the changed chunk(s), not the whole site
    assert all("USD 5,100" in t or "USD 4,500" not in t for t in emb.texts)

    emb.texts.clear()
    c.refresh_service.refresh(force=True)
    assert len(emb.texts) == first                        # --force re-embeds everything
    assert not any(d["text"].count("USD 4,500") for d in c.store.all_documents({"source_url": PKG_URL}))


def test_shared_key_shares_endpoint(settings):
    from app.rag.embeddings import build_embedder
    s = settings.model_copy(update={"embedding_provider": "openai", "llm_api_key": "k",
                                    "llm_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                                    "embedding_api_key": "", "embedding_base_url": ""})
    emb = build_embedder(s)
    assert str(emb._client.base_url).startswith("https://generativelanguage.googleapis.com/v1beta/openai")
    s2 = s.model_copy(update={"embedding_api_key": "other"})   # separate key => does NOT inherit the LLM endpoint
    assert "generativelanguage" not in str(build_embedder(s2)._client.base_url)


# ===================== regressions from the real Gemini run =====================
def make_resp(items):
    """items: list of (index, vector); index may be None, like Gemini's OpenAI-compatible endpoint."""
    data = [type("D", (), {"index": i, "embedding": v})() for i, v in items]
    return type("R", (), {"data": data})()


class ScriptedAPI:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def create(self, model, input):
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else make_resp([(i, [float(i), 0.0]) for i, _ in enumerate(input)])
        if isinstance(out, Exception):
            raise out
        return out


def emb_with(api, **kw):
    sleeps = []
    client = type("C", (), {"embeddings": api})()
    e = OpenAIEmbedder("k", "m", client=client, sleep=sleeps.append, **kw)
    return e, sleeps


def test_index_none_or_mixed_does_not_crash_and_keeps_input_order():
    # This is the exact crash: "'<' not supported between instances of 'int' and 'NoneType'"
    api = ScriptedAPI([make_resp([(0, [0.0]), (None, [1.0]), (None, [2.0])])])
    e, _ = emb_with(api)
    assert e.embed_documents(["a", "b", "c"]) == [[0.0], [1.0], [2.0]]


def test_all_integer_indexes_are_still_sorted():
    api = ScriptedAPI([make_resp([(1, [1.0]), (0, [0.0])])])
    e, _ = emb_with(api)
    assert e.embed_documents(["a", "b"]) == [[0.0], [1.0]]


def test_vector_count_mismatch_is_an_error():
    api = ScriptedAPI([make_resp([(0, [0.0])])])
    e, _ = emb_with(api)
    with pytest.raises(EmbeddingError):
        e.embed_documents(["a", "b"])


def test_gemini_style_429_is_a_retryable_rate_limit_that_honours_the_retry_hint():
    msg = "You exceeded your current quota, please check your plan and billing details. Please retry in 3.2s."
    api = ScriptedAPI([api_error(openai.RateLimitError, 429, msg), api_error(openai.RateLimitError, 429, msg)])
    e, sleeps = emb_with(api)
    assert len(e.embed_documents(["a"])) == 1          # succeeded on the 3rd attempt, NOT treated as 'no credits'
    assert api.calls == 3 and all(s >= 3.2 for s in sleeps)


def test_daily_limit_is_fatal_with_a_specific_message():
    msg = "Quota exceeded for metric: embed_content_free_tier_requests, limit: 1000, GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    api = ScriptedAPI([api_error(openai.RateLimitError, 429, msg)])
    e, _ = emb_with(api)
    with pytest.raises(ProviderFatalError) as exc:
        e.embed_documents(["a"])
    assert "DAILY" in str(exc.value) and api.calls == 1


def test_requests_are_paced():
    clock = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s); clock["t"] += s

    api = ScriptedAPI([])
    client = type("C", (), {"embeddings": api})()
    e = OpenAIEmbedder("k", "m", client=client, batch_size=1, min_interval_s=1.0, sleep=sleep, monotonic=lambda: clock["t"])
    e.embed_documents(["a", "b", "c"])
    assert api.calls == 3 and sum(sleeps) >= 2.0


# ---------------------------- resumable ingestion ----------------------------
def long_page(n=12):
    body = "".join(f"<h2>Part {i}</h2><p>{(f'Unique content block {i} about the programme. ' * 14)}</p>" for i in range(n))
    return f"<html><head><title>Long Demo Page</title></head><body><main><h1>Long Demo</h1>{body}</main></body></html>"


class FlakyEmbedder(HashEmbedder):
    """Fails (rate limit) on the Nth call, then behaves."""

    def __init__(self, fail_on_call):
        super().__init__()
        self.fail_on, self.calls, self.texts = fail_on_call, 0, []

    def embed_documents(self, texts):
        self.calls += 1
        if self.calls == self.fail_on:
            raise EmbeddingError("rate limit persisted")
        self.texts.extend(texts)
        return super().embed_documents(texts)


def test_rate_limit_mid_page_keeps_progress_and_rerun_resumes(settings, llm):
    from app.core.container import build_container
    scraper = FakeScraper()
    scraper.pages[PKG_URL] = long_page(12)
    emb = FlakyEmbedder(fail_on_call=3)               # call1=profile, call2=pkg batch1, call3=pkg batch2 -> fails
    c = build_container(settings, llm=llm, embedder=emb, scraper=scraper)
    c.refresh_service.pipeline.embed_batch = 4

    first = c.refresh_service.refresh()
    assert first["failed"] == 2                         # pkg failed, price list skipped (run stopped softly)
    assert any("skipped after provider error" in (r["error"] or "") for r in first["results"])
    stored_pkg = len(c.store.ids_for_source(PKG_URL))
    assert stored_pkg == 4                              # first batch persisted despite the failure

    emb.texts.clear()
    second = c.refresh_service.refresh()                # resumes
    assert second["failed"] == 0
    pkg_chunks = [t for t in emb.texts if "Unique content block" in t]
    assert len(pkg_chunks) == len(c.store.ids_for_source(PKG_URL)) - stored_pkg   # only the missing ones were embedded
    assert c.db.get_source_status(PKG_URL)["status"] == "ok"


# ------------------------------ chunk merging ------------------------------
def test_tiny_sections_are_merged_but_text_is_complete():
    from app.ingestion.chunker import chunk_page
    from app.ingestion.extractor import Block, ExtractedPage, Section
    sections = [Section(f"Video {i}", [Block("paragraph", f"Video number {i} title and short description.")]) for i in range(40)]
    page = ExtractedPage("u", "Videos", "clinic_videos", sections, "x")
    chunks = chunk_page(page, max_chars=1100, extracted_at="t", content_version="v", min_chunk_chars=400)
    assert len(chunks) < 10                              # was 40 near-empty chunks
    joined = "\n".join(c.text for c in chunks)
    assert all(f"Video number {i} title" in joined for i in range(40))   # nothing lost
    assert "[Section: Video 5]" in joined or "Section: Video 5" in joined
    assert all(len(c.text) <= 1100 + 60 for c in chunks)


def test_substantial_sections_are_not_merged_together():
    from app.ingestion.chunker import chunk_page
    from app.ingestion.extractor import Block, ExtractedPage, Section
    big = "Sentence about services provided. " * 20          # ~680 chars
    sections = [Section(f"Topic {i}", [Block("paragraph", big)]) for i in range(3)]
    page = ExtractedPage("u", "T", "treatment_package", sections, "x")
    chunks = chunk_page(page, max_chars=1100, extracted_at="t", content_version="v", min_chunk_chars=400)
    assert len(chunks) == 3


# ---------------------- LLM model-not-found (the 404 from the real run) ----------------------
def test_llm_404_gives_actionable_message_with_model_name():
    from app.core.errors import LLMError
    from app.rag.llm import OpenAICompatibleLLM

    class Completions:
        def create(self, **kw):
            raise api_error(openai.NotFoundError, 404, "models/gemini-2.0-flash is not found")

    client = type("C", (), {"chat": type("Ch", (), {"completions": Completions()})()})()
    llm = OpenAICompatibleLLM("k", "gemini-2.0-flash", base_url="https://x/v1/", client=client)
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u")
    assert "gemini-2.0-flash" in str(e.value) and "check_providers" in str(e.value)


def test_suggest_threshold():
    from app.rag.calibration import suggest_threshold
    clean = suggest_threshold([0.69, 0.72, 0.757], [0.50, 0.522])
    assert clean["gap"] > 0 and 0.522 < clean["suggested"] < 0.69
    overlap = suggest_threshold([0.55, 0.7], [0.6])
    assert overlap["gap"] < 0 and "OVERLAP" in overlap["verdict"]

from app.rag.retriever import Retriever, bm25_scores
from app.rag.embeddings import tokenize
from tests.conftest import PKG_URL, PRICE_URL


def test_price_query_finds_price_chunk(ingested):
    res = ingested.retriever.retrieve("How much does the adrenal package cost?")
    assert res.chunks
    assert any("USD 4,500" in c.text for c in res.chunks)
    assert all("source_url" in c.metadata for c in res.chunks)


def test_unrelated_query_is_gated_out(ingested):
    res = ingested.retriever.retrieve("weather forecast sunshine beach")
    assert res.chunks == [] and res.reason == "below_threshold"


def test_threshold_is_configurable(ingested):
    strict = Retriever(ingested.store, ingested.embedder, threshold=0.99, top_k=5, candidates=12)
    assert strict.retrieve("adrenal package price").chunks == []


def test_metadata_filter_restricts_page_types(ingested):
    res = ingested.retriever.retrieve("treatment prices Demo Adrenal Package starting price 14 days", ["price_list"])
    assert res.chunks and all(c.metadata["page_type"] == "price_list" for c in res.chunks)
    assert res.used_filter


def test_filter_falls_back_to_unfiltered_when_nothing_relevant(ingested):
    res = ingested.retriever.retrieve("airport transfers accommodation", ["clinic_videos"])  # no such page type indexed
    assert res.chunks and not res.used_filter
    assert any(c.metadata["source_url"] == PKG_URL for c in res.chunks)


def test_empty_kb_reported(container):
    res = container.retriever.retrieve("anything")
    assert res.reason == "empty_kb" and not res.chunks


def test_bm25_prefers_exact_term_matches():
    docs = [tokenize("alpha beta gamma"), tokenize("delta epsilon"), tokenize("alpha alpha zeta")]
    scores = bm25_scores(tokenize("alpha"), docs)
    assert scores[1] == 0 and scores[2] > scores[0] > 0

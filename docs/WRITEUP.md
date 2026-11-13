# Technical write-up (draft: adjust after you have seen real results)

**What was built.** A custom RAG pipeline over seven approved PlacidWay pages: a polite scraper (robots.txt,
identifying User-Agent, ~1 req/s), a structure-preserving extractor (table rows become self-contained sentences),
section-aware chunking that keeps each price with its currency and context, ChromaDB for vectors and SQLite for
logs. Answers pass through layered defences: a similarity gate that skips the LLM when nothing relevant exists,
a passages-only grounded prompt, a deterministic check that every currency amount in an answer appears in the
retrieved evidence, and code-side citation mapping. Booking, personal-medical, out-of-scope and prompt-injection
requests get fixed, multilingual replies.

**Current limitations.**
- Extraction is generic HTML parsing; pages that render prices with JavaScript, or tables with merged cells,
  would need a headless browser or per-page rules.
- Price validation only recognises amounts with a currency marker, and conflict detection between pages is left
  to the model rather than checked in code.
- Safety detectors are regex heuristics (strongest in English) backed by an LLM intent classifier; adversarial
  phrasing can still slip past the first layer, although the price post-check and grounded prompt remain.
- Sessions and rate limiting live in process memory (single worker); a restart clears conversations.
- Retrieval quality depends on one similarity threshold calibrated on a handful of queries.
- Only seven pages are indexed, so many reasonable questions (recovery time, other countries) will correctly
  return "not found".
- [Add anything you actually observed in docs/TEST_RESULTS.md here.]

**With one more week.**
1. Build an evaluation set of ~100 labelled questions (answerable / unanswerable / adversarial) and track
   groundedness, citation precision and refusal accuracy automatically, using it to tune the threshold and prompts.
2. Replace regex safety routing with a small multilingual classifier, evaluated on that set.
3. Add structured price extraction at ingestion (treatment, amount, currency, qualifier, country) so cost answers
   come from verified records and cross-page conflicts are detected programmatically.
4. Add a cross-encoder reranker, and a headless-browser fallback for JavaScript-rendered pages.
5. Scheduled refresh with change alerts, Redis-backed sessions and rate limits, streaming responses, and an admin
   dashboard for unanswered questions and lead review.

# PlacidWay Website Knowledge Chatbot

A grounded RAG chatbot that answers visitor questions **only** from seven approved PlacidWay pages,
cites the source URL for every factual answer, and says "I couldn't find this" instead of guessing.
Accuracy and hallucination prevention were prioritised over UI polish.

> **Status honesty.** The code, 83 automated tests and the running server were verified offline with
> synthetic fixture pages and a scripted fake LLM. **Not yet done (needs your machine/keys):**
> ingesting the real 7 pages, running against a real LLM/embedding API, the eight real assessment
> scenarios (`scripts/run_assessment_tests.py`), and the public deployment. See
> [Your remaining steps](#your-remaining-steps-in-order).

## Architecture

```
 7 approved URLs (config/sources.json)
      │  scraper: robots.txt, User-Agent, ~1 req/s, timeout+retry, never follows links
      ▼
 extractor: strip nav/ads/scripts, keep headings/lists/tables (table row => self-contained sentence)
      ▼
 chunker: per heading section, price kept with its context, deterministic chunk ids + metadata
      ▼
 embeddings ──► ChromaDB (persisted)        SQLite: source status, refresh runs, unanswered Qs, leads

 Visitor question
      ▼
 guardrails (rules, no LLM): normalise, prompt-injection  ──► canned refusal
      ▼
 query analyzer (1 LLM call): language, standalone English rewrite using chat history, intent
      ▼
 routing: booking | personal-medical | quote | greeting | out-of-scope ──► canned reply (no retrieval)
      ▼
 retriever: semantic search (+ page_type filter by intent) ─► RELEVANCE GATE ─► BM25/RRF re-rank
      │  nothing above threshold ──► canned fallback + log unanswered (generator NOT called)
      ▼
 generator: sees ONLY retrieved passages (never earlier bot answers), returns JSON
      ▼
 post-checks: every currency amount in the answer must exist in the evidence (or the user's question);
              unapproved URLs stripped; sources mapped from labels S1..Sn in code; disclaimer once per session
      ▼
 answer + sources
```

### Why these choices
| Decision | Reason |
|---|---|
| Custom RAG, no LangChain | ~600 lines, every step explainable in a review; fewer dependencies |
| ChromaDB (persistent) | Required by the brief; embeddings supplied explicitly so Chroma never downloads a model; collection name derived from the embedding model so models can't be mixed |
| Provider-agnostic LLM/embeddings | `openai` SDK works with OpenAI and any OpenAI-compatible endpoint (Gemini, Groq, Ollama via `*_BASE_URL`); Anthropic supported too |
| Default embeddings = API (`text-embedding-3-small`) | Multilingual enough for ES/AR questions about English pages, and keeps the deploy small. `sentence-transformers` is supported but needs torch (1-2 GB RAM/disk) |
| Hybrid retrieval (semantic + BM25) | Pure embeddings are weak on exact names/numbers; the gate stays semantic so "I don't know" stays reliable |
| Deterministic refusals | Booking, personal medical advice, out-of-scope, injection, fallback texts are fixed strings in 4 languages (en/es/ar/fr), so they cannot be argued away |
| SQLite via stdlib | 4 tiny tables; an ORM would add weight with no benefit |

### How hallucination is prevented (defence in depth)
1. Retrieval relevance gate: below `SIMILARITY_THRESHOLD` the LLM is never asked to answer.
2. System prompt: passages only, no outside knowledge, user claims are unverified, conflicts must be reported, no price arithmetic/conversion.
3. Model may declare `answerable:false` -> canned fallback.
4. **Price post-check:** any currency amount in the answer not found in the retrieved evidence (or echoed from the user's question) replaces the answer with the fallback and is logged.
5. A system-computed "PRICE CLAIM CHECK" tells the model whether a number the user mentioned (e.g. `$50`) appears in the passages.
6. Sources are chosen by label in code; URLs the model writes itself are stripped.
7. The generator never receives earlier assistant answers, so previous output can't become "evidence"; follow-ups re-retrieve every time.

## Project layout
```
app/api/           routes (chat, ingest, health+sources, admin logs, leads), schemas, auth/rate-limit deps
app/core/          config, prompts (ALL prompt text), container (wiring), errors, textutils
app/ingestion/     scraper, extractor, chunker, pipeline, sources
app/rag/           embeddings, vector_store, retriever, query_analyzer, generator, guardrails, llm
app/services/      chat_service (orchestration), refresh_service, sessions
app/database/      SQLite schema + access
app/frontend/      index.html, style.css, script.js
config/sources.json  the ONLY URLs that may be fetched
scripts/           ingest.py, inspect_extraction.py, probe_retrieval.py, run_assessment_tests.py
tests/             offline tests + assessment_questions.json
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # then edit: LLM_API_KEY, ADMIN_TOKEN, CRAWLER_USER_AGENT contact address
```
Set a real contact address in `CRAWLER_USER_AGENT` (the crawler must identify itself).

## Your remaining steps (in order)

**1. Inspect extraction on the real pages (do this before anything else).**
I could not reach placidway.com from my sandbox, so the extractor's generic selectors are untested on the real HTML.
```bash
python scripts/inspect_extraction.py            # fetches the 7 URLs politely, writes data/inspect/<id>.txt
```
Read every file. Check: prices keep their currency and label, nav/footer junk is gone, the price-list and
price-comparison tables became sentences like `Treatment: X; Price: USD ...`. If a page is thin or noisy,
add `"content_selector": "<css selector>"` to that source in `config/sources.json`. If a page is
JavaScript-rendered (very little text), you'll need Playwright for that page; tell me and I'll add it.

**2. Ingest.**
```bash
python scripts/ingest.py             # add --force to re-embed everything
```
**3. Calibrate the relevance threshold** (depends on your embedding model):
```bash
python scripts/probe_retrieval.py "How much does the adrenal treatment cost?" "What is the weather in Cancun?"
```
Set `SIMILARITY_THRESHOLD` above the unrelated-query scores and below real ones.

**4. Run it.**
```bash
uvicorn app.main:app --reload       # http://localhost:8000
```
**5. Run the real assessment scenarios and keep the output.**
```bash
python scripts/run_assessment_tests.py
```
Writes `docs/TEST_RESULTS.md` + `docs/test_results.json` with observed question, retrieved documents,
answer, source URLs, expected behaviour and an automatic check. Edit `tests/assessment_questions.json`
first so the questions use real treatments/countries from the pages. **Read each answer against the source page
and tick the reviewer boxes yourself.** If a scenario fails, tune (threshold, prompts) and re-run; never edit results by hand.

**6. Deploy, then test the public URL** (`python scripts/run_assessment_tests.py --base-url https://...`).

## Free / single-key setups (and the "429 / no credits remaining" error)
`openai.RateLimitError ... You have no credits remaining` means the **account has no prepaid credit**; a fresh key
does not help and no code change can fix it. OpenAI needs a paid balance even for embeddings (for this project the
whole ingestion costs a fraction of a cent once credit exists). Options:

| Option | Embeddings | LLM | Notes |
|---|---|---|---|
| **A. Gemini, one key** | `gemini-embedding-001` | e.g. `gemini-2.0-flash` | Free tier available (check current limits in Google AI Studio). Uses Gemini's OpenAI-compatible endpoint, so no code change, only `.env`. Model names change; list them with the `curl .../models` command in `.env.example`. |
| **B. OpenAI, one key** | `text-embedding-3-small` | `gpt-4o-mini` | Add credit at platform.openai.com billing. |
| **C. Local embeddings + any LLM** | `sentence-transformers` (free, offline) | any | `pip install -r requirements-local-embeddings.txt` (large download, needs RAM). |

With a single key, leave `EMBEDDING_API_KEY` empty: embeddings then reuse `LLM_API_KEY` **and** `LLM_BASE_URL`.
Switching embedding model needs no cleanup: vectors live in a collection named after the model, so just re-run
`python scripts/ingest.py` (and re-calibrate `SIMILARITY_THRESHOLD` with `scripts/probe_retrieval.py`).

How ingestion now behaves under provider problems: no-credit / invalid-key errors **abort the run immediately**
with one clear message (remaining pages are not fetched); genuine rate limits are retried with exponential backoff;
only chunks whose text changed are re-embedded (`--force` re-embeds all), which keeps API usage and cost low.

## First-run checklist (after ingestion works)
```bash
python scripts/check_providers.py              # lists models your key can use; tests a chat call and an embedding call
python scripts/probe_retrieval.py --calibrate  # prints scores for in-scope vs out-of-scope questions and suggests SIMILARITY_THRESHOLD
```
`NotFoundError 404` in the chat log means `LLM_MODEL` does not exist for your key/endpoint: take a name from the
`check_providers` list. Embedding models differ a lot in their baseline similarity (Gemini scores unrelated text around
0.5), so never keep the default threshold without calibrating. Edit `config/calibration_queries.json` to add your own questions.
If a reasoning model returns empty answers, raise `LLM_MAX_TOKENS` or choose a non-reasoning ("flash-lite"/"mini") model.

## Troubleshooting ingestion
| Symptom | Meaning | What to do |
|---|---|---|
| `NO CREDITS ... insufficient_quota` | Account has no prepaid balance (OpenAI) | Add credit, or switch provider (see above) |
| `API key rejected` / `model ... not found` | Wrong key, or model name not available at that endpoint | Check `LLM_API_KEY`, `EMBEDDING_MODEL`, `*_BASE_URL`; list models with the `curl .../models` command in `.env.example` |
| `per-minute rate limit persisted...` | Free-tier request/token rate limit | Wait a minute and run the same command again: **progress is saved, the run resumes**. Lower `EMBEDDING_BATCH_SIZE` / raise `EMBEDDING_MIN_INTERVAL_S` |
| `DAILY free quota ... used up` | Daily cap reached | Wait for the reset, use another key, or use local embeddings (option C) |
| `extraction failed: Extracted only N characters` | Page is JavaScript-rendered or its layout changed | `python scripts/inspect_extraction.py --id <source-id>`; set `content_selector` or ask for a headless-browser option |

Ingestion is resumable and cheap: chunks are stored batch by batch, unchanged chunks are never re-embedded, and
tiny sections are merged (`CHUNK_MIN_CHARS`) so a page with 130 one-line sections does not become 130 vectors.

## Configuration
All via environment variables (see `.env.example`). Key ones: `LLM_*`, `EMBEDDING_*`,
`SIMILARITY_THRESHOLD`, `RETRIEVAL_TOP_K`, `ADMIN_TOKEN`, `CONTACT_URL`, `AUTO_INGEST_ON_STARTUP`,
`RATE_LIMIT_PER_MINUTE`, `CRAWLER_USER_AGENT`, `REQUEST_DELAY_S`.

## API
| Method & path | Purpose | Auth |
|---|---|---|
| `POST /api/chat` `{question, session_id?}` | Answer + `sources` + `intent`, `answered`, retrieval debug in `metadata` | none (rate-limited) |
| `DELETE /api/chat/{session_id}` | Clear a conversation | none |
| `POST /api/ingest?force=false` | Refresh the KB from the 7 approved URLs | `X-Admin-Token` |
| `GET /api/health` | Status, chunk count, last refresh | none |
| `GET /api/sources` | Per-source status, chunk count, version, last error | none |
| `GET /api/logs/unanswered` | Unanswered questions (question, session, time, retrieval result, reason) | `X-Admin-Token` |
| `POST /api/leads` | Optional lead capture (consent required, stored locally only) | none (rate-limited) |
| `GET /api/logs/leads` | View captured leads | `X-Admin-Token` |

Interactive docs: `/docs`.

## Knowledge-base refresh
`python scripts/ingest.py` or `POST /api/ingest`. Only the 7 configured URLs are fetched. Pages whose cleaned
content hash is unchanged are skipped; changed pages are re-chunked, new chunks upserted, stale ones deleted
afterwards (deterministic ids prevent duplicates). A failed fetch/extraction/embedding leaves that page's
existing chunks untouched and records the error. Every run is stored in SQLite (`refresh_runs`, `source_status`).
Scheduled refresh is not implemented (optional): call the endpoint from a cron job.

## Tests
```bash
pytest                                  # all tests, fully offline (hash embeddings, fake LLM, fake HTTP session)
```
Covers: extraction/noise removal, tables, price-with-context chunking, scraper (robots, UA, rate limit, retries,
unapproved URL), pipeline (unchanged/failed/changed refresh, no duplicates), retrieval (gate, threshold, filter, BM25),
guardrails (booking, medical, injection, amounts, claims), chat scenarios T1-T8 logic, API (auth, validation, rate limit, leads).
**These prove the logic, not the answer quality of a real LLM**; that is what `run_assessment_tests.py` is for.

## Deployment
The app needs: a persistent folder for `DATA_DIR` (ChromaDB + SQLite), env vars, one worker, outbound HTTPS.

**Render (recommended): `render.yaml` included.** Create a Web Service from the repo (Docker), attach a 1 GB disk at
`/app/data`, set `LLM_API_KEY`, `ADMIN_TOKEN`, a real `CRAWLER_USER_AGENT`. First boot ingests automatically
(`AUTO_INGEST_ON_STARTUP=true`); or call `POST /api/ingest`. Health check path: `/api/health`.

**Hosting limitations to document honestly:**
- Render's free instance has **no persistent disk and sleeps after inactivity**. With `AUTO_INGEST_ON_STARTUP=true`
  it rebuilds the KB after each wake (costs a few embedding calls and ~15 s), but a sleeping app is a poor fit for
  "available for 7 days". Use a paid instance with a disk, or another host with an always-on tier (Fly.io volume, Railway volume).
- Sessions are in memory: lost on restart, not shared across workers (keep `--workers 1`).
- The rate limiter and session store are per-process.
- Free LLM/embedding tiers have quotas; a reviewer burst could exhaust them.

Test after deploying: `/api/health` shows `knowledge_base_ready: true`, `/api/sources` shows all 7 `ok`,
then run the scenarios with `--base-url`.

## Known limitations
- Extractor selectors are generic; tune per page with `content_selector` (JS-rendered pages need a headless browser).
- Table cells with `rowspan/colspan` are not expanded.
- Price post-check validates amounts that carry a currency marker (`$`, `USD`, `4,500 EUR`...); written-out numbers ("five thousand") and `5k` are not recognised.
- Heuristic detectors (booking / personal-medical / injection) are English-centric with partial es/fr/ar coverage; the LLM intent classifier is the second net.
- Canned messages exist in en/es/ar/fr only (others fall back to English); LLM answers follow the user's language.
- Conflicting prices between pages are surfaced by the model per the prompt, not detected programmatically.
- OpenAI/Anthropic client calls were not exercised against the live APIs in my environment.

## Review-call cheat sheet (45 min, live change)
| If asked to... | Change here |
|---|---|
| Make the bot more/less strict | `SIMILARITY_THRESHOLD` (.env); `GENERATOR_SYSTEM` in `app/core/prompts.py` |
| Change the fallback/refusal wording | `_MESSAGES` in `app/core/prompts.py` |
| Add a new page / source | `config/sources.json` then `python scripts/ingest.py` |
| Add a refusal rule (e.g. block "refund" questions) | regex lists + intent routing in `app/rag/guardrails.py` / `ChatService.handle` |
| Change chunk size | `CHUNK_MAX_CHARS`; logic in `app/ingestion/chunker.py` |
| Switch LLM / embedding provider | `LLM_*` / `EMBEDDING_*` env vars (re-run ingest after changing embeddings) |
| Show why an answer was given | `metadata.retrieval` in `/api/chat` response, or `scripts/probe_retrieval.py` |
Know the flow in `app/services/chat_service.py` (the docstring lists the six stages) and why each guardrail exists.

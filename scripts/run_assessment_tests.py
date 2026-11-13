"""Runs the assessment scenarios against the REAL application and writes a results report.

  python scripts/run_assessment_tests.py                     # in-process (needs KB ingested + API keys)
  python scripts/run_assessment_tests.py --base-url https://your-app.onrender.com   # test the deployed URL

Output: docs/TEST_RESULTS.md and docs/test_results.json (observed outputs, never invented).
Automatic checks are only sanity checks: read every answer against its source page and
tick the 'Reviewer' box yourself before submitting.
"""
import _bootstrap  # noqa: F401
import argparse
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANSWER_GAP = ["not listed", "couldn't find", "could not find", "no encontr", "no pude", "لم أتمكن", "no aparece", "السعر غير مذكور"]


def run_check(check: str, last: dict) -> tuple[bool, str]:
    name, _, arg = check.partition(":")
    ans, srcs = last["answer"], last["sources"]
    low = ans.lower()
    if name == "answered":
        return last["answered"], "answered == true"
    if name == "not_answered":
        return not last["answered"], "answered == false (refusal/fallback)"
    if name == "sources":
        return bool(srcs), "has source URL(s)"
    if name == "no_sources":
        return not srcs, "no source URLs on refusal"
    if name == "sources_if_answered":
        return (bool(srcs) or not last["answered"]), "source URL present whenever answered"
    if name == "source_url_contains":
        return any(arg in s["url"] for s in srcs), f"a source URL contains '{arg}'"
    if name == "contains_any":
        opts = [o.lower() for o in arg.split("|")]
        return any(o in low for o in opts), f"answer mentions one of {opts}"
    if name == "not_contains_any":
        opts = [o.lower() for o in arg.split("|")]
        return not any(o in low for o in opts), f"answer must not contain {opts}"
    if name == "price_or_honest_gap":
        has_amount = bool(re.search(r"(\$|€|£|USD|EUR|MXN)\s?\d|\d\s?(USD|EUR|MXN)", ans))
        honest = any(g.lower() in low for g in ANSWER_GAP)
        return has_amount or honest, "shows a price with currency OR honestly says it is not listed"
    return False, f"unknown check '{check}'"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=4.0, help="seconds to wait between questions (free-tier rate limits)")
    ap.add_argument("--base-url", help="test a deployed instance over HTTP instead of in-process")
    ap.add_argument("--questions", default=str(ROOT / "tests" / "assessment_questions.json"))
    args = ap.parse_args()
    cases = json.loads(Path(args.questions).read_text(encoding="utf-8"))["cases"]

    if args.base_url:
        import requests

        def ask(question: str, sid: str) -> dict:
            r = requests.post(f"{args.base_url.rstrip('/')}/api/chat", json={"question": question, "session_id": sid}, timeout=90)
            r.raise_for_status()
            return r.json()
        mode = f"HTTP ({args.base_url})"
    else:
        from app.core.config import get_settings
        from app.core.container import build_container

        container = build_container(get_settings())
        if container.retriever.is_empty():
            raise SystemExit("Knowledge base is empty. Run: python scripts/ingest.py")

        def ask(question: str, sid: str) -> dict:
            res = container.chat_service.handle(question, sid)
            return {"answer": res.answer, "sources": res.sources, "intent": res.intent,
                    "answered": res.answered, "metadata": res.metadata}
        mode = "in-process"

    report, md = [], [
        "# Assessment test results\n",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')} | mode: {mode}\n",
        "> Observed outputs from running the real application. Automatic checks are sanity checks only; "
        "the 'Reviewer' line must be confirmed by a human against the source page.\n",
    ]
    passed = 0
    for case in cases:
        sid = "t-" + uuid.uuid4().hex[:16]
        transcript, last = [], None
        for turn in case["turns"]:
            print(f"[{case['id']}] {turn[:70]}", flush=True)
            last, err = None, None
            for attempt in range(3):
                try:
                    last = ask(turn, sid)
                    break
                except Exception as exc:  # usually a rate limit: wait and retry, then record the failure
                    err = exc
                    if attempt < 2:
                        print(f"    request failed ({exc.__class__.__name__}); waiting 30s, then retrying", flush=True)
                        time.sleep(30)
            if last is None:
                last = {"answer": f"<<request failed: {err}>>", "sources": [], "intent": "error", "answered": False, "metadata": {}}
            time.sleep(args.delay)
            transcript.append({"question": turn, "answer": last["answer"], "intent": last.get("intent"),
                               "answered": last.get("answered"), "sources": last.get("sources", []),
                               "retrieved": last.get("metadata", {}).get("retrieval", {}).get("retrieved", []),
                               "standalone_question": last.get("metadata", {}).get("analysis", {}).get("standalone_question")})
        results = [(c, *run_check(c, last)) for c in case["checks"]]
        ok = all(r[1] for r in results)
        passed += ok
        report.append({"id": case["id"], "title": case["title"], "auto_pass": ok, "transcript": transcript,
                       "checks": [{"check": c, "passed": p, "meaning": m} for c, p, m in results]})

        md += [f"\n## {case['id']} - {case['title']}  ->  **{'PASS' if ok else 'FAIL'}** (automatic)\n",
               f"**Expected behaviour:** {case['expected']}\n"]
        for t in transcript:
            md.append(f"**User:** {t['question']}\n")
            if t["standalone_question"] and t["standalone_question"] != t["question"]:
                md.append(f"*Resolved query:* {t['standalone_question']}\n")
            md.append(f"**Retrieved documents:** " + (", ".join(f"`{r['url'].split('/')[-1][:50]}` ({r['score']})" for r in t["retrieved"]) or "none above the relevance threshold") + "\n")
            md.append(f"**Answer** (intent: {t['intent']}, answered: {t['answered']}):\n\n> " + t["answer"].replace("\n", "\n> ") + "\n")
            md.append("**Source URL(s):** " + (", ".join(s["url"] for s in t["sources"]) or "none") + "\n")
        md.append("**Automatic checks:** " + "; ".join(f"{'OK' if p else 'FAILED'} - {m}" for _, p, m in results) + "\n")
        md.append("**Reviewer (manual, fill in):** [ ] answer matches the source page   [ ] no unsupported facts\n")

    md.insert(3, f"**Automatic summary: {passed}/{len(cases)} passed**\n")
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    (out / "TEST_RESULTS.md").write_text("\n".join(md), encoding="utf-8")
    (out / "test_results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{passed}/{len(cases)} automatic checks passed. Wrote docs/TEST_RESULTS.md and docs/test_results.json")


if __name__ == "__main__":
    main()
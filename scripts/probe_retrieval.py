"""Show retrieval scores and calibrate SIMILARITY_THRESHOLD.

  python scripts/probe_retrieval.py "price of adrenal package" "weather in Cancun"   # ad-hoc queries
  python scripts/probe_retrieval.py --calibrate                                      # uses config/calibration_queries.json
Edit config/calibration_queries.json to add your own in-scope / out-of-scope questions.
"""
import _bootstrap  # noqa: F401
import argparse
import json
from pathlib import Path

from app.core.config import BASE_DIR, get_settings
from app.core.container import build_container
from app.rag.calibration import suggest_threshold


def top_similarity(container, query: str) -> float:
    res = container.retriever.retrieve(query)  # unfiltered search over everything
    return max((c["score"] for c in res.candidates), default=0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--file", default=str(BASE_DIR / "config" / "calibration_queries.json"))
    args = ap.parse_args()

    c = build_container(get_settings())
    print(f"threshold={c.retriever.threshold}  chunks={c.store.count()}  embedder={c.embedder.name}\n")

    if args.calibrate:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        scores = {}
        for group in ("in_scope", "out_of_scope"):
            print(f"--- {group} ---")
            scores[group] = []
            for q in data[group]:
                sc = top_similarity(c, q)
                scores[group].append(sc)
                print(f"  {sc:.3f}  {q}")
        r = suggest_threshold(scores["in_scope"], scores["out_of_scope"])
        print("\n" + json.dumps(r, indent=2))
        print(f"\n=> put this in .env:  SIMILARITY_THRESHOLD={r['suggested']}")
        return

    for q in args.queries or ["How much does the adrenal treatment cost?", "What is the weather in Cancun?"]:
        res = c.retriever.retrieve(q)
        print(f"Q: {q}\n   result={res.reason} top_score={res.top_score:.3f}")
        for cand in res.candidates[:5]:
            print(f"   {cand['score']:.3f}  {cand['url']}")
        print()


if __name__ == "__main__":
    main()

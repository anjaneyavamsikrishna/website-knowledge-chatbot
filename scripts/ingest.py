"""Ingestion command.   python scripts/ingest.py [--force] [--json]"""
import _bootstrap  # noqa: F401
import argparse
import json
import sys

from app.core.config import get_settings
from app.core.container import build_container
from app.core.logging import configure_logging


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch the 7 approved URLs and (re)build the knowledge base.")
    ap.add_argument("--force", action="store_true", help="re-embed pages even if their content is unchanged")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    report = build_container(settings).refresh_service.refresh(force=args.force, trigger="cli")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report["results"]:
            print(f"[{r['status']:<9}] {r['chunks']:>3} chunks  {r['url']}" + (f"\n            error: {r['error']}" if r["error"] else ""))
        print(f"\nupdated={report['updated']} unchanged={report['unchanged']} failed={report['failed']} total_chunks={report['total_chunks']}")
    errors = " ".join((r["error"] or "") for r in report["results"])
    if "NO CREDITS" in errors or "key rejected" in errors:
        print("\nHINT: account/billing/key problem, not a code problem. See README -> 'Free / single-key setups'.")
    elif "rate limit" in errors or "DAILY" in errors:
        print("\nHINT: provider rate/daily limit. Progress is saved: wait a minute (or until the daily reset) and run the same command again; it resumes.")
    return 1 if report["failed"] == len(report["results"]) else 0


if __name__ == "__main__":
    sys.exit(main())

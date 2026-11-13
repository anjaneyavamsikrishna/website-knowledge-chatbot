"""Look at what the extractor produces for the approved pages WITHOUT embedding anything.
Run this first on your machine and read the output for every URL (especially prices).

  python scripts/inspect_extraction.py                 # fetch all approved URLs politely
  python scripts/inspect_extraction.py --id price-list # one source
  python scripts/inspect_extraction.py --html-dir saved_html   # offline: files named <source-id>.html
Writes cleaned text + chunks to data/inspect/<source-id>.txt
"""
import _bootstrap  # noqa: F401
import argparse
from pathlib import Path

from app.core.config import get_settings
from app.ingestion.chunker import chunk_page
from app.ingestion.extractor import ExtractionError, extract_page
from app.ingestion.scraper import ApprovedScraper
from app.ingestion.sources import load_sources


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="only this source id (see config/sources.json)")
    ap.add_argument("--html-dir", help="read <id>.html from this folder instead of fetching")
    args = ap.parse_args()

    s = get_settings()
    sources = [x for x in load_sources(s.sources_file) if not args.id or x.id == args.id]
    scraper = None if args.html_dir else ApprovedScraper(
        [x.url for x in load_sources(s.sources_file)], user_agent=s.crawler_user_agent,
        delay_s=s.request_delay_s, timeout_s=s.request_timeout_s, max_retries=s.request_max_retries)
    out_dir = s.data_dir / "inspect"
    out_dir.mkdir(parents=True, exist_ok=True)

    for src in sources:
        print("=" * 100, f"\n{src.id}  ({src.page_type})\n{src.url}")
        if args.html_dir:
            html = (Path(args.html_dir) / f"{src.id}.html").read_text(encoding="utf-8")
        else:
            res = scraper.fetch(src.url)
            if not res.ok:
                print("  FETCH FAILED:", res.error)
                continue
            html = res.html
        try:
            page = extract_page(html, url=src.url, page_type=src.page_type,
                                content_selector=src.content_selector, min_chars=0)
        except ExtractionError as exc:
            print("  EXTRACTION FAILED:", exc)
            continue
        chunks = chunk_page(page, max_chars=s.chunk_max_chars, extracted_at="inspect", content_version="inspect",
                             min_chunk_chars=s.chunk_min_chars)
        lines = [f"TITLE: {page.title}", f"CHARS: {len(page.text)}  SECTIONS: {len(page.sections)}  CHUNKS: {len(chunks)}", ""]
        for i, c in enumerate(chunks):
            lines += [f"--- chunk {i} [{c.chunk_id}] ---", c.text, ""]
        (out_dir / f"{src.id}.txt").write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines[:2]))
        print(f"  (full text written to {out_dir / (src.id + '.txt')})")
        if len(page.text) < 400:
            print("  WARNING: very little text. Content may be JavaScript-rendered; consider setting content_selector.")


if __name__ == "__main__":
    main()

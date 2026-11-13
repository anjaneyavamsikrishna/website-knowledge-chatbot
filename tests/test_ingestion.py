import pytest
import requests

from app.ingestion.chunker import chunk_page
from app.ingestion.extractor import ExtractionError, ExtractedPage, Block, Section, extract_page
from app.ingestion.scraper import ApprovedScraper
from tests.conftest import HTML, PKG_URL, PRICE_URL, PROFILE_URL

REQUIRED_META = {"source_url", "page_title", "page_type", "chunk_id", "extracted_at", "content_version"}


def extract(url, ptype):
    return extract_page(HTML[url], url=url, page_type=ptype, min_chars=100)


def test_extract_removes_noise_and_keeps_content():
    page = extract(PKG_URL, "treatment_package")
    for noise in ("NOISE_SCRIPT", "NAVMENU", "COOKIE_NOISE", "FOOTER_NOISE"):
        assert noise not in page.text
    assert "USD 4,500" in page.text and "Airport transfers" in page.text
    assert page.title.startswith("Demo Adrenal Package")


def test_table_rows_become_self_contained_sentences():
    page = extract(PRICE_URL, "price_list")
    rows = [b.text for s in page.sections for b in s.blocks if b.kind == "table_row"]
    assert any("Treatment: Demo Adrenal Package" in r and "Price: USD 4,500" in r and "14 days" in r for r in rows)
    assert "LOGO_NOISE" not in page.text


def test_video_iframe_kept_as_text():
    assert "Clinic tour video" in extract(PROFILE_URL, "clinic_profile").text


def test_too_little_content_raises():
    with pytest.raises(ExtractionError):
        extract_page("<html><body><p>tiny</p></body></html>", url="u", page_type="clinic_profile", min_chars=200)


def test_duplicate_blocks_removed_within_page():
    html = "<main>" + "<p>Repeated sentence about the clinic services offered here.</p>" * 3 + "<p>" + "x " * 100 + "</p></main>"
    page = extract_page(html, url="u", page_type="clinic_profile", min_chars=50)
    assert page.text.count("Repeated sentence") == 1


def test_chunk_metadata_and_price_stays_with_context():
    page = extract(PKG_URL, "treatment_package")
    chunks = chunk_page(page, max_chars=1100, extracted_at="2026-01-01T00:00:00+00:00", content_version="abc")
    assert chunks
    for c in chunks:
        assert REQUIRED_META <= set(c.metadata)
        assert c.metadata["source_url"] == PKG_URL
    price_chunk = next(c for c in chunks if "USD 4,500" in c.text)
    assert "Starting from" in price_chunk.text and "accommodation" in price_chunk.text
    assert "Package Price" in price_chunk.text  # section heading travels with the price


def test_price_sentence_never_split_in_long_section():
    long_para = " ".join(f"Filler sentence number {i} describes services." for i in range(60))
    blocks = [Block("paragraph", long_para), Block("paragraph", "Package price"),
              Block("paragraph", "Starting from USD 4,500 including accommodation.")]
    page = ExtractedPage("u", "T", "treatment_package", [Section("Pricing", blocks)], "x")
    chunks = chunk_page(page, max_chars=500, extracted_at="t", content_version="v")
    assert len(chunks) > 1
    holder = [c for c in chunks if "USD 4,500" in c.text]
    assert len(holder) == 1 and "Package price" in holder[0].text and "including accommodation" in holder[0].text


def test_chunk_ids_deterministic():
    page = extract(PKG_URL, "treatment_package")
    a = chunk_page(page, max_chars=1100, extracted_at="t1", content_version="v")
    b = chunk_page(page, max_chars=1100, extracted_at="t2", content_version="v")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


# ------------------------------------------------------------------------ scraper
class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.content = status, body
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append((url, headers))
        r = self.routes.get(url)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        return r or Resp(404)


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def monotonic(self):
        return self.now


def make_scraper(routes, **kw):
    clock = Clock()
    s = ApprovedScraper([PKG_URL, PRICE_URL], user_agent="TestBot/1.0 (contact: t@example.com)", delay_s=1.0,
                        max_retries=2, session=FakeSession(routes), sleep=clock.sleep, monotonic=clock.monotonic, **kw)
    return s, clock


def test_scraper_rejects_unapproved_url():
    s, _ = make_scraper({})
    with pytest.raises(ValueError):
        s.fetch("https://www.placidway.com/some/other/page")


def test_scraper_respects_robots_disallow_and_uses_user_agent():
    routes = {"https://www.placidway.com/robots.txt": Resp(200, b"User-agent: *\nDisallow: /package/\n"),
              PKG_URL: Resp(200, b"<html></html>"), PRICE_URL: Resp(200, b"<html>ok</html>")}
    s, _ = make_scraper(routes)
    blocked = s.fetch(PKG_URL)
    assert not blocked.ok and "robots" in blocked.error
    assert s.fetch(PRICE_URL).ok
    assert all(h["User-Agent"].startswith("TestBot/1.0") for _, h in s.session.calls)
    assert PKG_URL not in [u for u, _ in s.session.calls]  # never requested a disallowed page


def test_scraper_rate_limits_to_about_one_request_per_second():
    routes = {"https://www.placidway.com/robots.txt": Resp(404), PKG_URL: Resp(200, b"a"), PRICE_URL: Resp(200, b"b")}
    s, clock = make_scraper(routes)
    s.fetch(PKG_URL); s.fetch(PRICE_URL)
    assert len(s.session.calls) == 3  # robots + 2 pages
    assert clock.now >= 2.0  # at least 1s gap between each of the 3 requests


def test_scraper_retries_then_succeeds_and_gives_up_cleanly():
    routes = {"https://www.placidway.com/robots.txt": Resp(404), PKG_URL: [Resp(503), Resp(200, b"fine")],
              PRICE_URL: requests.Timeout("slow")}
    s, _ = make_scraper(routes)
    assert s.fetch(PKG_URL).ok
    failed = s.fetch(PRICE_URL)
    assert not failed.ok and "network error" in failed.error


def test_scraper_robots_5xx_means_do_not_crawl():
    s, _ = make_scraper({"https://www.placidway.com/robots.txt": Resp(500), PKG_URL: Resp(200, b"x")})
    res = s.fetch(PKG_URL)
    assert not res.ok and PKG_URL not in [u for u, _ in s.session.calls]


# ------------------------------------------------------------------------ pipeline
def test_pipeline_ingest_unchanged_failure_and_change(container, scraper):
    first = container.refresh_service.refresh()
    assert first["updated"] == 3 and first["failed"] == 0
    n = container.store.count()
    assert n > 0

    second = container.refresh_service.refresh()
    assert second["unchanged"] == 3 and container.store.count() == n  # no duplicates, no re-embedding

    scraper.fail.add(PRICE_URL)
    third = container.refresh_service.refresh(force=True)
    assert third["failed"] == 1
    assert container.store.count() == n  # old knowledge preserved on failure
    assert container.db.get_source_status(PRICE_URL)["status"] == "error"
    assert container.db.get_source_status(PRICE_URL)["last_error"] == "HTTP 503"

    scraper.fail.clear()
    scraper.pages[PKG_URL] = HTML[PKG_URL].replace("USD 4,500", "USD 5,100")
    fourth = container.refresh_service.refresh()
    assert fourth["updated"] == 1 and fourth["unchanged"] == 2
    texts = [d["text"] for d in container.store.all_documents({"source_url": PKG_URL})]
    assert any("USD 5,100" in t for t in texts) and not any("USD 4,500" in t for t in texts)


def test_pipeline_extraction_failure_keeps_old_data(container, scraper):
    container.refresh_service.refresh()
    n = container.store.count()
    scraper.pages[PKG_URL] = "<html><body><p>tiny</p></body></html>"
    res = container.refresh_service.refresh()
    assert res["failed"] == 1 and container.store.count() == n

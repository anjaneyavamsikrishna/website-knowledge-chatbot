"""HTML -> clean, structured text.

Output is a list of Sections (heading path + blocks). Headings, paragraphs, list items,
and tables are preserved. Tables become one self-contained sentence per row
("Treatment: X; Price: USD 4,500; Notes: ...") so a price never loses its label/currency.

IMPORTANT: selectors here are generic because pages differ. Use
`python scripts/inspect_extraction.py` to look at the cleaned output for each of the 7
URLs and, if needed, set a `content_selector` for that source in config/sources.json.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


class ExtractionError(Exception):
    pass


HEADINGS = {f"h{i}": i for i in range(1, 7)}
TEXT_BLOCKS = {"p", "blockquote", "pre", "figcaption", "dt", "dd", "summary"}
INLINE = {"a", "span", "strong", "b", "em", "i", "u", "small", "sup", "sub", "mark", "abbr", "code", "font", "time", "label"}
BLOCKISH = list(HEADINGS) + ["ul", "ol", "table", "p", "div", "section", "article", "li", "dl", "blockquote", "pre"]
REMOVE_TAGS = [
    "script", "style", "noscript", "template", "svg", "canvas", "object", "embed",
    "input", "select", "textarea", "button", "nav", "footer", "aside",
]
NOISE_ATTR_RE = re.compile(r"(cookie|consent|gdpr|adsbygoogle|advert|popup|newsletter|breadcrumb)", re.I)
NOISE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "dialog"}
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")
BOILERPLATE = {
    "read more", "learn more", "click here", "share", "login", "log in", "sign in", "sign up",
    "register", "menu", "skip to content", "view all", "view more", "back to top", "close", "next", "previous",
}


@dataclass
class Block:
    kind: str  # heading | paragraph | list_item | table_row
    text: str
    level: int = 0


@dataclass
class Section:
    heading_path: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ExtractedPage:
    url: str
    title: str
    page_type: str
    sections: list[Section]
    text: str  # full cleaned text (used for hashing and sanity checks)


# ------------------------------------------------------------------------- text helpers
def _clean_text(s: str) -> str:
    s = s.replace("\xa0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([$€£¥₹])\s+(\d)", r"\1\2", s)
    return s


def _inline(node: Tag) -> str:
    return _clean_text(node.get_text(" "))


# ----------------------------------------------------------------------------- cleaning
def _prepare(soup: BeautifulSoup) -> None:
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # Keep embedded videos as text ("Video: <title> (<url>)") before iframes are removed.
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or iframe.get("data-src") or ""
        if any(h in src for h in VIDEO_HOSTS):
            title = (iframe.get("title") or "embedded video").strip()
            p = soup.new_tag("p")
            p.string = f"Video: {title} ({src})"
            iframe.replace_with(p)
        else:
            iframe.decompose()
    for tag in soup.find_all(REMOVE_TAGS):
        tag.decompose()
    # <header> is noise only when it is page chrome (not inside main/article).
    for header in soup.find_all("header"):
        if not header.find_parent(["main", "article"]):
            header.decompose()
    # NOTE: <form> is deliberately NOT removed; some CMSs wrap the whole page in one.
    for el in list(soup.find_all(True)):
        if getattr(el, "decomposed", False) or el.attrs is None:
            continue
        marker = " ".join([" ".join(el.get("class", [])), el.get("id", "") or ""])
        if NOISE_ATTR_RE.search(marker) or el.get("role") in NOISE_ROLES or el.get("aria-hidden") == "true":
            el.decompose()


def _pick_root(soup: BeautifulSoup, selector: str | None) -> Tag:
    if selector:
        found = soup.select_one(selector)
        if found is not None:
            return found
    body = soup.body or soup
    body_len = len(body.get_text(" ", strip=True))
    for sel in ("main", "[role=main]", "article"):
        cand = soup.select_one(sel)
        if cand is not None and len(cand.get_text(" ", strip=True)) >= max(300, 0.3 * body_len):
            return cand
    return body


# ----------------------------------------------------------------------------- walking
def _walk(node: Tag, out: list[Block]) -> None:
    buf: list[str] = []

    def flush() -> None:
        text = _clean_text(" ".join(buf))
        buf.clear()
        if text:
            out.append(Block("paragraph", text))

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in HEADINGS:
            flush()
            text = _inline(child)
            if text:
                out.append(Block("heading", text, HEADINGS[name]))
        elif name in ("ul", "ol"):
            flush()
            _list(child, out)
        elif name == "table":
            flush()
            out.extend(_table(child))
        elif name in TEXT_BLOCKS:
            flush()
            if child.find(BLOCKISH):
                _walk(child, out)
            else:
                text = _inline(child)
                if text:
                    out.append(Block("paragraph", text))
        elif name in INLINE:
            if child.find(BLOCKISH):
                flush()
                _walk(child, out)
            else:
                buf.append(_inline(child))
        else:
            flush()
            _walk(child, out)
    flush()


def _list(lst: Tag, out: list[Block]) -> None:
    for li in lst.find_all("li", recursive=False):
        parts, nested = [], []
        for c in li.children:
            if isinstance(c, Comment):
                continue
            if isinstance(c, Tag) and c.name in ("ul", "ol"):
                nested.append(c)
            elif isinstance(c, Tag):
                parts.append(c.get_text(" "))
            else:
                parts.append(str(c))
        text = _clean_text(" ".join(parts))
        if text:
            out.append(Block("list_item", text))
        for n in nested:
            _list(n, out)


def _table(table: Tag) -> list[Block]:
    caption = table.find("caption")
    cap = _inline(caption) if caption else ""
    parsed: list[tuple[list[str], bool]] = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cells = [c for c in tr.find_all(["th", "td"]) if c.find_parent("tr") is tr]
        parsed.append(([_inline(c) for c in cells], bool(cells) and all(c.name == "th" for c in cells)))

    headers: list[str] | None = None
    rows: list[list[str]] = []
    for texts, is_header in parsed:
        if not any(texts):
            continue
        if is_header and headers is None:
            headers = texts
        else:
            rows.append(texts)
    if headers is None and len(rows) > 1 and not any(re.search(r"\d", t) for t in rows[0]):
        headers = rows.pop(0)  # first data-less row acts as header

    blocks = []
    for texts in rows:
        if headers and len(headers) == len(texts):
            line = "; ".join(f"{h}: {v}" for h, v in zip(headers, texts) if v)
        else:
            line = " | ".join(t for t in texts if t)
        if cap:
            line = f"{cap} - {line}"
        if line:
            blocks.append(Block("table_row", line))
    return blocks


# ------------------------------------------------------------------------ public API
def extract_page(html: str, *, url: str, page_type: str, content_selector: str | None = None, min_chars: int = 200) -> ExtractedPage:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = _clean_text(title_tag.get_text(" ")) if title_tag else ""
    if not title:
        h1 = soup.find("h1")
        title = _clean_text(h1.get_text(" ")) if h1 else url

    _prepare(soup)
    root = _pick_root(soup, content_selector)
    raw: list[Block] = []
    _walk(root, raw)

    blocks: list[Block] = []
    seen: set[str] = set()
    for b in raw:
        key = b.text.lower()
        if not re.search(r"\w", b.text) or key in BOILERPLATE:
            continue
        if b.kind != "heading":
            if key in seen:  # exact duplicates within the page
                continue
            seen.add(key)
        blocks.append(b)

    sections = _build_sections(blocks, title)
    text = "\n".join(b.text for b in blocks)
    if len(text) < min_chars:
        raise ExtractionError(f"Extracted only {len(text)} characters (< {min_chars}); page layout may have changed or content is JavaScript-rendered")
    return ExtractedPage(url=url, title=title, page_type=page_type, sections=sections, text=text)


def _build_sections(blocks: list[Block], title: str) -> list[Section]:
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    current = Section(heading_path=title)
    for b in blocks:
        if b.kind == "heading":
            if current.blocks:
                sections.append(current)
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, b.text))
            current = Section(heading_path=" > ".join(t for _, t in stack))
        else:
            current.blocks.append(b)
    if current.blocks:
        sections.append(current)
    return sections

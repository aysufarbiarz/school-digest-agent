"""Find school links in emails and open them in a headless browser.

SchoolMessenger messages, Sway newsletters and similar pages only show their
content after JavaScript runs, so a plain HTTP fetch is not enough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .documents import MAX_TEXT_CHARS, MIN_TEXT_CHARS, convert_pdf

SKIP_PATTERNS = re.compile(
    r"unsubscribe|optout|opt-out|preferences|manage.?subscription|mailto:|tel:|"
    r"facebook\.com|twitter\.com|x\.com/|instagram\.com|linkedin\.com|youtube\.com|"
    r"privacy|terms-of|/login|/signin",
    re.IGNORECASE,
)
PAGE_TIMEOUT_MS = 30_000
MAX_SUBDOCS_PER_PAGE = 3


@dataclass
class FetchedLink:
    url: str
    final_url: str = ""
    title: str = ""
    text: str = ""
    image_paths: list[Path] = field(default_factory=list)
    readable: bool = True
    note: str = ""
    subdocs: list["FetchedLink"] = field(default_factory=list)


def domain_allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def extract_links(html: str, domains: list[str], limit: int) -> list[str]:
    """Return unique, followable links from an email's HTML body, in order."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    seen: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith(("http://", "https://")):
            continue
        if SKIP_PATTERNS.search(href):
            continue
        if not domain_allowed(href, domains):
            continue
        href = href.split("#", 1)[0]
        if href not in seen:
            seen.append(href)
        if len(seen) >= limit:
            break
    return seen


def _is_pdf_response(resp) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return "application/pdf" in ctype


def _fetch_one(context, url: str, out_dir: Path, stem: str, depth: int = 0) -> FetchedLink:
    result = FetchedLink(url=url)
    try:
        probe = context.request.get(url, timeout=PAGE_TIMEOUT_MS, max_redirects=10)
        if probe.ok and _is_pdf_response(probe):
            conv = convert_pdf(probe.body(), out_dir, stem)
            result.final_url = probe.url
            result.text, result.image_paths = conv.text, conv.image_paths
            result.readable, result.note = conv.readable, conv.note
            return result
    except Exception:
        pass  # fall through to the browser; some hosts reject plain requests

    page = context.new_page()
    try:
        try:
            page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            page.goto(url, wait_until="load", timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(3000)
        result.final_url = page.url
        result.title = (page.title() or "")[:200]
        if "accounts.google.com" in page.url or "login.microsoftonline.com" in page.url:
            result.readable = False
            result.note = "page needs a sign-in"
            return result
        text = page.inner_text("body").strip()
        result.text = text[:MAX_TEXT_CHARS]
        if len(text) < MIN_TEXT_CHARS:
            shot = out_dir / f"{stem}.png"
            page.screenshot(path=str(shot), full_page=True)
            result.image_paths.append(shot)
            result.note = "little text on page, read from a screenshot"
        if depth == 0:
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            pdfs = []
            for h in hrefs:
                h = urljoin(page.url, h)
                if (".pdf" in h.lower() or "attachment" in h.lower()) and h not in pdfs:
                    pdfs.append(h)
            for i, h in enumerate(pdfs[:MAX_SUBDOCS_PER_PAGE]):
                sub = _fetch_one(context, h, out_dir, f"{stem}-doc{i + 1}", depth=1)
                if sub.text or sub.image_paths:
                    result.subdocs.append(sub)
    except Exception as exc:
        result.readable = False
        result.note = f"page could not be opened ({type(exc).__name__})"
    finally:
        page.close()
    return result


def fetch_links(urls: list[tuple[str, str]], out_dir: Path) -> dict[str, FetchedLink]:
    """Open each (stem, url) pair. Returns results keyed by stem."""
    results: dict[str, FetchedLink] = {}
    if not urls:
        return results
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
            viewport={"width": 1100, "height": 1400},
            accept_downloads=False,
        )
        for stem, url in urls:
            results[stem] = _fetch_one(context, url, out_dir, stem)
        browser.close()
    return results

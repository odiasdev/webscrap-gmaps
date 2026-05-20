from __future__ import annotations
import asyncio
from typing import Iterable, List, Dict, Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .utils import extract_emails, extract_phones

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=64)

_CONTACT_HINTS = ("contato", "contact", "fale", "atendimento", "suporte", "ajuda")
_MAX_HTML_BYTES = 1_500_000  # 1.5MB chega para extrair contatos


async def _fetch(client: httpx.AsyncClient, url: str):
    try:
        r = await client.get(url)
    except Exception:
        return None, None
    if r.status_code >= 400:
        return None, None
    ctype = (r.headers.get("content-type") or "").lower()
    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
        return None, None
    text = r.text or ""
    if len(text) > _MAX_HTML_BYTES:
        text = text[:_MAX_HTML_BYTES]
    return text, str(r.url)


def _extract_contact_links(soup: BeautifulSoup, max_links: int = 3):
    out, seen = [], set()
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        low = href.lower()
        if not any(k in low for k in _CONTACT_HINTS):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
        if len(out) >= max_links:
            break
    return out


async def _enrich_one(client: httpx.AsyncClient, row: Dict[str, Any]) -> None:
    url = row.get("website") or ""
    if not url:
        return

    html, final_url = await _fetch(client, url)
    if not html:
        return

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text(" ", strip=True)
    emails = set(extract_emails(text))
    phones = set(extract_phones(text))

    parsed = urlparse(final_url or url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url

    contact_targets = []
    seen = set()
    for h in _extract_contact_links(soup):
        cand = urljoin(base + "/", h)
        if cand in seen:
            continue
        seen.add(cand)
        contact_targets.append(cand)

    if contact_targets:
        sub_results = await asyncio.gather(
            *[_fetch(client, t) for t in contact_targets],
            return_exceptions=True,
        )
        for res in sub_results:
            if isinstance(res, tuple) and res[0]:
                emails.update(extract_emails(res[0]))
                phones.update(extract_phones(res[0]))

    row["emails"] = ", ".join(sorted(emails))
    if not row.get("phone") and phones:
        row["phone"] = ", ".join(sorted(phones))
    row["website_final"] = final_url or url


async def enrich_many_async(
    rows: Iterable[Dict[str, Any]],
    concurrency: int = 16,
    on_progress=None,
) -> None:
    targets: List[Dict[str, Any]] = [r for r in rows if r.get("website")]
    if not targets:
        return

    sem = asyncio.Semaphore(max(1, concurrency))
    done = 0
    total = len(targets)
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=_TIMEOUT,
        limits=_LIMITS,
        follow_redirects=True,
        verify=False,
    ) as client:

        async def task(r):
            nonlocal done
            async with sem:
                await _enrich_one(client, r)
            async with lock:
                done += 1
                if on_progress:
                    try:
                        on_progress(done, total)
                    except Exception:
                        pass

        await asyncio.gather(*[task(r) for r in targets])


# --- Backwards-compat sync wrapper (não usado pelo runner novo) ---
def fetch_site_and_enrich(url: str, save_html_path=None):
    row: Dict[str, Any] = {"website": url}
    try:
        asyncio.run(enrich_many_async([row], concurrency=1))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(enrich_many_async([row], concurrency=1))
        finally:
            loop.close()
    return {
        "emails": [e for e in (row.get("emails") or "").split(", ") if e],
        "phones": [p for p in (row.get("phone") or "").split(", ") if p],
        "final_url": row.get("website_final"),
    }

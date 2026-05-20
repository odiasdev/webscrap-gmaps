from __future__ import annotations
import asyncio
import csv
import json
import random
import re
import sys
import time
import unicodedata
from typing import Any, Dict, List

from parsel import Selector
from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    TimeoutError as PWTimeoutError,
)

from .config import settings
from .utils import (
    extract_coords_from_url,
    query_to_search_url,
    write_progress,
)
from .website_enricher import enrich_many_async


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Recursos pesados que não são necessários para extrair dados textuais.
_BLOCK_RESOURCES = {"image", "media", "font"}
_BLOCK_URL_HINTS = (
    "googlevideo.com",
    "ggpht.com",
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
)

PHONE_RE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9?\d{4})[-.\s]?\d{4}")
DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)


def _parse_rating(raw):
    if raw is None:
        return None
    s = str(raw).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_reviews_count(raw):
    if raw is None:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


# ---- horário de funcionamento ----

_DAY_KEYS = {
    "segunda": "segunda", "monday": "segunda",
    "terca": "terca", "tuesday": "terca",
    "quarta": "quarta", "wednesday": "quarta",
    "quinta": "quinta", "thursday": "quinta",
    "sexta": "sexta", "friday": "sexta",
    "sabado": "sabado", "saturday": "sabado",
    "domingo": "domingo", "sunday": "domingo",
}

_DAY_ORDER = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _normalize_day(s: str):
    s = _strip_accents((s or "").lower()).strip()
    s = re.sub(r"\([^)]*\)", "", s)  # remove notas tipo "(Dia das Mães)"
    s = s.replace("-feira", "").strip().rstrip(":,").strip()
    if not s:
        return None
    first = s.split()[0]
    return _DAY_KEYS.get(first)


def _pad_time(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h):02d}:{m}"


def _parse_hours_aria(aria: str) -> Dict[str, str]:
    """
    Parse uma aria-label como:
      "Sábado, 7:00 às 22:00; Domingo, fechado; Segunda-feira, 6:00 às 23:00; ..."
    Retorna {dia_normalizado: 'HH:MM-HH:MM' | 'fechado'}.
    """
    if not aria:
        return {}
    out: Dict[str, str] = {}
    for seg in aria.split(";"):
        seg = seg.strip().strip(".").strip()
        if not seg or "," not in seg:
            continue
        day_part, time_part = seg.split(",", 1)
        day_part = day_part.strip().lower()
        if ":" in day_part and not re.match(r"^\d", day_part):
            day_part = day_part.split(":", 1)[-1].strip()
        day_norm = _normalize_day(day_part)
        if not day_norm:
            continue
        tp = time_part.strip().lower()
        if "fechad" in tp or "closed" in tp:
            out[day_norm] = "fechado"
        elif "24 hor" in tp or "24 hour" in tp or "aberto 24" in tp:
            out[day_norm] = "00:00-23:59"
        else:
            times = re.findall(r"(\d{1,2}:\d{2})", tp)
            if len(times) >= 2:
                out[day_norm] = f"{_pad_time(times[0])}-{_pad_time(times[1])}"
    return out


def _extract_hours(sel) -> Dict[str, str]:
    """Tenta extrair horário a partir de aria-labels do painel."""
    candidates: list[str] = []
    candidates += sel.css('div.t39EBf::attr(aria-label)').getall()
    candidates += sel.css('div.OqCZI::attr(aria-label)').getall()
    candidates += sel.css('[aria-label*="Horário"]::attr(aria-label)').getall()
    candidates += sel.css('[aria-label*="Hours"]::attr(aria-label)').getall()

    for aria in candidates:
        if not aria or ";" not in aria:
            continue
        hours = _parse_hours_aria(aria)
        if hours:
            # Reordena para a sequência canônica seg→dom quando todos os dias presentes
            ordered = {d: hours[d] for d in _DAY_ORDER if d in hours}
            extras = {k: v for k, v in hours.items() if k not in ordered}
            ordered.update(extras)
            return ordered

    # Fallback: tabela com linhas (dia / horário)
    rows = sel.css('table.eK4R0e tr, tr.y0skZc')
    out: Dict[str, str] = {}
    for row in rows:
        day_text = " ".join(row.css('td:nth-child(1) *::text').getall()).strip()
        time_text = " ".join(row.css('td:nth-child(2) *::text').getall()).strip()
        day_norm = _normalize_day(day_text)
        if not day_norm or not time_text:
            continue
        tl = time_text.lower()
        if "fechad" in tl or "closed" in tl:
            out[day_norm] = "fechado"
        elif "24 hor" in tl or "24 hour" in tl:
            out[day_norm] = "00:00-23:59"
        else:
            times = re.findall(r"(\d{1,2}:\d{2})", time_text)
            if len(times) >= 2:
                out[day_norm] = f"{_pad_time(times[0])}-{_pad_time(times[1])}"
    if out:
        ordered = {d: out[d] for d in _DAY_ORDER if d in out}
        return ordered
    return {}


# ---------- helpers ----------

async def _route_filter(route):
    req = route.request
    if req.resource_type in _BLOCK_RESOURCES:
        try:
            await route.abort()
            return
        except Exception:
            pass
    url = req.url
    if any(h in url for h in _BLOCK_URL_HINTS):
        try:
            await route.abort()
            return
        except Exception:
            pass
    try:
        await route.continue_()
    except Exception:
        pass


async def _new_page(context: BrowserContext) -> Page:
    page = await context.new_page()
    await page.route("**/*", _route_filter)
    page.set_default_timeout(20_000)
    return page


async def _dismiss_popups(page: Page) -> None:
    selectors = [
        'button[aria-label*="Aceitar"]',
        'button[aria-label*="Concordo"]',
        'button[aria-label*="Accept"]',
        'button:has-text("Aceitar tudo")',
        'button:has-text("Aceitar")',
        'button:has-text("Accept all")',
    ]
    for sel in selectors:
        try:
            await page.locator(sel).first.click(timeout=1200)
            return
        except Exception:
            continue


async def _scroll_results_list(page: Page, max_places: int, max_steps: int) -> None:
    feed = page.locator('div[role="feed"]').first
    try:
        await feed.wait_for(timeout=8000)
    except PWTimeoutError:
        return

    last_count = 0
    stable = 0
    end_marker = 'span:has-text("Você chegou ao final da lista")'

    for _ in range(max_steps):
        try:
            await feed.evaluate("(el) => el.scrollTo(0, el.scrollHeight)")
        except Exception:
            try:
                await page.mouse.wheel(0, 4000)
            except Exception:
                pass

        await page.wait_for_timeout(420)

        count = await page.locator("a.hfpxzc[aria-label]").count()
        if count >= max_places:
            return

        if await page.locator(end_marker).count():
            return

        if count == last_count:
            stable += 1
            if stable >= 4:
                return
        else:
            stable = 0
        last_count = count


async def _collect_listing(page: Page, max_places: int) -> List[Dict[str, str]]:
    cards = page.locator("a.hfpxzc[aria-label]")
    total = min(await cards.count(), max_places)
    items: List[Dict[str, str]] = []
    seen = set()
    for i in range(total):
        c = cards.nth(i)
        try:
            href = await c.get_attribute("href")
            name = await c.get_attribute("aria-label")
        except Exception:
            continue
        if not href or href in seen:
            continue
        seen.add(href)
        items.append({"name": (name or "").strip(), "url": href})
    return items


async def _extract_top_reviews(page: Page, max_reviews: int) -> List[str]:
    """
    Clica na aba 'Avaliações' do painel da arena, expande os 'Mais' das primeiras
    N reviews e retorna apenas o texto. Se não houver, retorna [].
    """
    if max_reviews <= 0:
        return []

    tab_selectors = [
        'button[role="tab"][aria-label*="Avaliações"]',
        'button[role="tab"][aria-label*="Reviews"]',
        'button[role="tab"]:has-text("Avaliações")',
        'button[role="tab"]:has-text("Reviews")',
    ]
    tab_clicked = False
    for sel in tab_selectors:
        try:
            await page.locator(sel).first.click(timeout=2500)
            tab_clicked = True
            break
        except Exception:
            continue

    if not tab_clicked:
        return []

    try:
        await page.wait_for_selector(
            'div[data-review-id]', timeout=8000
        )
    except Exception:
        return []

    await page.wait_for_timeout(800)

    async def _unique_ids():
        return await page.evaluate(
            """() => {
                const seen = new Set(); const out = [];
                for (const el of document.querySelectorAll('[data-review-id]')) {
                    const id = el.getAttribute('data-review-id');
                    if (id && !seen.has(id)) { seen.add(id); out.push(id); }
                }
                return out;
            }"""
        )

    # Garante que há cards o suficiente carregados (rola o painel se preciso).
    for _ in range(8):
        ids_now = await _unique_ids()
        if len(ids_now) >= max_reviews:
            break
        try:
            await page.locator("div[data-review-id]").last.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            try:
                await page.mouse.wheel(0, 1800)
            except Exception:
                pass
        await page.wait_for_timeout(500)

    review_ids = (await _unique_ids())[:max_reviews]
    out: List[str] = []

    for rid in review_ids:
        # CSS attribute selector com escape simples (IDs do GMaps são alfanuméricos).
        card = page.locator(f'div[data-review-id="{rid}"]').first
        # Expande "Mais" se existir
        try:
            more = card.locator(
                'button.w8nwRe, button[aria-label="Mais"], button:has-text("Mais")'
            ).first
            if await more.count():
                try:
                    await more.click(timeout=800)
                    await page.wait_for_timeout(120)
                except Exception:
                    pass
        except Exception:
            pass

        text = ""
        for tsel in (
            "span.wiI7pd",
            'div[data-expandable-section]',
            "span.review-full-text",
        ):
            try:
                el = card.locator(tsel).first
                if await el.count():
                    text = (await el.inner_text(timeout=1500)).strip()
                    if text:
                        break
            except Exception:
                continue

        if text:
            out.append(text)

    return out


async def _extract_place(page: Page, item: Dict[str, str]) -> Dict[str, Any] | None:
    url = item["url"]
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector("h1.DUwDvf", timeout=8_000)
    except Exception:
        return None

    try:
        html = await page.content()
    except Exception:
        return None

    sel = Selector(text=html)

    name_parts = sel.xpath('//h1[contains(@class,"DUwDvf")]//text()').getall()
    name = " ".join(p.strip() for p in name_parts if p and p.strip()) or item.get("name") or ""

    info_texts = sel.css("div.Io6YTe::text").getall()
    info_texts = [t.strip() for t in info_texts if t and t.strip()]

    phone = None
    website = None
    for t in info_texts:
        if not phone:
            m = PHONE_RE.search(t)
            if m:
                phone = m.group(0)
        if not website:
            m = DOMAIN_RE.search(t)
            if m:
                website = m.group(0)

    if not phone:
        tel = sel.css('a[href^="tel:"]::attr(href)').get()
        if tel:
            phone = tel.replace("tel:", "").strip()

    if not website:
        for q in [
            'a[aria-label*="Site"]::attr(href)',
            'a[aria-label*="Website"]::attr(href)',
            'a[data-item-id="authority"]::attr(href)',
        ]:
            cand = sel.css(q).get()
            if cand:
                website = cand
                break

    if website and not re.match(r"^https?://", website, re.I):
        website = "http://" + website

    if website and "google.com" in website:
        website = ""

    address = sel.css('[data-item-id="address"]::text').get() or ""
    if not address:
        lab = sel.xpath(
            '//*[@aria-label[contains(., "Endereço:") or contains(., "Address:")]]/@aria-label'
        ).get()
        if lab and ":" in lab:
            address = lab.split(":", 1)[1].strip()

    rating_raw = sel.css('div.F7nice span[aria-hidden="true"]::text').get()
    reviews_raw = sel.css('div.F7nice span[aria-label*="avaliações"]::text').get()
    category = sel.css('button[jsaction*="category"]::text').get() or ""

    rating = _parse_rating(rating_raw)
    reviews_count = _parse_reviews_count(reviews_raw)
    hours = _extract_hours(sel)

    lat, lng = extract_coords_from_url(page.url)

    row: Dict[str, Any] = {
        "name": name,
        "phone": phone or "",
        "website": website or "",
        "address": address,
        "rating": rating,
        "reviews_count": reviews_count,
        "category": category,
        "hours": hours,
        "lat": lat,
        "lng": lng,
        "gmaps_url": page.url,
        "top_reviews": [],
    }

    if settings.EXTRACT_REVIEWS:
        # Delay extra somente nas arenas onde extrai reviews — atenua bloqueio.
        await asyncio.sleep(2.0 + random.random() * 2.0)
        try:
            reviews = await _extract_top_reviews(page, settings.MAX_REVIEWS_PER_PLACE)
        except Exception as e:
            print(f"[reviews] {name[:40]}: erro {e}")
            reviews = []
        row["top_reviews"] = reviews
        print(f"[reviews] {name[:40]}: {len(reviews)} extraídas")

    return row


# ---------- per-query orchestration ----------

class ProgressTracker:
    """Estado compartilhado serializado em JSON p/ a UI ler em paralelo."""

    def __init__(self, queries: List[str]):
        self._lock = asyncio.Lock()
        self.state: Dict[str, Any] = {
            "started_at": time.time(),
            "queries": {
                str(i): {
                    "query": q,
                    "status": "queued",
                    "current": 0,
                    "total": settings.MAX_PLACES,
                    "phase": "queued",
                }
                for i, q in enumerate(queries)
            },
        }
        self._flush()

    def _flush(self) -> None:
        write_progress(settings.PROGRESS_FILE, self.state)

    async def update(self, idx: int, **fields) -> None:
        async with self._lock:
            self.state["queries"][str(idx)].update(fields)
            self._flush()

    async def inc(self, idx: int, key: str = "current", amount: int = 1) -> None:
        async with self._lock:
            q = self.state["queries"][str(idx)]
            q[key] = q.get(key, 0) + amount
            self._flush()

    async def set_global(self, **fields) -> None:
        async with self._lock:
            self.state.update(fields)
            self._flush()


async def _scrape_query(
    context: BrowserContext,
    query: str,
    idx: int,
    tracker: ProgressTracker,
) -> List[Dict[str, Any]]:
    url = query_to_search_url(query, settings.DEFAULT_CENTER)
    print(f"[Q{idx}] {query!r} -> {url}")

    list_page = await _new_page(context)
    try:
        await tracker.update(idx, status="running", phase="loading")
        await list_page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await _dismiss_popups(list_page)

        await tracker.update(idx, phase="scrolling")
        await _scroll_results_list(list_page, settings.MAX_PLACES, settings.SCROLL_STEPS)

        items = await _collect_listing(list_page, settings.MAX_PLACES)
    finally:
        try:
            await list_page.close()
        except Exception:
            pass

    print(f"[Q{idx}] {len(items)} cards coletados")
    await tracker.update(idx, total=len(items) or settings.MAX_PLACES, phase="details")

    if not items:
        await tracker.update(idx, status="done", phase="empty")
        return []

    sem = asyncio.Semaphore(settings.DETAIL_CONCURRENCY)
    rows: List[Dict[str, Any]] = []
    rows_lock = asyncio.Lock()

    async def worker(item: Dict[str, str]):
        async with sem:
            page = await _new_page(context)
            try:
                row = await _extract_place(page, item)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        if row:
            row["query"] = query
            async with rows_lock:
                rows.append(row)
        await tracker.inc(idx, "current", 1)

    await asyncio.gather(*(worker(it) for it in items))

    await tracker.update(idx, phase="enrich-pending", details_done=len(rows))
    return rows


# ---------- top-level runner ----------

async def run_async(queries: List[str]) -> List[Dict[str, Any]]:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tracker = ProgressTracker(queries)

    all_rows: List[Dict[str, Any]] = []

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.HEADLESS, args=launch_args)
        try:
            query_sem = asyncio.Semaphore(max(1, settings.QUERY_CONCURRENCY))

            async def run_one(idx: int, q: str):
                async with query_sem:
                    context = await browser.new_context(
                        viewport={"width": 1280, "height": 900},
                        user_agent=USER_AGENT,
                        locale="pt-BR",
                        timezone_id="America/Sao_Paulo",
                    )
                    try:
                        return await _scrape_query(context, q, idx, tracker)
                    except Exception as e:
                        print(f"[Q{idx}] ERRO: {e}", file=sys.stderr)
                        await tracker.update(idx, status="error", error=str(e))
                        return []
                    finally:
                        try:
                            await context.close()
                        except Exception:
                            pass

            results = await asyncio.gather(
                *(run_one(i, q) for i, q in enumerate(queries))
            )
            for r in results:
                all_rows.extend(r)
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    websites = sum(1 for r in all_rows if r.get("website"))
    print(f"[*] Enriquecendo {websites} sites em paralelo (concorrência={settings.ENRICH_CONCURRENCY})")
    await tracker.set_global(phase="enriching", to_enrich=websites)

    enrich_done = {"n": 0}

    def _on_enrich(done, total):
        enrich_done["n"] = done
        # broadcast através de um sync write — não usamos lock async aqui para não travar httpx
        st = tracker.state
        st["enrich_done"] = done
        st["enrich_total"] = total
        write_progress(settings.PROGRESS_FILE, st)

    if websites:
        await enrich_many_async(
            all_rows,
            concurrency=settings.ENRICH_CONCURRENCY,
            on_progress=_on_enrich,
        )

    for idx in range(len(queries)):
        await tracker.update(idx, status="completed", phase="done")
    await tracker.set_global(phase="completed", finished_at=time.time())

    _save_outputs(all_rows)
    return all_rows


# ---------- output ----------

HEADERS = [
    "query",
    "name",
    "phone",
    "emails",
    "website",
    "website_final",
    "address",
    "rating",
    "reviews_count",
    "category",
    "hours",
    "top_reviews",
    "lat",
    "lng",
    "gmaps_url",
]

REVIEW_SEPARATOR = " ||| "


def _csv_value(key: str, v):
    """Serializa valores complexos para CSV (dict/list -> JSON; top_reviews -> ' ||| ')."""
    if v is None:
        return ""
    if key == "top_reviews":
        if isinstance(v, list):
            return REVIEW_SEPARATOR.join(str(x).replace("\n", " ").strip() for x in v if x)
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _save_outputs(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        for k in HEADERS:
            r.setdefault(k, "")

    # JSON mantém os tipos nativos (dict de hours, listas, etc.)
    with open(settings.OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    # CSV serializa estruturas para string
    csv_rows = [{k: _csv_value(k, r.get(k, "")) for k in HEADERS} for r in rows]
    with open(settings.OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"[*] {len(rows)} registros salvos em {settings.OUTPUT_CSV}")


# ---------- legacy class wrapper ----------

class MapsScraper:
    """Mantido por compatibilidade. Use run_async / main() direto para multi-busca."""

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def run(self):
        queries = _resolve_queries()
        self.rows = asyncio.run(run_async(queries))


def _resolve_queries() -> List[str]:
    if settings.QUERIES:
        return settings.QUERIES
    if settings.SEARCH_URL:
        return [settings.SEARCH_URL]
    raise SystemExit(
        "Nenhuma busca configurada. Defina SCRAPER_QUERIES (separado por '||') "
        "ou SCRAPER_SEARCH_URL."
    )


def main() -> None:
    queries = _resolve_queries()
    print(f"[*] {len(queries)} busca(s) | "
          f"max_places={settings.MAX_PLACES} | "
          f"detail_conc={settings.DETAIL_CONCURRENCY} | "
          f"query_conc={settings.QUERY_CONCURRENCY}")
    asyncio.run(run_async(queries))


if __name__ == "__main__":
    main()

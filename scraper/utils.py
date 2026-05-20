from __future__ import annotations
import json
import re
import time
import random
import urllib.parse
from pathlib import Path
from typing import Optional

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9?\d{4})[-.\s]?\d{4}")

# Endereços que aparecem em scripts ou em conteúdo binário e poluem os e-mails.
_EMAIL_BLACKLIST = (
    "wixpress.com", "sentry.io", "googleapis.com", "schema.org",
    "example.com", "wordpress.org", "gravatar.com",
)


def rsleep(base: float = 1.0, spread: float = 0.6) -> None:
    if base <= 0 and spread <= 0:
        return
    time.sleep(max(0.0, base) + random.random() * max(0.0, spread))


def extract_emails(text: str):
    found = set()
    for m in EMAIL_RE.finditer(text or ""):
        e = m.group(0).lower()
        if any(b in e for b in _EMAIL_BLACKLIST):
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")):
            continue
        found.add(e)
    return sorted(found)


def extract_phones(text: str):
    return sorted({m.group(0) for m in PHONE_RE.finditer(text or "")})


def clean(text: str) -> str:
    return " ".join((text or "").split())


def parse_lat_lng_from_url(url: str):
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url or "")
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def extract_coords_from_url(url: str):
    """
    Extrai (lat, lng) de uma URL do Google Maps.
    Prioriza o padrão `!3d<lat>!4d<lng>` (presente nas URLs de /place/),
    depois o `@<lat>,<lng>` que aparece em URLs de busca.
    Retorna (None, None) se nada bater.
    """
    if not url:
        return None, None
    m = re.search(r"!3d(-?\d+\.?\d*)!4d(-?\d+\.?\d*)", url)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    m2 = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m2:
        try:
            return float(m2.group(1)), float(m2.group(2))
        except ValueError:
            pass
    return None, None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:80] if s else "place"


def query_to_search_url(query: str, center: str) -> str:
    """Converte termo de busca em URL do Google Maps. Aceita URL pronta também."""
    q = (query or "").strip()
    if q.lower().startswith("http"):
        return q
    encoded = urllib.parse.quote_plus(q)
    return (
        f"https://www.google.com/maps/search/{encoded}/{center}"
        f"?entry=ttu&hl=pt-BR"
    )


def write_progress(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        tmp.replace(path)
    except Exception:
        pass


def read_progress(path: Path) -> Optional[dict]:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None

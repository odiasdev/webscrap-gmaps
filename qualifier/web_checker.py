from __future__ import annotations
import re
from typing import Optional, TypedDict
from urllib.parse import urlparse

import httpx


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
}

# Domínios que indicam que o "site" é só uma vitrine/redirect social.
_SOCIAL_DOMAINS = (
    "instagram.com", "facebook.com", "fb.com", "wa.me",
    "api.whatsapp.com", "linktr.ee", "linklist.bio", "linkr.bio",
    "linktree.com", "beacons.ai", "bio.link",
)

# Palavras-chave em PT/EN que indicam sistema de reserva/agenda no site.
_BOOKING_KEYWORDS = (
    "reservar", "reserva online", "reservas online", "agendar",
    "agendamento", "horários disponíveis", "horarios disponiveis",
    "calendário", "calendario", "book now", "book online", "booking",
    "schedule",
)

# Concorrentes conhecidos (substring no HTML ou no host).
_COMPETITORS = {
    "playon": ("playon", "playon.com"),
    "reservaquadra": ("reservaquadra", "reservaquadra.com"),
    "booksy": ("booksy", "booksy.com"),
    "agendor": ("agendor",),
    "eu rachei": ("eurachei", "eu rachei"),
    "matchmania": ("matchmania",),
    "fut7pro": ("fut7pro",),
}


class WebInfo(TypedDict):
    has_site: bool
    final_url: Optional[str]
    is_social_only: bool
    has_booking_system: bool
    booking_keywords_found: list[str]
    competitor_detected: Optional[str]
    error: Optional[str]


def _empty_result(error: Optional[str] = None) -> WebInfo:
    return {
        "has_site": False,
        "final_url": None,
        "is_social_only": False,
        "has_booking_system": False,
        "booking_keywords_found": [],
        "competitor_detected": None,
        "error": error,
    }


def _is_social_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) or host.endswith(d) for d in _SOCIAL_DOMAINS)


def _strip_html(html: str) -> str:
    """Remove tags HTML grosseiramente p/ fazer match de palavras visíveis."""
    no_tag = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    no_tag = re.sub(r"<style[^>]*>.*?</style>", " ", no_tag, flags=re.S | re.I)
    no_tag = re.sub(r"<[^>]+>", " ", no_tag)
    return no_tag.lower()


def check_website(url: Optional[str], timeout: int = 10) -> WebInfo:
    """
    Faz GET no site da arena e classifica:
      - se tem site real (vs. só redirect social)
      - se há indícios de sistema de reserva
      - se há concorrente embarcado
    Não levanta exceção: erros de rede/SSL/HTTP viram error em texto.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return _empty_result()

    target = url.strip()
    if not target.lower().startswith(("http://", "https://")):
        target = "http://" + target

    try:
        with httpx.Client(
            headers=_HEADERS,
            timeout=httpx.Timeout(connect=min(timeout, 6), read=timeout, write=timeout, pool=timeout),
            follow_redirects=True,
            verify=False,
        ) as client:
            r = client.get(target)
    except httpx.TimeoutException:
        return {**_empty_result("timeout"), "has_site": True, "final_url": target}
    except httpx.SSLError as e:
        return {**_empty_result(f"ssl: {e!s}"), "has_site": True, "final_url": target}
    except httpx.HTTPError as e:
        return {**_empty_result(f"http: {e!s}"), "has_site": True, "final_url": target}
    except Exception as e:
        return {**_empty_result(f"erro: {e!s}"), "has_site": True, "final_url": target}

    final_url = str(r.url)
    final_host = (urlparse(final_url).hostname or "").lower()

    if r.status_code >= 400:
        return {
            **_empty_result(f"status {r.status_code}"),
            "has_site": True,
            "final_url": final_url,
        }

    is_social = _is_social_host(final_host)

    ctype = (r.headers.get("content-type") or "").lower()
    body_lower = ""
    if "html" in ctype or "text" in ctype:
        body_lower = _strip_html(r.text or "")[:500_000]

    keywords_found = sorted({kw for kw in _BOOKING_KEYWORDS if kw in body_lower})

    competitor: Optional[str] = None
    haystack = final_host + " " + body_lower
    for label, needles in _COMPETITORS.items():
        if any(n in haystack for n in needles):
            competitor = label
            break

    return {
        "has_site": True,
        "final_url": final_url,
        "is_social_only": is_social,
        "has_booking_system": bool(keywords_found) and not is_social,
        "booking_keywords_found": keywords_found,
        "competitor_detected": competitor,
        "error": None,
    }

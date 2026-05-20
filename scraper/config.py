from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Settings:
    QUERIES: List[str] = field(default_factory=list)
    SEARCH_URL: str = ""

    MAX_PLACES: int = 50
    SCROLL_STEPS: int = 30
    SLEEP_BASE: float = 0.0

    HEADLESS: bool = True

    DETAIL_CONCURRENCY: int = 6
    QUERY_CONCURRENCY: int = 3
    ENRICH_CONCURRENCY: int = 16

    EXTRACT_REVIEWS: bool = False
    MAX_REVIEWS_PER_PLACE: int = 5

    DEFAULT_CENTER: str = "@-19.9481481,-44.0771872,13z"

    DATA_DIR: Path = ROOT / "data"
    RAW_DIR: Path = ROOT / "data" / "raw_html"
    OUTPUT_CSV: Path = ROOT / "data" / "outputs.csv"
    OUTPUT_JSON: Path = ROOT / "data" / "outputs.json"
    PROGRESS_FILE: Path = ROOT / "data" / "scraper_progress.json"


def load_settings() -> Settings:
    s = Settings()

    qenv = os.getenv("SCRAPER_QUERIES", "")
    if qenv:
        s.QUERIES = [q.strip() for q in qenv.split("||") if q.strip()]

    url = os.getenv("SCRAPER_SEARCH_URL", "").strip()
    if url:
        s.SEARCH_URL = url

    s.MAX_PLACES = _env_int("SCRAPER_MAX_PLACES", s.MAX_PLACES)
    s.SCROLL_STEPS = _env_int("SCRAPER_SCROLL_STEPS", s.SCROLL_STEPS)
    s.SLEEP_BASE = _env_float("SCRAPER_SLEEP_BASE", s.SLEEP_BASE)
    s.HEADLESS = _env_bool("SCRAPER_HEADLESS", s.HEADLESS)
    s.DETAIL_CONCURRENCY = _env_int("SCRAPER_DETAIL_CONCURRENCY", s.DETAIL_CONCURRENCY)
    s.QUERY_CONCURRENCY = _env_int("SCRAPER_QUERY_CONCURRENCY", s.QUERY_CONCURRENCY)
    s.ENRICH_CONCURRENCY = _env_int("SCRAPER_ENRICH_CONCURRENCY", s.ENRICH_CONCURRENCY)

    s.EXTRACT_REVIEWS = _env_bool("SCRAPER_EXTRACT_REVIEWS", s.EXTRACT_REVIEWS)
    s.MAX_REVIEWS_PER_PLACE = _env_int(
        "SCRAPER_MAX_REVIEWS_PER_PLACE", s.MAX_REVIEWS_PER_PLACE
    )

    center = os.getenv("SCRAPER_CENTER", "").strip()
    if center:
        s.DEFAULT_CENTER = center

    return s


settings = load_settings()

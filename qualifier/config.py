from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Carrega .env da raiz do projeto antes de instanciar settings.
load_dotenv(ROOT / ".env")


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


def _env_optional_int(name: str) -> Optional[int]:
    v = os.getenv(name)
    if v in (None, ""):
        return None
    try:
        return int(v)
    except ValueError:
        return None


@dataclass
class QualifierSettings:
    """Settings do módulo qualifier. Instancie via load_settings()."""

    ANTHROPIC_API_KEY: str
    MODEL: str = "claude-sonnet-4-6"
    INPUT_PATH: Path = ROOT / "data" / "outputs.json"
    OUTPUT_PATH: Path = ROOT / "data" / "qualified_leads.json"
    CHECKPOINT_PATH: Path = ROOT / "data" / "qualified_leads.checkpoint.json"

    MIN_RATING_FOR_DEEP_ANALYSIS: float = 4.0
    MIN_REVIEWS_FOR_DEEP_ANALYSIS: int = 20

    WEB_CHECK_TIMEOUT: int = 10
    PARALLEL_WORKERS: int = 5
    CHECKPOINT_EVERY: int = 10

    # Opcional: limita nº de arenas processadas (testes / debug). None = todas.
    LIMIT: Optional[int] = None


def load_settings() -> QualifierSettings:
    """Lê env vars e devolve QualifierSettings. Lança se faltar a API key."""
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ausente. Crie um arquivo .env na raiz do projeto "
            "(use .env.example como modelo) e preencha sua chave."
        )

    model = os.getenv("QUALIFIER_MODEL", "").strip() or "claude-sonnet-4-6"

    input_path = Path(os.getenv("QUALIFIER_INPUT", "")) if os.getenv("QUALIFIER_INPUT") else (ROOT / "data" / "outputs.json")
    output_path = Path(os.getenv("QUALIFIER_OUTPUT", "")) if os.getenv("QUALIFIER_OUTPUT") else (ROOT / "data" / "qualified_leads.json")

    return QualifierSettings(
        ANTHROPIC_API_KEY=api_key,
        MODEL=model,
        INPUT_PATH=input_path,
        OUTPUT_PATH=output_path,
        CHECKPOINT_PATH=output_path.with_suffix(".checkpoint.json"),
        MIN_RATING_FOR_DEEP_ANALYSIS=_env_float("QUALIFIER_MIN_RATING", 4.0),
        MIN_REVIEWS_FOR_DEEP_ANALYSIS=_env_int("QUALIFIER_MIN_REVIEWS", 20),
        WEB_CHECK_TIMEOUT=_env_int("QUALIFIER_WEB_TIMEOUT", 10),
        PARALLEL_WORKERS=_env_int("QUALIFIER_PARALLEL_WORKERS", 5),
        CHECKPOINT_EVERY=_env_int("QUALIFIER_CHECKPOINT_EVERY", 10),
        LIMIT=_env_optional_int("QUALIFIER_LIMIT"),
    )

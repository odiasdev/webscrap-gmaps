from __future__ import annotations
import argparse
import json
import logging
import signal
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .claude_client import ClaudeClient
from .config import QualifierSettings, load_settings
from .pitch_generator import generate_pitch
from .qualifier import qualify_arena
from .web_checker import check_website


log = logging.getLogger("qualifier")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ----------------------------- pipeline -----------------------------

def _load_arenas(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Input não encontrado: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Esperava lista de arenas em {path}")
    return data


def _filter_arenas(arenas: list[dict]) -> list[dict]:
    """Remove duplicatas por gmaps_url e descarta sem nome ou sem telefone."""
    seen: set[str] = set()
    out: list[dict] = []
    for a in arenas:
        if not a.get("name") or not a.get("phone"):
            continue
        key = a.get("gmaps_url") or f"{a.get('name')}|{a.get('phone')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _process_arena(arena: dict, settings: QualifierSettings, client: ClaudeClient) -> dict:
    """
    Processa UMA arena:
      web_check → qualify → (pitch se quente o suficiente).
    Erros não-fatais ficam em _errors; nunca relevanta exceção.
    """
    name = arena.get("name") or "?"
    enriched: dict[str, Any] = dict(arena)
    enriched["_errors"] = []

    site_url = arena.get("website_final") or arena.get("website") or ""
    try:
        web_info = check_website(site_url, timeout=settings.WEB_CHECK_TIMEOUT)
    except Exception as e:
        log.warning("[%s] web_check falhou: %s", name, e)
        web_info = {
            "has_site": bool(site_url),
            "final_url": site_url or None,
            "is_social_only": False,
            "has_booking_system": False,
            "booking_keywords_found": [],
            "competitor_detected": None,
            "error": str(e),
        }
        enriched["_errors"].append(f"web_check: {e!s}")
    enriched["web_info"] = web_info

    try:
        qualification = qualify_arena(arena, web_info, client)
    except Exception as e:
        log.warning("[%s] qualify falhou: %s", name, e)
        qualification = {
            "score": 0, "tier": "frio", "_error": str(e),
        }
        enriched["_errors"].append(f"qualify: {e!s}")
    enriched["qualification"] = qualification

    tier = (qualification.get("tier") or "").lower()
    if tier in ("morno", "quente", "muito_quente"):
        try:
            pitch = generate_pitch(arena, qualification, client)
        except Exception as e:
            log.warning("[%s] pitch falhou: %s", name, e)
            pitch = {"mensagem_completa": "", "_error": str(e)}
            enriched["_errors"].append(f"pitch: {e!s}")
        enriched["pitch"] = pitch
    else:
        enriched["pitch"] = None  # frio: economia de tokens

    log.info(
        "[OK] %s | score=%s tier=%s pitch=%s",
        name[:40],
        qualification.get("score"),
        tier,
        "sim" if enriched.get("pitch") else "—",
    )
    return enriched


# ----------------------------- IO / checkpoint -----------------------------

def _save_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ----------------------------- summary -----------------------------

def _print_summary(results: list[dict], client: ClaudeClient) -> None:
    n = len(results)
    tier_counts = Counter((r.get("qualification") or {}).get("tier") or "?" for r in results)

    sorted_results = sorted(
        results,
        key=lambda r: (r.get("qualification") or {}).get("score") or 0,
        reverse=True,
    )

    log.info("==================== SUMMARY ====================")
    log.info("Arenas processadas: %d", n)
    log.info("Distribuição por tier:")
    for tier, c in tier_counts.most_common():
        log.info("  - %s: %d", tier, c)

    log.info("Top 5 leads:")
    for r in sorted_results[:5]:
        q = r.get("qualification") or {}
        p = r.get("pitch") or {}
        log.info(
            "  %3d  %s  [%s/%s]  dor: %s",
            q.get("score") or 0,
            (r.get("name") or "")[:40],
            q.get("tier"),
            q.get("tamanho_estimado"),
            (q.get("dor_principal") or "")[:80],
        )
        if p and p.get("mensagem_completa"):
            log.info("       pitch: %s", p["mensagem_completa"].replace("\n", " ⏎ ")[:160])

    usage = client.usage_summary()
    log.info(
        "Tokens — calls=%d input=%d output=%d cache_read=%d cache_write=%d",
        usage["calls"], usage["input_tokens"], usage["output_tokens"],
        usage["cache_read_tokens"], usage["cache_creation_tokens"],
    )
    log.info("Custo estimado: USD $%.4f", usage["estimated_usd"])
    log.info("=================================================")


# ----------------------------- main -----------------------------

class _GracefulExit:
    """Captura Ctrl+C e expõe flag p/ o loop salvar checkpoint e parar."""

    def __init__(self) -> None:
        self.requested = False
        signal.signal(signal.SIGINT, self._handler)
        try:
            signal.signal(signal.SIGTERM, self._handler)
        except (AttributeError, ValueError):
            pass

    def _handler(self, signum, frame) -> None:
        if self.requested:
            log.warning("Sinal repetido — abortando duro.")
            sys.exit(1)
        self.requested = True
        log.warning("Sinal recebido. Vou salvar o que tem e parar.")


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    parser = argparse.ArgumentParser(description="Qualifier de leads de arenas")
    parser.add_argument("--limit", type=int, default=None, help="processa só N arenas (debug/testes)")
    parser.add_argument("--input", type=str, default=None, help="caminho do outputs.json")
    parser.add_argument("--output", type=str, default=None, help="caminho do qualified_leads.json")
    parser.add_argument("--workers", type=int, default=None, help="paralelismo (default: settings)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except RuntimeError as e:
        log.error("Configuração inválida: %s", e)
        return 2

    if args.input:
        settings.INPUT_PATH = Path(args.input)
    if args.output:
        settings.OUTPUT_PATH = Path(args.output)
        settings.CHECKPOINT_PATH = settings.OUTPUT_PATH.with_suffix(".checkpoint.json")
    if args.limit is not None:
        settings.LIMIT = args.limit
    if args.workers is not None:
        settings.PARALLEL_WORKERS = args.workers

    log.info("Carregando %s", settings.INPUT_PATH)
    arenas = _load_arenas(settings.INPUT_PATH)
    log.info("Lidas %d arenas brutas", len(arenas))

    arenas = _filter_arenas(arenas)
    log.info("%d arenas válidas após filtro (com nome+telefone, dedup gmaps_url)", len(arenas))

    if settings.LIMIT is not None:
        arenas = arenas[: settings.LIMIT]
        log.info("LIMIT aplicado: processando %d arenas", len(arenas))

    if not arenas:
        log.warning("Nada pra processar. Saindo.")
        return 0

    client = ClaudeClient(api_key=settings.ANTHROPIC_API_KEY, model=settings.MODEL)
    log.info("Modelo: %s | workers=%d | checkpoint a cada %d",
             settings.MODEL, settings.PARALLEL_WORKERS, settings.CHECKPOINT_EVERY)

    results: list[dict] = []
    results_lock = threading.Lock()
    graceful = _GracefulExit()

    def submit_all(executor: ThreadPoolExecutor):
        return {executor.submit(_process_arena, a, settings, client): a for a in arenas}

    try:
        with ThreadPoolExecutor(max_workers=settings.PARALLEL_WORKERS) as executor:
            futures = submit_all(executor)
            for fut in as_completed(futures):
                if graceful.requested:
                    break
                try:
                    enriched = fut.result()
                except Exception as e:
                    arena = futures[fut]
                    log.error("[%s] erro inesperado: %s", arena.get("name"), e)
                    continue
                with results_lock:
                    results.append(enriched)
                    if len(results) % settings.CHECKPOINT_EVERY == 0:
                        _save_json(settings.CHECKPOINT_PATH, _sorted_by_score(results))
                        log.info("checkpoint: %d arenas → %s", len(results), settings.CHECKPOINT_PATH)
    except KeyboardInterrupt:
        graceful.requested = True

    final = _sorted_by_score(results)
    _save_json(settings.OUTPUT_PATH, final)
    log.info("Salvo: %s (%d arenas)", settings.OUTPUT_PATH, len(final))

    if settings.CHECKPOINT_PATH.exists():
        try:
            settings.CHECKPOINT_PATH.unlink()
        except Exception:
            pass

    _print_summary(final, client)
    return 0


def _sorted_by_score(results: list[dict]) -> list[dict]:
    return sorted(
        results,
        key=lambda r: (r.get("qualification") or {}).get("score") or 0,
        reverse=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())

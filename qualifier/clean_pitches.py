"""
Limpeza local dos pitches gerados pelo qualifier.

Não chama API. Lê data/qualified_leads.json, aplica regras de remoção,
flag de revisão manual, normalização e salva em
data/qualified_leads_cleaned.json + data/leads_para_atacar.csv.
"""
from __future__ import annotations
import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "qualified_leads.json"
OUTPUT_PATH = ROOT / "data" / "qualified_leads_cleaned.json"
CSV_PATH = ROOT / "data" / "leads_para_atacar.csv"

log = logging.getLogger("clean_pitches")


# ----------------------------- regex bank -----------------------------

# Padrões de remoção: cada item é (regex_compilada, label_p_relatório)
_REMOVAL_PATTERNS_GREETING = [
    (re.compile(r"tudo\s+certo\s*[?!]?", re.IGNORECASE), "tudo_certo"),
    (re.compile(r"tudo\s+bem\s*[?!]?", re.IGNORECASE), "tudo_bem"),
    (re.compile(r"como\s+vai\s*[?!]?", re.IGNORECASE), "como_vai"),
    (re.compile(r"espero\s+que\s+esteja\s+bem\.?", re.IGNORECASE), "espero_bem"),
]

_REMOVAL_PATTERNS_BAJULACAO = [
    (
        re.compile(
            # conector opcional "é/são/sendo/é a" antes da bajulação
            r"(?:\s+(?:é|e|são|sao|sendo))?"
            # substantivo opcional intermediário: "uma das ARENAS mais bem avaliadas"
            r"\s+uma\s+das\s+(?:\w+\s+)?"
            r"(?:mais\s+bem\s+|mais\s+|melhores\s+)"
            r"(?:avaliadas?|movimentadas?|conhecidas?)"
            r"(?:\s+(?:de|do|da|em)\s+[^—.,!?]+)?",
            re.IGNORECASE,
        ),
        "bajulacao_estrutural",
    ),
    (
        re.compile(r"\s+uma\s+das\s+mais\s+bem\s+avaliadas", re.IGNORECASE),
        "mais_bem_avaliadas",
    ),
]

_EMOJI_MULETA = "🤝"

# Detecção de menção a nota baixa (rating < 4.0).
# Combos comuns: "nota 3.5", "avaliação 2.8", "estrelas 3.4 no Google" etc.
# (?<![\d.,]) impede pegar dígito que seja parte de "4.3" ou "5,0".
_LOW_RATING_NEAR_WORD = re.compile(
    r"(?:nota|avalia[cç][aã]o|estrelas?|rating)\s+(?:de\s+)?(?<![\d.,])(\d(?:[.,]\d+)?)(?!\d)",
    re.IGNORECASE,
)
# Backup: número X.Y acompanhado de "no Google", "/ 5", "estrelas", "de 5".
# Mesma proteção contra captura parcial de decimais.
_LOW_RATING_TRAILING = re.compile(
    r"(?<![\d.,])(\d(?:[.,]\d+)?)(?!\d)\s*(?:no\s+google|/\s*5|estrelas?|de\s+5)",
    re.IGNORECASE,
)


# ----------------------------- helpers -----------------------------

def _apply_removals(text: str, patterns) -> tuple[str, list[str]]:
    """Aplica lista de (regex, label) em sequência. Retorna texto + labels que removeram algo."""
    hits: list[str] = []
    out = text
    for pattern, label in patterns:
        new, n = pattern.subn("", out)
        if n > 0:
            hits.append(label)
            out = new
    return out, hits


def _strip_emoji_muletinha(text: str) -> tuple[str, bool]:
    if _EMOJI_MULETA in text:
        return text.replace(_EMOJI_MULETA, ""), True
    return text, False


def _detect_low_rating(text: str) -> Optional[str]:
    """
    Procura menção a rating < 4.0 no texto. Retorna razão (str) ou None.
    """
    for m in _LOW_RATING_NEAR_WORD.finditer(text):
        try:
            val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if val < 4.0:
            return f"menção a nota baixa ({m.group(0).strip()})"

    for m in _LOW_RATING_TRAILING.finditer(text):
        try:
            val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if val < 4.0:
            return f"menção a nota baixa ({m.group(0).strip()})"

    return None


_PREFIX_OK_STARTS = (
    "oi, sou",
    "oi, aqui é o",
    "oi, aqui e o",
    "sou dias",
    "sou o dias",
)


def _ensure_self_id(text: str) -> tuple[str, bool]:
    """Prefixa identificação se a mensagem não começa por uma das aberturas válidas."""
    if not text.strip():
        return text, False
    head = text.lstrip().lower()
    if any(head.startswith(s) for s in _PREFIX_OK_STARTS):
        return text, False
    prefixed = "Oi, sou Dias do Arena App. " + text.lstrip()
    return prefixed, True


def _normalize(text: str) -> str:
    """Limpa artefatos pós-remoção: pontuação órfã, espaços duplos, travessões duplicados."""
    if not text:
        return text

    # Cláusula órfã tipo "[X] é —" ou "[X] é, —": remove o "é" pendurado.
    text = re.sub(r"\b(?:é|e|são|sao)\s*[—–-]", "—", text)
    # Conector seguido só de pontuação: "é." / "é,"
    text = re.sub(r"\b(?:é|e|são|sao)\s*([.,!?;])", r"\1", text)

    # Travessão duplicado " — — " ou " —  — ".
    text = re.sub(r"\s*—\s*—\s*", " — ", text)
    # Travessão antes de pontuação final: "[...] — ." → "[...]."
    text = re.sub(r"\s*—\s*([.!?])", r"\1", text)
    # Travessão no fim de tudo: "[...] —" → "[...]"
    text = re.sub(r"\s*—\s*$", "", text)
    # Garante 1 espaço de cada lado do travessão entre palavras: "X—Y" → "X — Y"
    text = re.sub(r"(\S)—(\S)", r"\1 — \2", text)
    text = re.sub(r"(\S)—(\s)", r"\1 —\2", text)
    text = re.sub(r"(\s)—(\S)", r"\1— \2", text)

    # Vírgula/ponto-e-vírgula imediatamente antes de pontuação: ",." → "."
    text = re.sub(r"\s*[,;]\s*([.!?])", r"\1", text)
    # Vírgulas duplicadas
    text = re.sub(r",(\s*,)+", ",", text)
    # Espaço antes de pontuação
    text = re.sub(r"\s+([.,;!?])", r"\1", text)
    # "Oi," sozinho seguido de letra maiúscula → ok. Mas "Oi, ." ou "Oi, ?" → "Oi."
    text = re.sub(r"^(Oi),\s*([.!?])", r"\1\2", text)
    # Múltiplos espaços
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Quebras de linha múltiplas
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Tira pontuação/espaço orfão no começo
    text = re.sub(r"^[\s,;:.!?—–-]+", "", text)

    # Trim final
    text = text.strip()

    return text


def _ensure_question_mark(text: str) -> str:
    """Garante que a mensagem termina com '?'. CTA é pergunta."""
    if not text:
        return text
    stripped = text.rstrip()
    if not stripped:
        return text
    if stripped.endswith("?"):
        return stripped
    # Se termina com . / ! / outros — substitui por ?
    while stripped and stripped[-1] in ".!,;:":
        stripped = stripped[:-1].rstrip()
    return stripped + "?"


# ----------------------------- por-pitch -----------------------------

def _clean_text_field(
    text: str,
    is_full_message: bool,
) -> tuple[str, dict]:
    """
    Aplica todas as regras a um campo. Retorna (texto_limpo, hits_dict).
    hits_dict tem: greetings_removed, bajulacao_removed, emoji_removed, prefix_added.
    """
    hits = {
        "greetings_removed": [],
        "bajulacao_removed": [],
        "emoji_removed": False,
        "prefix_added": False,
    }
    if not text:
        return text, hits

    text, g_hits = _apply_removals(text, _REMOVAL_PATTERNS_GREETING)
    hits["greetings_removed"] = g_hits

    text, b_hits = _apply_removals(text, _REMOVAL_PATTERNS_BAJULACAO)
    hits["bajulacao_removed"] = b_hits

    text, emoji_hit = _strip_emoji_muletinha(text)
    hits["emoji_removed"] = emoji_hit

    text = _normalize(text)

    if is_full_message:
        text, prefixed = _ensure_self_id(text)
        hits["prefix_added"] = prefixed
        text = _normalize(text)
        text = _ensure_question_mark(text)

    return text, hits


def _clean_pitch(pitch: dict) -> tuple[dict, dict]:
    """
    Limpa os 4 campos do pitch. Retorna (pitch_novo, stats).
    stats: greetings, bajulacao, emoji, prefix, low_rating_reason.
    """
    if not isinstance(pitch, dict):
        return pitch, {}

    out = dict(pitch)
    agg = {
        "greetings": False,
        "bajulacao": False,
        "emoji": False,
        "prefix": False,
        "low_rating_reason": None,
    }

    # Detecta nota baixa ANTES de limpar (mensagem original é fonte de verdade).
    full_msg_orig = pitch.get("mensagem_completa") or ""
    rating_reason = _detect_low_rating(full_msg_orig)
    if not rating_reason:
        # Verifica também os campos individuais
        for k in ("abertura", "dor", "proposta"):
            r = _detect_low_rating(pitch.get(k) or "")
            if r:
                rating_reason = r
                break
    agg["low_rating_reason"] = rating_reason

    for key in ("abertura", "dor", "proposta", "mensagem_completa"):
        val = pitch.get(key) or ""
        is_full = key == "mensagem_completa"
        new, hits = _clean_text_field(val, is_full_message=is_full)
        out[key] = new
        if hits["greetings_removed"]:
            agg["greetings"] = True
        if hits["bajulacao_removed"]:
            agg["bajulacao"] = True
        if hits["emoji_removed"]:
            agg["emoji"] = True
        if hits["prefix_added"]:
            agg["prefix"] = True

    if rating_reason:
        out["needs_review"] = True
        out["review_reason"] = rating_reason
    else:
        out["needs_review"] = False
        out["review_reason"] = ""

    return out, agg


# ----------------------------- main -----------------------------

def _yes_no(b: bool) -> str:
    return "sim" if b else "não"


def _write_csv(leads: list[dict], path: Path) -> None:
    """Gera CSV pronto pra disparar contato."""
    headers = [
        "Nome",
        "Telefone",
        "Score",
        "Tier",
        "Tamanho",
        "Categoria",
        "Endereço",
        "Rating",
        "Reviews",
        "Tem Site",
        "Tem Sistema Atual",
        "Sistema Atual",
        "Dor Principal",
        "Mensagem WhatsApp",
        "Precisa Revisar",
        "Razão Revisão",
        "Maps URL",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in leads:
            q = r.get("qualification") or {}
            p = r.get("pitch") or {}
            w = r.get("web_info") or {}
            writer.writerow({
                "Nome": r.get("name", ""),
                "Telefone": r.get("phone", ""),
                "Score": q.get("score", ""),
                "Tier": q.get("tier", ""),
                "Tamanho": q.get("tamanho_estimado", ""),
                "Categoria": r.get("category", ""),
                "Endereço": r.get("address", ""),
                "Rating": r.get("rating", ""),
                "Reviews": r.get("reviews_count", ""),
                "Tem Site": _yes_no(bool(w.get("has_site"))),
                "Tem Sistema Atual": _yes_no(bool(q.get("tem_sistema_atual"))),
                "Sistema Atual": q.get("sistema_atual_nome") or "",
                "Dor Principal": q.get("dor_principal", ""),
                "Mensagem WhatsApp": p.get("mensagem_completa", ""),
                "Precisa Revisar": _yes_no(bool(p.get("needs_review"))),
                "Razão Revisão": p.get("review_reason", ""),
                "Maps URL": r.get("gmaps_url", ""),
            })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not INPUT_PATH.exists():
        log.error("Input não encontrado: %s", INPUT_PATH)
        return 2

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        leads = json.load(f)

    if not isinstance(leads, list):
        log.error("Esperava lista de leads em %s", INPUT_PATH)
        return 2

    log.info("Carregados %d leads de %s", len(leads), INPUT_PATH)

    counters = {
        "total": 0,
        "with_pitch": 0,
        "greetings": 0,
        "bajulacao": 0,
        "emoji": 0,
        "prefix": 0,
        "flagged": 0,
    }
    flagged: list[dict] = []
    cleaned_leads: list[dict] = []

    for r in leads:
        counters["total"] += 1
        new = dict(r)
        pitch = r.get("pitch")
        if pitch and isinstance(pitch, dict):
            counters["with_pitch"] += 1
            new_pitch, stats = _clean_pitch(pitch)
            new["pitch"] = new_pitch
            if stats.get("greetings"):
                counters["greetings"] += 1
            if stats.get("bajulacao"):
                counters["bajulacao"] += 1
            if stats.get("emoji"):
                counters["emoji"] += 1
            if stats.get("prefix"):
                counters["prefix"] += 1
            if stats.get("low_rating_reason"):
                counters["flagged"] += 1
                flagged.append({
                    "name": r.get("name", "?"),
                    "phone": r.get("phone", ""),
                    "reason": stats["low_rating_reason"],
                })
        cleaned_leads.append(new)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned_leads, f, ensure_ascii=False, indent=2)
    log.info("JSON limpo salvo em %s", OUTPUT_PATH)

    _write_csv(cleaned_leads, CSV_PATH)
    log.info("CSV salvo em %s", CSV_PATH)

    # ----- relatório -----
    log.info("==================== RELATÓRIO ====================")
    log.info("Total de leads:                      %d", counters["total"])
    log.info("Com pitch (passíveis de limpeza):    %d", counters["with_pitch"])
    log.info("Removeram saudação (tudo bem etc):   %d", counters["greetings"])
    log.info("Removeram bajulação:                 %d", counters["bajulacao"])
    log.info("Removeram emoji muleta 🤝:           %d", counters["emoji"])
    log.info("Receberam prefixo de auto-id:        %d", counters["prefix"])
    log.info("FLAGGEDS p/ revisão manual:          %d", counters["flagged"])
    if flagged:
        log.info("--- Flaggeds (revisar antes de mandar) ---")
        for fl in flagged:
            log.info("  • %-50s %-20s — %s", fl["name"][:50], fl["phone"], fl["reason"])
    log.info("===================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

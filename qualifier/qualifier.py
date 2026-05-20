from __future__ import annotations
import json
from typing import Any

from .claude_client import ClaudeClient


SYSTEM_PROMPT = """Você é analista de vendas do Arena App, um SaaS de gestão de quadras esportivas. Avalie a arena recebida e produza análise estruturada em JSON.

Critérios de pontuação (score 0-100):
- Volume estimado: rating + reviews_count indicam tamanho da operação. Mais reviews = mais volume = mais dor de gestão.
- Dor de reserva: se reviews mencionam dificuldade de agendamento, WhatsApp não atendido, falta de organização, horário confuso → +20 pts. Cite a evidência exata.
- Sem sistema atual: se has_booking_system=false, +25 pts. Se já tem concorrente detectado, -40 pts (lead frio).
- Maturidade digital: tem site profissional? → +10. Site só redireciona pra Instagram? → -5 (mas dor pode ser maior).
- Reclamações de atendimento (não-reserva): peso neutro, apenas observar.

Retorne APENAS JSON válido, sem markdown, sem comentários:
{
  "score": int 0-100,
  "tier": "frio" | "morno" | "quente" | "muito_quente",
  "tamanho_estimado": "pequena" | "média" | "grande",
  "dor_principal": str (1 frase, citando evidência das reviews ou ausência de sistema),
  "evidencia_dor": str (citação literal de review, se aplicável, ou observação técnica),
  "tem_sistema_atual": bool,
  "sistema_atual_nome": str | null,
  "horario_pico_estimado": str (ex: "noites e fins de semana"),
  "perfil_arena": str (1 frase, ex: "arena de bairro com perfil familiar"),
  "abordagem_recomendada": str (1 frase prática)
}"""


def _build_user_payload(arena: dict, web_info: dict) -> str:
    """Monta o user message com os dados relevantes da arena já organizados."""
    payload = {
        "nome": arena.get("name") or "",
        "categoria": arena.get("category") or "",
        "endereco": arena.get("address") or "",
        "rating": arena.get("rating"),
        "reviews_count": arena.get("reviews_count"),
        "telefone": arena.get("phone") or "",
        "website": arena.get("website") or arena.get("website_final") or "",
        "horario_funcionamento": arena.get("hours") or {},
        "top_reviews": arena.get("top_reviews") or [],
        "web_info": {
            "has_site": web_info.get("has_site", False),
            "is_social_only": web_info.get("is_social_only", False),
            "has_booking_system": web_info.get("has_booking_system", False),
            "booking_keywords_found": web_info.get("booking_keywords_found", []),
            "competitor_detected": web_info.get("competitor_detected"),
            "final_url": web_info.get("final_url"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def qualify_arena(arena: dict, web_info: dict, client: ClaudeClient) -> dict:
    """
    Chama Claude p/ pontuar e analisar a arena. Sempre devolve um dict no
    schema esperado, mesmo em falha (preenchendo defaults seguros).
    """
    user = _build_user_payload(arena, web_info)
    try:
        result = client.complete(SYSTEM_PROMPT, user, json_mode=True)
    except Exception as e:
        return _fallback_qualification(error=f"erro_chamada: {e!s}")

    if not isinstance(result, dict) or not result:
        return _fallback_qualification(error="resposta_invalida")

    return _normalize_qualification(result)


_VALID_TIERS = ("frio", "morno", "quente", "muito_quente")
_VALID_SIZES = ("pequena", "média", "media", "grande")


def _normalize_qualification(d: dict) -> dict:
    """Garante chaves e tipos esperados, sem perder o que veio bom da LLM."""
    score_raw = d.get("score", 0)
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    tier = str(d.get("tier") or "").strip().lower()
    if tier not in _VALID_TIERS:
        # Inferência por faixa de score se a LLM não devolveu tier válido.
        if score >= 75:
            tier = "muito_quente"
        elif score >= 55:
            tier = "quente"
        elif score >= 35:
            tier = "morno"
        else:
            tier = "frio"

    tamanho = str(d.get("tamanho_estimado") or "").strip().lower()
    if tamanho == "media":
        tamanho = "média"
    if tamanho not in ("pequena", "média", "grande"):
        tamanho = "pequena"

    return {
        "score": score,
        "tier": tier,
        "tamanho_estimado": tamanho,
        "dor_principal": str(d.get("dor_principal") or "").strip(),
        "evidencia_dor": str(d.get("evidencia_dor") or "").strip(),
        "tem_sistema_atual": bool(d.get("tem_sistema_atual")),
        "sistema_atual_nome": d.get("sistema_atual_nome") or None,
        "horario_pico_estimado": str(d.get("horario_pico_estimado") or "").strip(),
        "perfil_arena": str(d.get("perfil_arena") or "").strip(),
        "abordagem_recomendada": str(d.get("abordagem_recomendada") or "").strip(),
    }


def _fallback_qualification(error: str) -> dict:
    return {
        "score": 0,
        "tier": "frio",
        "tamanho_estimado": "pequena",
        "dor_principal": "",
        "evidencia_dor": "",
        "tem_sistema_atual": False,
        "sistema_atual_nome": None,
        "horario_pico_estimado": "",
        "perfil_arena": "",
        "abordagem_recomendada": "",
        "_error": error,
    }

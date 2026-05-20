from __future__ import annotations
import json

from .claude_client import ClaudeClient


SYSTEM_PROMPT = """Você é Dias, fundador do Arena App. Escreva uma mensagem de WhatsApp pro dono desta arena.

REGRAS RÍGIDAS:
- Tom direto, brasileiro, informal-profissional. Nunca "espero que esteja bem", "tudo bem?", "como vai?".
- Máximo 4 linhas, sem floreio.
- ABRIR mencionando algo ESPECÍFICO da arena (review real, modalidade, horário) — não pode ser genérico.
- Apresentar a dor concreta identificada (1 frase).
- Pitch: software grátis + 4.99% só sobre transações no app. 3 tiers (Starter grátis, Pro R$99,90, Elite R$249,90) só se fizer sentido na conversa, não obrigatório.
- CTA: "posso passar aí terça ou quinta?"
- Sem emoji exagerado. No máximo 1 emoji se couber natural.
- Não inventar dados. Se não tem evidência forte, abertura pode ser baseada em algo factual neutro (rating, modalidade, bairro).

Retorne APENAS JSON, sem markdown:
{
  "abertura": str (1 linha, gancho específico),
  "dor": str (1 linha, problema concreto),
  "proposta": str (1 linha, oferta + CTA),
  "mensagem_completa": str (texto final unido, pronto pra disparar no WhatsApp),
  "personalizacao_score": int 0-10 (quão específica ficou)
}"""


def _build_user_payload(arena: dict, qualification: dict) -> str:
    payload = {
        "arena": {
            "nome": arena.get("name") or "",
            "categoria": arena.get("category") or "",
            "endereco": arena.get("address") or "",
            "rating": arena.get("rating"),
            "reviews_count": arena.get("reviews_count"),
            "horario_funcionamento": arena.get("hours") or {},
            "top_reviews": arena.get("top_reviews") or [],
        },
        "qualificacao": {
            "score": qualification.get("score"),
            "tier": qualification.get("tier"),
            "tamanho_estimado": qualification.get("tamanho_estimado"),
            "dor_principal": qualification.get("dor_principal"),
            "evidencia_dor": qualification.get("evidencia_dor"),
            "perfil_arena": qualification.get("perfil_arena"),
            "horario_pico_estimado": qualification.get("horario_pico_estimado"),
            "abordagem_recomendada": qualification.get("abordagem_recomendada"),
            "tem_sistema_atual": qualification.get("tem_sistema_atual"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def generate_pitch(arena: dict, qualification: dict, client: ClaudeClient) -> dict:
    """
    Gera o JSON de pitch (abertura/dor/proposta/mensagem_completa/personalizacao_score).
    Em falha, retorna dict marcado com _error mas com chaves esperadas.
    """
    user = _build_user_payload(arena, qualification)
    try:
        result = client.complete(SYSTEM_PROMPT, user, json_mode=True)
    except Exception as e:
        return _fallback_pitch(error=f"erro_chamada: {e!s}")

    if not isinstance(result, dict) or not result:
        return _fallback_pitch(error="resposta_invalida")

    return _normalize_pitch(result)


def _normalize_pitch(d: dict) -> dict:
    abertura = str(d.get("abertura") or "").strip()
    dor = str(d.get("dor") or "").strip()
    proposta = str(d.get("proposta") or "").strip()
    mensagem = str(d.get("mensagem_completa") or "").strip()
    if not mensagem:
        # Reconstrói se a LLM não devolveu o texto unificado.
        mensagem = "\n".join(p for p in (abertura, dor, proposta) if p)

    score_raw = d.get("personalizacao_score", 0)
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))

    return {
        "abertura": abertura,
        "dor": dor,
        "proposta": proposta,
        "mensagem_completa": mensagem,
        "personalizacao_score": score,
    }


def _fallback_pitch(error: str) -> dict:
    return {
        "abertura": "",
        "dor": "",
        "proposta": "",
        "mensagem_completa": "",
        "personalizacao_score": 0,
        "_error": error,
    }

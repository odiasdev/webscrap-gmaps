from __future__ import annotations
import json
import logging
import re
from typing import Any, Optional, Union

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


log = logging.getLogger(__name__)


class ClaudeClient:
    """
    Encapsula chamadas à API Anthropic com:
    - prompt caching no system message (5 min TTL)
    - retry exponencial em erros transitórios
    - parse defensivo de JSON
    - acumulador de uso de tokens p/ estimar custo no fim do batch
    """

    # Sonnet 4.6 pricing (USD por 1M tokens)
    _PRICE_INPUT_PER_M = 3.0
    _PRICE_INPUT_CACHED_PER_M = 0.30
    _PRICE_INPUT_CACHE_WRITE_PER_M = 3.75
    _PRICE_OUTPUT_PER_M = 15.0

    def __init__(self, api_key: str, model: str, max_tokens: int = 2000):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_calls = 0

    @retry(
        retry=retry_if_exception_type(
            (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
                anthropic.APITimeoutError,
            )
        ),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _create_message(self, system: str, user: str):
        # System como bloco com cache_control p/ economia entre arenas.
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )

    def complete(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
    ) -> Union[dict, str]:
        """
        Faz uma chamada single-turn. Em json_mode tenta parsear a resposta como JSON;
        se a resposta vier embrulhada em markdown (``` ... ```) ou texto, tenta
        extrair o primeiro objeto JSON do texto.
        """
        resp = self._create_message(system=system, user=user)

        # Acumula uso
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.total_input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.total_output_tokens += getattr(usage, "output_tokens", 0) or 0
            self.total_cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
            self.total_cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.total_calls += 1

        text_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        raw = "".join(text_parts).strip()

        if not json_mode:
            return raw

        return _parse_json_loose(raw)

    def estimated_cost_usd(self) -> float:
        """Estima o custo desta sessão em USD com a tabela de Sonnet 4.6."""
        non_cached_input = self.total_input_tokens
        cost = (
            non_cached_input / 1_000_000 * self._PRICE_INPUT_PER_M
            + self.total_output_tokens / 1_000_000 * self._PRICE_OUTPUT_PER_M
            + self.total_cache_read_tokens / 1_000_000 * self._PRICE_INPUT_CACHED_PER_M
            + self.total_cache_creation_tokens / 1_000_000 * self._PRICE_INPUT_CACHE_WRITE_PER_M
        )
        return round(cost, 4)

    def usage_summary(self) -> dict:
        return {
            "calls": self.total_calls,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_creation_tokens": self.total_cache_creation_tokens,
            "estimated_usd": self.estimated_cost_usd(),
        }


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def _parse_json_loose(text: str) -> dict:
    """Tenta parsear JSON de forma defensiva: direto, depois fenced, depois primeira chave."""
    if not text:
        return {}

    # 1) parse direto
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) fenced ```json ... ```
    m = _FENCED_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3) primeiro `{` até `}` correspondente (balanceado)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    log.warning("falha no parse JSON; retornando dict vazio. Trecho: %r", text[:200])
    return {}

# Google Maps Scraper + Qualifier (Arena App)

Pipeline de prospecção em duas etapas:

1. **Scraper** — extrai negócios do Google Maps (Playwright + async) e enriquece os sites encontrados (e‑mails, telefones extras).
2. **Qualifier** — pontua cada lead com Claude (Anthropic API), gera mensagem de WhatsApp e exporta planilha pronta pra disparo.

> **Aviso**: respeite os **Termos de Serviço** do Google. Os seletores mudam com frequência — para uso em escala/produção considere a **Google Places API**.

---

## O que cada etapa faz

### Scraper (`scraper/`)
- Recebe **uma ou várias buscas** (termos ou URLs do Maps prontas).
- Roda em **paralelo**: múltiplas buscas + múltiplas fichas por busca (Playwright async).
- Para cada lugar captura: `name`, `phone`, `website`, `address`, `rating`, `reviews_count`, `category`, `hours` (seg→dom), `lat/lng`, `gmaps_url` — opcionalmente até N **top reviews**.
- **Enriquecimento de site**: depois das fichas, baixa o HTML do site (e da página "contato/fale/atendimento" quando existir) e extrai `emails` adicionais e telefones extras.
- Bloqueia `image/media/font` + trackers (`doubleclick`, `googletagmanager`, etc.) pra acelerar o crawl.
- Publica estado em `data/scraper_progress.json` (o dashboard lê em tempo real).
- Salva resultados em `data/outputs.csv` e `data/outputs.json`.

### Qualifier (`qualifier/`)
- Carrega `data/outputs.json`, filtra leads sem nome/telefone e dedup por `gmaps_url`.
- Pra cada arena, em **threads paralelas**:
  1. **`web_checker`** — GET no site e classifica: tem site real? só vitrine social? tem palavras de sistema de reserva (`reservar`, `agendar`, `book now`...)? algum concorrente conhecido embutido?
  2. **`qualifier`** — Claude pontua 0–100, define tier (`frio`/`morno`/`quente`/`muito_quente`), tamanho estimado, dor principal, evidência, abordagem recomendada.
  3. **`pitch_generator`** — só pros tiers `morno`+: Claude escreve a mensagem de WhatsApp (abertura específica, dor, proposta + CTA "terça ou quinta?").
- Usa **prompt caching** (cache_control efêmero no system) — barateia bastante em batch.
- Salva checkpoint a cada N arenas (`qualified_leads.checkpoint.json`) e Ctrl+C grava o parcial.
- Saída: `data/qualified_leads.json` + sumário com tokens/custo estimado.

### Pós-processamento
- **`qualifier/clean_pitches.py`** — limpeza local (sem API) dos pitches: remove saudações (`tudo bem?`, `como vai?`), bajulação genérica (`uma das mais bem avaliadas...`), emoji-muleta 🤝, força auto‑ID (`Oi, sou Dias...`), garante `?` no CTA, **flagga** mensagens que citam nota < 4.0 pra revisão manual. Gera `qualified_leads_cleaned.json` e `leads_para_atacar.csv` (formato simples pra CRM).
- **`gerar_planilha_final.py`** — gera CSV final ordenado por score com colunas de controle (status, data envio, resposta, próxima ação, notas).
- **`check_flagged.py`** — lista os pitches marcados pra revisão.
- **`vertop5.py`** — imprime as 5 mensagens de maior score (sanity check antes de enviar).
- **`fixorion.py`** — exemplo de patch manual de pitch (substitui mensagem e desfaz flag).

### Dashboard (`app/streamlit_app.py`)
- UI pra **disparar o scraper** (textarea de buscas, limite/concorrências).
- Barras de progresso por busca + global, lendo `scraper_progress.json` em tempo real.
- Filtros por busca, texto, presença de e‑mail/telefone/site.
- Tabela final, mapa por `lat/lng`, download do CSV filtrado.

---

## Estrutura

```
.
├── app/streamlit_app.py            # Dashboard (UI + lançador do scraper)
├── scraper/
│   ├── config.py                   # Settings via env (SCRAPER_*)
│   ├── maps_scraper.py             # Runner async + extração das fichas
│   ├── website_enricher.py         # httpx async: emails/contato a partir do site
│   └── utils.py                    # regex de e-mail/telefone, URL builder, progress IO
├── qualifier/
│   ├── config.py                   # Settings (QUALIFIER_* + ANTHROPIC_API_KEY)
│   ├── claude_client.py            # Anthropic + retry + prompt caching + custo
│   ├── web_checker.py              # Classifica site (social-only, booking, concorrente)
│   ├── qualifier.py                # Prompt de scoring + normalização
│   ├── pitch_generator.py          # Prompt de WhatsApp
│   ├── clean_pitches.py            # Limpeza local (sem API)
│   └── run.py                      # Pipeline + ThreadPool + checkpoint
├── check_flagged.py                # Lista pitches flaggeds
├── gerar_planilha_final.py         # CSV final pra atacar
├── vertop5.py                      # Top 5 mensagens
├── fixorion.py                     # Patch manual (exemplo)
├── data/                           # CSVs, JSONs, progresso, logs
├── .env.example                    # ANTHROPIC_API_KEY + overrides
└── requirements.txt
```

---

## Requisitos

- Python 3.10+
- Conta na Anthropic com `ANTHROPIC_API_KEY` (só pro qualifier — o scraper roda standalone)

## Instalação

```powershell
# 1) venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # Windows PowerShell
# source .venv/bin/activate       # macOS/Linux

# 2) deps
pip install -r requirements.txt

# 3) navegadores do Playwright
python -m playwright install

# 4) configurar API key (só se for usar o qualifier)
copy .env.example .env            # depois edita .env e cola sua chave
```

---

## Como rodar

### Opção A — Dashboard (recomendado)

```powershell
streamlit run app/streamlit_app.py
```

Digite as buscas, escolha limite/concorrência, clica em **Executar**. O dashboard sobe o scraper como subprocesso e atualiza a barra de progresso conforme `scraper_progress.json` muda.

### Opção B — Scraper via CLI

Passe parâmetros por env var (não tem flags). Duas formas de definir buscas:

```powershell
# Múltiplos termos separados por '||'
$env:SCRAPER_QUERIES = "dentista contagem||pizzaria belo horizonte"
$env:SCRAPER_MAX_PLACES = "50"
python -m scraper.maps_scraper
```

ou uma URL pronta do Maps:

```powershell
$env:SCRAPER_SEARCH_URL = "https://www.google.com/maps/search/streetwear+contagem/..."
python -m scraper.maps_scraper
```

Saída: `data/outputs.csv` e `data/outputs.json`.

### Opção C — Qualifier (depois do scraper)

```powershell
python -m qualifier.run                       # processa tudo de outputs.json
python -m qualifier.run --limit 20            # só as 20 primeiras (debug)
python -m qualifier.run --workers 8           # mais paralelismo
python -m qualifier.run --input ./data/outputs.json --output ./data/qualified_leads.json
```

Depois, limpe os pitches e gere o CSV final:

```powershell
python -m qualifier.clean_pitches             # → qualified_leads_cleaned.json + leads_para_atacar.csv
python gerar_planilha_final.py                # CSV alternativo com colunas de CRM
python check_flagged.py                       # revisar pitches flaggeds (nota baixa)
python vertop5.py                             # ver as 5 melhores mensagens
```

---

## Configuração (env vars)

### Scraper (`scraper/config.py`)
| Variável                         | Default                          | O que faz                                    |
|----------------------------------|----------------------------------|----------------------------------------------|
| `SCRAPER_QUERIES`                | —                                | Lista de buscas separada por `\|\|`          |
| `SCRAPER_SEARCH_URL`             | —                                | URL pronta do Maps (alternativa)             |
| `SCRAPER_MAX_PLACES`             | `50`                             | Máx. de fichas por busca                     |
| `SCRAPER_SCROLL_STEPS`           | `30`                             | Iterações de scroll na lista                 |
| `SCRAPER_DETAIL_CONCURRENCY`     | `6`                              | Fichas abertas em paralelo                   |
| `SCRAPER_QUERY_CONCURRENCY`      | `3`                              | Buscas rodando em paralelo                   |
| `SCRAPER_ENRICH_CONCURRENCY`     | `16`                             | Sites baixados em paralelo (httpx)           |
| `SCRAPER_HEADLESS`               | `True`                           | `False` pra abrir navegador (debug)          |
| `SCRAPER_EXTRACT_REVIEWS`        | `False`                          | Captura `top_reviews` (mais lento)           |
| `SCRAPER_MAX_REVIEWS_PER_PLACE`  | `5`                              | Qtd. de reviews por ficha                    |
| `SCRAPER_CENTER`                 | `@-19.9481481,-44.0771872,13z`   | Coordenada/zoom default (BH/Contagem)        |

### Qualifier (`qualifier/config.py`)
| Variável                          | Default                                | O que faz                              |
|-----------------------------------|----------------------------------------|----------------------------------------|
| `ANTHROPIC_API_KEY`               | — (obrigatória)                        | Chave da Anthropic                     |
| `QUALIFIER_MODEL`                 | `claude-sonnet-4-6`                    | Modelo                                 |
| `QUALIFIER_INPUT`                 | `data/outputs.json`                    | Entrada                                |
| `QUALIFIER_OUTPUT`                | `data/qualified_leads.json`            | Saída                                  |
| `QUALIFIER_PARALLEL_WORKERS`      | `5`                                    | Threads                                |
| `QUALIFIER_CHECKPOINT_EVERY`      | `10`                                   | Salva parcial a cada N                 |
| `QUALIFIER_LIMIT`                 | — (todas)                              | Limita N arenas processadas            |
| `QUALIFIER_MIN_RATING`            | `4.0`                                  | Reservado (análise futura)             |
| `QUALIFIER_MIN_REVIEWS`           | `20`                                   | Reservado (análise futura)             |
| `QUALIFIER_WEB_TIMEOUT`           | `10`                                   | Timeout (s) do `web_checker`           |

---

## Arquivos gerados (`data/`)

| Arquivo                            | Origem                          | Conteúdo                                   |
|------------------------------------|---------------------------------|--------------------------------------------|
| `outputs.csv` / `outputs.json`     | scraper                         | Leads brutos enriquecidos                  |
| `scraper_progress.json`            | scraper                         | Estado live consumido pelo dashboard       |
| `last_run.log`                     | dashboard                       | stdout/stderr do subprocess do scraper     |
| `qualified_leads.json`             | `qualifier.run`                 | Leads + `web_info` + `qualification` + `pitch` |
| `qualified_leads.checkpoint.json`  | `qualifier.run`                 | Parcial (auto-removido no fim)             |
| `qualified_leads_cleaned.json`     | `qualifier.clean_pitches`       | Mensagens limpas + flags de revisão        |
| `leads_para_atacar.csv`            | `clean_pitches` + `gerar_planilha_final` | CSV pronto pra CRM/disparo         |
| `top_leads.json`                   | scripts ad-hoc                  | Subset top-N                               |

---

## Domínio do projeto (contexto)

O qualifier está calibrado pra **arenas esportivas** (quadras de futsal/society/areia/padel etc.) — alvo do **Arena App** (SaaS de gestão de quadras). Os prompts de `qualifier.py` e `pitch_generator.py` assumem esse contexto: mencionam o pitch comercial (software grátis + 4,99% sobre transações no app), os tiers, o CTA padrão e a persona ("Dias, fundador do Arena App"). Trocando os prompts dá pra adaptar pra qualquer vertical.

## Dicas

- Se o Google travar resultados, **diminua** `SCRAPER_DETAIL_CONCURRENCY`/`SCRAPER_QUERY_CONCURRENCY` e use `SCRAPER_HEADLESS=False` pra ver o que está acontecendo.
- `data/last_run.log` tem o stdout do scraper iniciado pelo dashboard.
- O qualifier usa cache_control no system prompt — rodar tudo de uma vez é bem mais barato que partir o batch em vários `--limit`.
- Antes de disparar, **sempre** rode `python -m qualifier.clean_pitches` e revise o que está em `needs_review: true` (mensagens que citam nota < 4.0, principalmente).

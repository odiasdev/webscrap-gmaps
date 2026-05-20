# Google Maps Scraper + Dashboard (Python)

> **Aviso**: verifique e respeite os **Termos de Serviço** do site que pretende coletar. O Google pode bloquear automações ou alterar seletores com frequência. Para uso robusto/escala, considere a **Google Places API**.

## O que este projeto faz
- Abre uma **URL de busca do Google Maps** (ex.: Street wear em Contagem).
- Percorre os resultados, entra em cada ficha de loja e captura:
  - **nome**, **telefone**, **site**, **coord. (lat/lng)** e **endereço** (quando disponível).
- Se houver **site**, baixa o HTML e tenta achar **e‑mails** e **telefones** extras.
- Salva **snapshots HTML**: da página de busca, de cada ficha e dos sites.
- Exporta para **CSV/JSON**.
- Traz um **Dashboard em Streamlit** para inspecionar/filtrar/baixar.

## Requisitos
- Python 3.10+
- Windows, macOS ou Linux

## Instalação
```bash
# 1) Ambiente (opcional, mas recomendado)
python -m venv .venv
# Windows
.\.venv\Scriptsctivate
# macOS/Linux
# source .venv/bin/activate

# 2) Dependências
pip install -r requirements.txt

# 3) Playwright (baixa os navegadores)
python -m playwright install
```

> Em alguns ambientes Windows, rode o terminal **como Administrador** para o passo 3.

## Como rodar o scraper
Edite `scraper/config.py` se quiser mudar limites. Depois:
```bash
python -m scraper.maps_scraper
```

- A saída ficará em `data/outputs.csv` e `data/outputs.json`.
- Snapshots HTML em `data/raw_html/`.

## Como rodar o dashboard (Streamlit)
```bash
streamlit run app/streamlit_app.py
```
O app carrega `data/outputs.csv` automaticamente.

## Dicas de uso
- Se o Google travar resultados, reduza `MAX_PLACES`, aumente `SLEEP_BASE` e ative `HEADLESS=False` em `config.py` para depurar.
- Se um campo estiver vazio (como e‑mail), ele pode não existir na ficha. Tentar pelo website ajuda.
- Os **seletores do Google** mudam bastante; este projeto já salva o HTML para você ajustar os seletores caso algo quebre.

## Alternativa oficial (recomendado para produção)
- **Google Places API** e **Places Details API** oferecem dados estruturados e estáveis com políticas claras de uso.

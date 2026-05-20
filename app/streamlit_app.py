from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_CSV = DATA_DIR / "outputs.csv"
PROGRESS_FILE = DATA_DIR / "scraper_progress.json"
RUN_LOG = DATA_DIR / "last_run.log"


# ----------------------------- helpers -----------------------------

def normalize_phone(phone) -> str:
    if pd.isna(phone) or phone == "":
        return ""
    clean_phone = re.sub(r"[^\d]", "", str(phone))
    if len(clean_phone) == 13 and clean_phone.startswith("55"):
        if clean_phone[4] == "9":
            clean_phone = clean_phone[:4] + clean_phone[5:]
    elif len(clean_phone) == 11:
        if clean_phone[2] == "9":
            clean_phone = clean_phone[:2] + clean_phone[3:]
        clean_phone = "55" + clean_phone
    elif len(clean_phone) == 10:
        clean_phone = "55" + clean_phone
    elif len(clean_phone) < 10:
        return clean_phone
    return clean_phone


def get_progress() -> dict:
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def remove_duplicates(df: pd.DataFrame):
    if df.empty:
        return df, 0
    df = df.copy()
    df["unique_key"] = df["name"].str.lower().str.strip() + "_" + df["phone_normalized"].fillna("")
    unique = df.drop_duplicates(subset=["unique_key"], keep="first").drop(columns=["unique_key"])
    return unique, len(df) - len(unique)


def parse_queries(raw: str) -> list[str]:
    out = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def launch_scraper(queries: list[str], limit: int, detail_conc: int, query_conc: int) -> subprocess.Popen:
    """Inicia o scraper como subprocesso, passando parâmetros via env vars."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PROGRESS_FILE.exists():
        try:
            PROGRESS_FILE.unlink()
        except Exception:
            pass
    if DATA_CSV.exists():
        try:
            DATA_CSV.unlink()
        except Exception:
            pass

    env = os.environ.copy()
    env["SCRAPER_QUERIES"] = "||".join(queries)
    env["SCRAPER_MAX_PLACES"] = str(limit)
    env["SCRAPER_DETAIL_CONCURRENCY"] = str(detail_conc)
    env["SCRAPER_QUERY_CONCURRENCY"] = str(min(query_conc, len(queries)))
    env["SCRAPER_HEADLESS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log_file = open(RUN_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "scraper.maps_scraper"],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return proc


def is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and exit_code.value == STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


# ----------------------------- session state -----------------------------

ss = st.session_state
ss.setdefault("scraper_pid", None)
ss.setdefault("scraper_running", False)
ss.setdefault("last_queries", [])
ss.setdefault("last_limit", 50)


# ----------------------------- page -----------------------------

st.set_page_config(page_title="Google Maps Scraper — Dashboard", layout="wide")

c1, c2 = st.columns([4, 1])
with c1:
    st.title("🔎 B2B Lead Scraper & AI Qualifier - ODIASDEV")
    st.caption("Multi-busca paralela • async Playwright • enriquecimento concorrente")


# ----------------------------- run config -----------------------------

st.header("⚙️ Configurar Buscas")

# Status atual
progress = get_progress()
queries_state: dict = (progress or {}).get("queries", {})

still_alive = is_process_alive(ss.get("scraper_pid"))
finished_phase = progress.get("phase") == "completed" if progress else False

if ss.scraper_running and not still_alive:
    ss.scraper_running = False

with st.expander("🚀 Iniciar nova execução", expanded=not ss.scraper_running):
    colA, colB = st.columns([2, 1])

    with colA:
        raw_queries = st.text_area(
            "Buscas (uma por linha — pode misturar termos e URLs do Maps)",
            value="dentista contagem\npizzaria belo horizonte",
            height=160,
            help="Linhas iniciadas com # são ignoradas. Cada linha vira uma busca paralela.",
            disabled=ss.scraper_running,
        )

    with colB:
        limit = st.selectbox(
            "Limite por busca",
            options=[20, 30, 40, 50, 60, 80, 120, 200],
            index=3,
            disabled=ss.scraper_running,
        )
        detail_conc = st.slider(
            "Páginas paralelas (por busca)",
            min_value=1, max_value=12, value=6,
            help="Quantas fichas serão abertas em paralelo. Aumentar acelera mas pode ser bloqueado.",
            disabled=ss.scraper_running,
        )
        query_conc = st.slider(
            "Buscas em paralelo",
            min_value=1, max_value=6, value=3,
            help="Quantas buscas rodam ao mesmo tempo. Cada busca usa um navegador isolado.",
            disabled=ss.scraper_running,
        )

    queries = parse_queries(raw_queries)
    st.caption(f"{len(queries)} busca(s) configurada(s).")

    if st.button("🔍 Executar", type="primary", disabled=ss.scraper_running or not queries):
        proc = launch_scraper(queries, limit, detail_conc, query_conc)
        ss.scraper_pid = proc.pid
        ss.scraper_running = True
        ss.last_queries = queries
        ss.last_limit = limit
        st.rerun()


# ----------------------------- live progress -----------------------------

if ss.scraper_running or queries_state:
    st.subheader("📡 Progresso")

    if queries_state:
        total_done = sum(int(q.get("current", 0)) for q in queries_state.values())
        total_target = sum(int(q.get("total", 0)) for q in queries_state.values()) or 1
        global_pct = min(1.0, total_done / total_target)
        st.progress(global_pct, text=f"Global: {total_done}/{total_target} ({global_pct*100:.0f}%)")

        for idx in sorted(queries_state.keys(), key=lambda x: int(x)):
            q = queries_state[idx]
            cur = int(q.get("current", 0))
            tot = int(q.get("total", 0)) or 1
            pct = min(1.0, cur / tot)
            phase = q.get("phase", q.get("status", ""))
            label = q.get("query", "?")
            status_emoji = {
                "queued": "⏳", "loading": "🌐", "scrolling": "🖱️",
                "details": "🔎", "enrich-pending": "✉️",
                "done": "✅", "completed": "✅", "error": "❌",
            }.get(phase, "🔄")
            st.progress(
                pct,
                text=f"{status_emoji} [{idx}] {label} — {cur}/{tot} ({phase})",
            )

        if progress.get("phase") == "enriching":
            ed = int(progress.get("enrich_done", 0))
            et = int(progress.get("enrich_total", 0)) or 1
            st.progress(min(1.0, ed / et), text=f"✉️ Enriquecendo sites: {ed}/{et}")

    else:
        st.info("Aguardando o scraper iniciar...")

    if ss.scraper_running:
        time.sleep(2)
        st.rerun()
    elif progress.get("phase") == "completed":
        st.success("✅ Execução concluída.")

    if RUN_LOG.exists():
        with st.expander("📄 Log de execução"):
            try:
                txt = RUN_LOG.read_text(encoding="utf-8", errors="ignore")
                st.code(txt[-8000:] if len(txt) > 8000 else txt)
            except Exception:
                st.write("(log indisponível)")

st.markdown("---")


# ----------------------------- results -----------------------------

if not DATA_CSV.exists():
    if not ss.scraper_running:
        st.warning("📁 Nenhum dado encontrado. Execute uma busca acima.")
    st.stop()

df = pd.read_csv(DATA_CSV)
if df.empty:
    st.warning("📁 CSV está vazio.")
    st.stop()

if "phone" not in df.columns:
    df["phone"] = ""
df["phone_normalized"] = df["phone"].apply(normalize_phone)

if "query" not in df.columns:
    df["query"] = ""

df_unique, dupes = remove_duplicates(df)
if dupes:
    st.info(f"🧹 {dupes} duplicata(s) removida(s).")


# Sidebar filters
with st.sidebar:
    st.header("🔍 Filtros")
    queries_in_data = sorted(df_unique["query"].dropna().unique().tolist())
    selected_queries = st.multiselect(
        "Filtrar por busca",
        options=queries_in_data,
        default=queries_in_data,
    )
    q = st.text_input("Buscar texto (nome / site / endereço)")
    only_email = st.checkbox("Somente com e-mail")
    only_phone = st.checkbox("Somente com telefone")
    only_site = st.checkbox("Somente com site")

f = df_unique.copy()
if selected_queries:
    f = f[f["query"].isin(selected_queries)]

if q:
    ql = q.lower()
    f = f[
        f["name"].astype(str).str.lower().str.contains(ql, na=False)
        | f["website"].astype(str).str.lower().str.contains(ql, na=False)
        | f["address"].astype(str).str.lower().str.contains(ql, na=False)
    ]

if only_email:
    f = f[f["emails"].fillna("") != ""]
if only_phone:
    f = f[f["phone_normalized"].fillna("") != ""]
if only_site:
    f = f[f["website"].fillna("") != ""]


# KPIs
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total", len(df_unique))
k2.metric("Filtrado", len(f))
k3.metric("Com e-mail", (df_unique["emails"].fillna("") != "").sum())
k4.metric("Com telefone", (df_unique["phone_normalized"].fillna("") != "").sum())
k5.metric("Buscas", df_unique["query"].nunique())

st.markdown("---")
st.subheader("📊 Resultados")


def make_link(row):
    w = row.get("website", "")
    g = row.get("gmaps_url", "")
    if pd.notna(w) and str(w).strip():
        return str(w)
    if pd.notna(g) and str(g).strip():
        return str(g)
    return ""


display_df = f.copy()
display_df["link"] = display_df.apply(make_link, axis=1)

cols = ["query", "name", "link", "phone_normalized", "emails", "address"]
existing = [c for c in cols if c in display_df.columns]
final_df = display_df[existing].rename(columns={
    "query": "Busca",
    "name": "Nome",
    "link": "Link",
    "phone_normalized": "Telefone",
    "emails": "Email",
    "address": "Endereço",
}).fillna("")

st.dataframe(
    final_df,
    use_container_width=True,
    height=540,
    column_config={
        "Busca": st.column_config.TextColumn("Busca", width="small"),
        "Nome": st.column_config.TextColumn("Nome", width="medium"),
        "Link": st.column_config.LinkColumn("Link", width="medium"),
        "Telefone": st.column_config.TextColumn("Telefone", width="small"),
        "Email": st.column_config.TextColumn("Email", width="medium"),
        "Endereço": st.column_config.TextColumn("Endereço", width="large"),
    },
)


if {"lat", "lng"}.issubset(f.columns):
    coords = f[["lat", "lng", "name"]].replace("", pd.NA).dropna(subset=["lat", "lng"])
    if not coords.empty:
        st.subheader("🗺️ Mapa")
        try:
            map_df = coords.astype({"lat": float, "lng": float}).rename(
                columns={"lat": "latitude", "lng": "longitude"}
            )
            st.map(map_df)
        except Exception:
            st.info("Coordenadas insuficientes para o mapa.")


st.markdown("---")
st.download_button(
    "📥 Baixar CSV filtrado",
    data=final_df.to_csv(index=False).encode("utf-8"),
    file_name="resultados_filtrados.csv",
    mime="text/csv",
)

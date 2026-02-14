"""AI Analyst: grounded chat over transcripts, claims, and financials."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.config import settings
from backend.services.rag import AnalystChatbot, HybridRetriever, RAGIndexBuilder, get_index_status

DATA_DIR = Path(__file__).parent.parent.parent / "data"


st.set_page_config(page_title="AI Analyst - EarningsLens", layout="wide")
st.title("AI Analyst")
st.caption("Hybrid retrieval over transcripts, claims/verdicts, and financial snapshots with source citations.")


@st.cache_resource
def get_retriever() -> HybridRetriever:
    return HybridRetriever()


def _read_secret_or_env(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    if value:
        return str(value).strip()
    return str(os.getenv(name, "")).strip()


ANALYST_APP_PASSWORD = _read_secret_or_env("ANALYST_APP_PASSWORD")
if not ANALYST_APP_PASSWORD:
    ANALYST_APP_PASSWORD = str(getattr(settings, "analyst_app_password", "")).strip()
DEFAULT_OPENROUTER_KEY = _read_secret_or_env("OPENROUTER_API_KEY") or settings.openrouter_api_key


def _require_analyst_access() -> None:
    """Optional password gate for AI Analyst page."""
    if not ANALYST_APP_PASSWORD:
        return
    if (
        st.session_state.get("analyst_authenticated")
        and st.session_state.get("analyst_auth_password") == ANALYST_APP_PASSWORD
    ):
        return

    st.warning("AI Analyst access is password protected.")
    with st.form("analyst_unlock_form", clear_on_submit=False):
        password = st.text_input("Enter Analyst Password", type="password")
        unlock = st.form_submit_button("Unlock Analyst")
    if unlock:
        if password == ANALYST_APP_PASSWORD:
            st.session_state["analyst_authenticated"] = True
            st.session_state["analyst_auth_password"] = ANALYST_APP_PASSWORD
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


@st.cache_data
def load_tickers() -> list[str]:
    companies_path = DATA_DIR / "companies.json"
    if not companies_path.exists():
        return []
    try:
        with open(companies_path) as f:
            companies = json.load(f)
    except Exception:
        return []
    return sorted({
        str(company.get("ticker", "")).upper()
        for company in companies
        if company.get("ticker")
    })


def _initial_messages() -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": (
                "Ask about a company, quarter, metric, or claim quality. "
                "I will answer with citations from indexed evidence."
            ),
            "sources": [],
            "retrieval": {},
        }
    ]


def _render_chat_text(text: str) -> str:
    """Escape markdown math delimiters so money values render correctly."""
    return (text or "").replace("$", r"\$")


def _render_source_card(source: dict) -> None:
    header = (
        f"{source.get('source_id')} | {source.get('ticker') or 'N/A'} | "
        f"{source.get('period') or 'N/A'} | {source.get('source_type')}"
    )
    with st.expander(header, expanded=False):
        st.caption(source.get("title") or source.get("doc_id"))
        st.code(source.get("text") or "")
        sb = source.get("score_breakdown") or {}
        if sb:
            cols = st.columns(4)
            cols[0].metric("Dense", sb.get("dense", 0))
            cols[1].metric("Lexical", sb.get("lexical", 0))
            cols[2].metric("Entity", sb.get("entity", 0))
            cols[3].metric("Prior", sb.get("prior", 0))
        if source.get("source_path"):
            st.caption(f"Path: {source['source_path']}")


_require_analyst_access()


with st.sidebar:
    if ANALYST_APP_PASSWORD:
        st.caption("Access mode: protected")
        if st.button("Lock Analyst", use_container_width=True):
            st.session_state["analyst_authenticated"] = False
            st.session_state["analyst_auth_password"] = ""
            st.rerun()
    else:
        st.caption("Access mode: open")

    st.subheader("Model Access")
    st.caption(
        "Use default `OPENROUTER_API_KEY` from secrets/env, or provide a session key below."
    )
    st.text_input(
        "Session OpenRouter API Key",
        type="password",
        key="session_openrouter_api_key",
        help="Optional per-session key. Leave blank to use configured default key.",
    )
    session_key = str(st.session_state.get("session_openrouter_api_key", "")).strip()
    generation_enabled = bool(session_key or DEFAULT_OPENROUTER_KEY)
    if generation_enabled:
        st.caption("Generation mode: enabled")
    else:
        st.caption("Generation mode: extractive fallback only")

    st.divider()
    st.subheader("Index")
    status = get_index_status()
    st.caption(f"DB: {status.get('db_path')}")
    st.caption(f"Exists: {status.get('exists')}")
    st.caption(f"Documents: {status.get('documents', 0)}")
    st.caption(f"Chunks: {status.get('chunks', 0)}")
    if status.get("built_at"):
        st.caption(f"Built at: {status['built_at']}")

    top_k = st.slider("Top-K sources", min_value=3, max_value=15, value=8)
    ticker_options = ["All"] + load_tickers()
    selected_ticker = st.selectbox("Ticker filter", ticker_options)
    source_filter = st.selectbox(
        "Source filter",
        ["All", "transcript", "claim_verdict", "financial_snapshot"],
    )

    if st.button("Build / Rebuild Index", use_container_width=True):
        with st.spinner("Building RAG index..."):
            build_stats = RAGIndexBuilder().build(reset=True)
            get_retriever.clear()
        st.success(
            f"Indexed {build_stats.get('documents', 0)} docs and {build_stats.get('chunks', 0)} chunks."
        )
    if st.button("Clear Chat", use_container_width=True):
        st.session_state.analyst_messages = _initial_messages()
        st.rerun()

    st.divider()
    st.markdown("### Example Questions")
    st.caption("- What were Walmart revenue and operating margin trends in 2025?")
    st.caption("- Compare NVDA and MSFT EPS trajectory across Q1-Q4 2025.")
    st.caption("- Show unverifiable claim patterns for TSLA.")


if "analyst_messages" not in st.session_state:
    st.session_state.analyst_messages = _initial_messages()

for msg in st.session_state.analyst_messages:
    with st.chat_message(msg["role"]):
        st.markdown(_render_chat_text(msg["content"]))
        sources = msg.get("sources") or []
        if sources:
            st.caption(f"Sources: {', '.join(s['source_id'] for s in sources)}")
            for source in sources:
                _render_source_card(source)
        retrieval = msg.get("retrieval") or {}
        if retrieval:
            st.caption(
                "Retrieval: "
                f"{retrieval.get('results', 0)} results | "
                f"{retrieval.get('candidates', 0)} candidates | "
                f"{retrieval.get('latency_ms', 0)} ms"
            )
            source_types = pd.Series([s.get("source_type", "unknown") for s in sources]).value_counts()
            if not source_types.empty:
                st.bar_chart(source_types, use_container_width=True)

prompt = st.chat_input("Ask a grounded earnings/claims question...")
if prompt:
    st.session_state.analyst_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(_render_chat_text(prompt))

    session_api_key = str(st.session_state.get("session_openrouter_api_key", "")).strip()
    active_api_key = session_api_key or DEFAULT_OPENROUTER_KEY or None
    chatbot = AnalystChatbot(retriever=get_retriever(), api_key=active_api_key)
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.analyst_messages
        if m["role"] in {"user", "assistant"}
    ]
    active_filters = {}
    if selected_ticker != "All":
        active_filters["ticker"] = selected_ticker
    if source_filter != "All":
        active_filters["source_type"] = source_filter

    with st.chat_message("assistant"):
        with st.spinner("Retrieving evidence and generating analysis..."):
            response = chatbot.ask(
                prompt,
                top_k=top_k,
                history=history,
                filters=(active_filters or None),
            )

        answer = response.get("answer", "No answer generated.")
        sources = response.get("sources", [])
        retrieval = response.get("retrieval", {})

        st.markdown(_render_chat_text(answer))
        if sources:
            st.caption(f"Citations: {', '.join(response.get('citations', []))}")
            for source in sources:
                _render_source_card(source)

        st.caption(
            "Retrieval: "
            f"{retrieval.get('results', 0)} results | "
            f"{retrieval.get('candidates', 0)} candidates | "
            f"{retrieval.get('latency_ms', 0)} ms | "
            f"Model: {response.get('model_used', 'n/a')}"
        )

        source_types = pd.Series([s.get("source_type", "unknown") for s in sources]).value_counts()
        if not source_types.empty:
            st.bar_chart(source_types, use_container_width=True)

    st.session_state.analyst_messages.append(
        {
            "role": "assistant",
            "content": answer,
            "sources": sources,
            "retrieval": retrieval,
        }
    )

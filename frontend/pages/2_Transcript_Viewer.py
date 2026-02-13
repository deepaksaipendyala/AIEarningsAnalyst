"""Transcript Viewer: read transcripts with highlighted claims."""

import json
import html
import streamlit as st
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
VERDICTS_DIR = DATA_DIR / "verdicts"

VERDICT_COLORS = {
    "verified": "#bbf7d0",
    "close_match": "#fef08a",
    "mismatch": "#fecaca",
    "misleading": "#fed7aa",
    "unverifiable": "#e2e8f0",
}

VERDICT_ICONS = {
    "verified": "\u2705", "close_match": "\U0001f7e1", "mismatch": "\u274c",
    "misleading": "\u26a0\ufe0f", "unverifiable": "\u2753",
}

st.set_page_config(page_title="Transcript Viewer - EarningsLens", layout="wide")
st.title("Transcript Viewer")


@st.cache_data
def load_companies():
    path = DATA_DIR / "companies.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


@st.cache_data
def get_available_quarters(ticker):
    """Find all available transcript quarters for a ticker."""
    quarters = []
    if TRANSCRIPTS_DIR.exists():
        for f in sorted(TRANSCRIPTS_DIR.glob(f"{ticker}_Q*_*.json"), reverse=True):
            parts = f.stem.split("_")
            if len(parts) >= 3:
                quarters.append(f.stem)
    return quarters


@st.cache_data
def load_transcript(key):
    path = TRANSCRIPTS_DIR / f"{key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


@st.cache_data
def load_verdicts(key):
    path = VERDICTS_DIR / f"{key}_verdicts.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def build_highlighted_html(transcript_text: str, highlights: list) -> str:
    """Build HTML with multiple highlighted spans.

    Uses a forward-scanning approach: sort highlights by start position,
    escape text segments between highlights, wrap highlights with <mark>.
    Handles overlapping spans by skipping overlaps.
    """
    if not highlights:
        return html.escape(transcript_text)

    # Sort by start position, then by end (longer first)
    highlights.sort(key=lambda h: (h["start"], -h["end"]))

    # Remove overlapping spans (keep the first one encountered)
    cleaned = []
    last_end = 0
    for h in highlights:
        if h["start"] >= last_end:
            cleaned.append(h)
            last_end = h["end"]

    parts = []
    pos = 0
    for h in cleaned:
        if h["start"] > pos:
            parts.append(html.escape(transcript_text[pos:h["start"]]))
        color = VERDICT_COLORS.get(h["verdict"], "#e2e8f0")
        span_text = html.escape(transcript_text[h["start"]:h["end"]])
        metric_label = h.get("metric", "").replace("_", " ")
        parts.append(
            f'<mark style="background-color:{color};color:#0f172a;padding:1px 3px;'
            f'border-radius:3px;cursor:help" '
            f'title="{metric_label}: {h["verdict"]}">{span_text}</mark>'
        )
        pos = h["end"]

    if pos < len(transcript_text):
        parts.append(html.escape(transcript_text[pos:]))

    return "".join(parts)


companies = load_companies()
if not companies:
    st.warning("No company data found.")
    st.stop()

# Company selector
col1, col2 = st.columns([1, 2])
with col1:
    ticker_options = [c["ticker"] for c in companies]
    selected_ticker = st.selectbox("Company", ticker_options)

with col2:
    quarters = get_available_quarters(selected_ticker)
    if not quarters:
        st.warning(f"No transcripts found for {selected_ticker}")
        st.stop()
    selected_key = st.selectbox("Quarter", quarters,
                                 format_func=lambda x: x.replace("_", " "))

# Load data
transcript_data = load_transcript(selected_key)
verdict_data = load_verdicts(selected_key)

if not transcript_data:
    st.warning("Transcript not found.")
    st.stop()

transcript_text = transcript_data.get("text", "")

# Two-column layout: transcript left, claims right
left_col, right_col = st.columns([3, 2])

with right_col:
    st.subheader("Extracted Claims")

    if verdict_data:
        claims_with_verdicts = verdict_data.get("claims_with_verdicts", [])
        summary = verdict_data.get("summary", {})

        # Summary
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Total", summary.get("total", 0))
        mc2.metric("Verified", summary.get("verified", 0) + summary.get("close_match", 0))
        mc3.metric("Flagged", summary.get("mismatch", 0) + summary.get("misleading", 0))
        mc4.metric("Unverifiable", summary.get("unverifiable", 0))

        for cv in claims_with_verdicts:
            claim = cv.get("claim", {})
            verif = cv.get("verification", {})
            verdict = verif.get("verdict", "unverifiable")
            icon = VERDICT_ICONS.get(verdict, "")
            metric = claim.get("metric_type", "unknown").replace("_", " ").title()
            context = claim.get("metric_context", "")
            context_label = f" ({context})" if context and context != "Total" else ""

            with st.expander(
                f"{icon} {metric}{context_label} ({claim.get('claim_type', '').replace('_', ' ')}) - {verdict.upper()}",
                expanded=(verdict in ("mismatch", "misleading")),
            ):
                # Quote
                st.markdown(
                    f'<div style="background:#1e293b;border-left:3px solid #6366f1;'
                    f'padding:12px 16px;border-radius:0 8px 8px 0;font-style:italic;margin:8px 0">'
                    f'"{html.escape(claim.get("quote_text", ""))}"</div>',
                    unsafe_allow_html=True,
                )
                if claim.get("speaker"):
                    st.caption(f"Speaker: {claim['speaker']}")

                # Values
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**Claimed**")
                    raw = claim.get("claimed_value_raw", str(claim.get("claimed_value", "N/A")))
                    st.code(raw)
                with c2:
                    st.markdown("**Actual**")
                    actual = verif.get("actual_value")
                    metric_key = claim.get("metric_type", "")
                    if actual is not None:
                        if "margin" in metric_key:
                            st.code(f"{actual:.1f}%")
                        elif metric_key in ("eps_diluted", "eps_basic", "eps"):
                            st.code(f"${actual:,.2f}")
                        elif abs(actual) >= 1e9:
                            st.code(f"${actual/1e9:,.2f}B")
                        elif abs(actual) >= 1e6:
                            st.code(f"${actual/1e6:,.1f}M")
                        else:
                            st.code(f"{actual:,.2f}")
                    else:
                        st.code("N/A")
                with c3:
                    st.markdown("**Verdict**")
                    color = VERDICT_COLORS.get(verdict, "#e2e8f0")
                    st.markdown(
                        f"<span style='color:{color};font-size:1.1rem;font-weight:bold'>"
                        f"{icon} {verdict.replace('_',' ').upper()}</span>",
                        unsafe_allow_html=True,
                    )

                if verif.get("computation_detail"):
                    st.info(verif["computation_detail"])
                if verif.get("explanation"):
                    st.write(verif["explanation"])
                if verif.get("misleading_flags"):
                    for flag in verif["misleading_flags"]:
                        st.warning(flag.replace("_", " ").title())
                if verif.get("flags"):
                    for flag in verif["flags"]:
                        st.caption(f"Flag: {flag.replace('_', ' ')}")
                if verif.get("evidence_source"):
                    st.caption(f"Source: {verif['evidence_source']}")
    else:
        st.info("No claims extracted yet for this quarter.")

with left_col:
    st.subheader("Transcript")

    # Build highlighted transcript with all claim spans
    highlights = []
    if verdict_data and transcript_text:
        claims_with_verdicts = verdict_data.get("claims_with_verdicts", [])
        for cv in claims_with_verdicts:
            claim = cv.get("claim", {})
            verif = cv.get("verification", {})
            start = claim.get("quote_start_char")
            end = claim.get("quote_end_char")
            if start is not None and end is not None and 0 <= start < end <= len(transcript_text):
                highlights.append({
                    "start": start, "end": end,
                    "verdict": verif.get("verdict", "unverifiable"),
                    "metric": claim.get("metric_type", ""),
                })

    if transcript_text:
        highlighted_html = build_highlighted_html(transcript_text, highlights)
        st.markdown(
            f'<div style="background:#0f172a;padding:16px;border-radius:8px;'
            f'max-height:700px;overflow-y:auto;font-family:monospace;font-size:0.85rem;'
            f'white-space:pre-wrap;line-height:1.6">{highlighted_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("No transcript text available.")

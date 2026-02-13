"""Dashboard page: company overview and aggregate stats."""

import json
import streamlit as st
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
VERDICTS_DIR = DATA_DIR / "verdicts"

VERDICT_ICONS = {
    "verified": "✅", "close_match": "🟡", "mismatch": "❌",
    "misleading": "⚠️", "unverifiable": "❓",
}


@st.cache_data
def load_companies():
    path = DATA_DIR / "companies.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


@st.cache_data
def load_all_verdicts():
    all_data = {}
    if not VERDICTS_DIR.exists():
        return all_data
    for vf in sorted(VERDICTS_DIR.glob("*_verdicts.json")):
        with open(vf) as f:
            data = json.load(f)
        key = data.get("key", vf.stem.replace("_verdicts", ""))
        all_data[key] = data
    return all_data


st.set_page_config(page_title="Dashboard - EarningsLens", layout="wide")
st.title("Dashboard")

all_verdicts = load_all_verdicts()

if not all_verdicts:
    st.warning("No verdict data found. Run the pipeline first:\n\n"
               "```\npython scripts/run_pipeline.py\n```")
    st.stop()

# Aggregate summary
total = {"verified": 0, "close_match": 0, "mismatch": 0, "misleading": 0, "unverifiable": 0, "total": 0}
by_ticker = {}

for key, data in all_verdicts.items():
    ticker = data.get("ticker", key.split("_")[0])
    s = data.get("summary", {})
    for k in total:
        total[k] += s.get(k, 0)
    if ticker not in by_ticker:
        by_ticker[ticker] = {"quarters": [], "total": 0, "verified": 0, "close_match": 0,
                              "mismatch": 0, "misleading": 0, "unverifiable": 0}
    by_ticker[ticker]["quarters"].append(key)
    for k in ["total", "verified", "close_match", "mismatch", "misleading", "unverifiable"]:
        by_ticker[ticker][k] += s.get(k, 0)

# Summary metrics
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Total Claims", total["total"])
col2.metric("Verified", total["verified"], help="Within tolerance of reported data")
col3.metric("Close Match", total["close_match"], help="Within loose tolerance")
col4.metric("Mismatch", total["mismatch"], help="Significant deviation")
col5.metric("Misleading", total["misleading"], help="Possibly misleading framing")
col6.metric("Unverifiable", total["unverifiable"], help="Non-GAAP, guidance, etc.")

# Verification accuracy
verifiable = total["verified"] + total["close_match"] + total["mismatch"] + total["misleading"]
if verifiable > 0:
    accuracy = (total["verified"] + total["close_match"]) / verifiable * 100
    st.progress(accuracy / 100, text=f"Verification Accuracy: {accuracy:.1f}% of verifiable claims match reported data")

st.divider()

# Company cards
st.subheader("Companies")
companies = load_companies()
ticker_to_name = {c["ticker"]: c["name"] for c in companies}

sorted_tickers = sorted(by_ticker.items())
# Two rows of 5
for row_start in range(0, len(sorted_tickers), 5):
    row = sorted_tickers[row_start:row_start + 5]
    cols = st.columns(5)
    for col_idx, (ticker, stats) in enumerate(row):
        with cols[col_idx]:
            name = ticker_to_name.get(ticker, ticker)
            verified = stats["verified"] + stats["close_match"]
            flagged = stats["mismatch"] + stats["misleading"]
            verifiable = verified + flagged

            # Accuracy for verifiable claims only
            acc_str = ""
            if verifiable > 0:
                acc = verified / verifiable * 100
                acc_color = "#22c55e" if acc >= 95 else "#eab308" if acc >= 80 else "#ef4444"
                acc_str = f"<span style='color:{acc_color};font-size:1.4rem;font-weight:bold'>{acc:.0f}%</span> accuracy"

            st.markdown(
                f"<div style='background:#1e293b;padding:16px;border-radius:10px;margin-bottom:8px'>"
                f"<div style='font-size:1.3rem;font-weight:bold'>{ticker}</div>"
                f"<div style='color:#94a3b8;font-size:0.85rem;margin-bottom:8px'>{name}</div>"
                f"<div style='margin-bottom:6px'>{acc_str}</div>"
                f"<div style='font-size:0.85rem'>"
                f"<span style='color:#22c55e'>{verified} verified</span> · "
                f"<span style='color:#ef4444'>{flagged} flagged</span> · "
                f"<span style='color:#64748b'>{stats['unverifiable']} unverif.</span>"
                f"</div>"
                f"<div style='color:#64748b;font-size:0.8rem;margin-top:4px'>"
                f"{stats['total']} claims · {len(stats['quarters'])} quarters</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

# Sidebar info
with st.sidebar:
    st.markdown("### About")
    st.caption(
        "EarningsLens extracts quantitative claims from earnings calls "
        "and verifies them against GAAP financial data."
    )
    st.divider()
    st.markdown("### Data Sources")
    st.caption("Transcripts: Finnhub")
    st.caption("Financials: Financial Modeling Prep")
    st.divider()
    st.markdown("### Tolerance Reference")
    st.caption("Revenue: ±0.5%")
    st.caption("EPS: ±$0.01")
    st.caption("Margins: ±0.3 pp")
    st.caption("Growth rates: ±1.0 pp")

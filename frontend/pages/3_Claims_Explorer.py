"""Claims Explorer: filterable table of all claims with evidence details."""

import json
import html
import streamlit as st
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
VERDICTS_DIR = DATA_DIR / "verdicts"

VERDICT_ICONS = {
    "verified": "✅", "close_match": "🟡", "mismatch": "❌",
    "misleading": "⚠️", "unverifiable": "❓",
}

st.set_page_config(page_title="Claims Explorer - EarningsLens", layout="wide")
st.title("Claims Explorer")


@st.cache_data
def load_all_claims():
    """Load all claims with verdicts from all verdict files."""
    all_claims = []
    if not VERDICTS_DIR.exists():
        return all_claims

    for vf in sorted(VERDICTS_DIR.glob("*_verdicts.json")):
        with open(vf) as f:
            data = json.load(f)

        ticker = data.get("ticker", "")
        key = data.get("key", vf.stem.replace("_verdicts", ""))
        year = data.get("year", 0)
        quarter = data.get("quarter", 0)

        for cv in data.get("claims_with_verdicts", []):
            claim = cv.get("claim", {})
            verif = cv.get("verification", {})

            all_claims.append({
                "ticker": ticker,
                "quarter": f"Q{quarter} {year}",
                "year": year,
                "quarter_num": quarter,
                "key": key,
                "speaker": claim.get("speaker", "Unknown"),
                "metric": claim.get("metric_type", "other"),
                "claim_type": claim.get("claim_type", "other"),
                "claimed_value": claim.get("claimed_value"),
                "claimed_raw": claim.get("claimed_value_raw", ""),
                "actual_value": verif.get("actual_value"),
                "verdict": verif.get("verdict", "unverifiable"),
                "confidence": claim.get("confidence", 0),
                "gaap": claim.get("gaap_classification", "unknown"),
                "quote_text": claim.get("quote_text", ""),
                "computation_detail": verif.get("computation_detail", ""),
                "explanation": verif.get("explanation", ""),
                "evidence_source": verif.get("evidence_source", ""),
                "misleading_flags": verif.get("misleading_flags", []),
                "misleading_reasons": verif.get("misleading_reasons", []),
                "financial_facts_used": verif.get("financial_facts_used", []),
                "computation_steps": verif.get("computation_steps", []),
            })

    return all_claims


_PERCENT_METRICS = {"operating_margin", "gross_margin", "net_margin", "ebitda_margin"}
_PER_SHARE_METRICS = {"eps_diluted", "eps_basic", "eps"}


def _fmt_actual(val, metric: str) -> str:
    """Format actual values with sensible units."""
    if val is None:
        return "N/A"
    metric = metric or ""
    if "margin" in metric or metric in _PERCENT_METRICS:
        return f"{val:.1f}%"
    if metric in _PER_SHARE_METRICS:
        return f"${val:,.2f}"
    if abs(val) >= 1e9:
        return f"${val / 1e9:,.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:,.1f}M"
    if abs(val) >= 1e3:
        return f"${val / 1e3:,.1f}K"
    return f"{val:,.2f}"


all_claims = load_all_claims()

if not all_claims:
    st.warning("No claims data found. Run the pipeline first.")
    st.stop()

# Filters
st.sidebar.header("Filters")

tickers = sorted(set(c["ticker"] for c in all_claims))
selected_tickers = st.sidebar.multiselect("Companies", tickers, default=tickers)

metrics = sorted(set(c["metric"] for c in all_claims))
selected_metrics = st.sidebar.multiselect("Metrics", metrics, default=metrics)

verdicts = ["verified", "close_match", "mismatch", "misleading", "unverifiable"]
selected_verdicts = st.sidebar.multiselect("Verdicts", verdicts, default=verdicts)

# Apply filters
filtered = [
    c for c in all_claims
    if c["ticker"] in selected_tickers
    and c["metric"] in selected_metrics
    and c["verdict"] in selected_verdicts
]
filtered_df = pd.DataFrame(filtered)

# Summary row
st.markdown(f"**{len(filtered)}** claims matching filters")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Verified", sum(1 for c in filtered if c["verdict"] == "verified"))
col2.metric("Close Match", sum(1 for c in filtered if c["verdict"] == "close_match"))
col3.metric("Mismatch", sum(1 for c in filtered if c["verdict"] == "mismatch"))
col4.metric("Misleading", sum(1 for c in filtered if c["verdict"] == "misleading"))
col5.metric("Unverifiable", sum(1 for c in filtered if c["verdict"] == "unverifiable"))

st.divider()

if not filtered_df.empty:
    st.subheader("Visualizations")

    c1, c2 = st.columns(2)
    verdict_order = ["verified", "close_match", "mismatch", "misleading", "unverifiable"]
    verdict_counts = (
        filtered_df["verdict"]
        .value_counts()
        .reindex(verdict_order, fill_value=0)
    )
    with c1:
        st.caption("Verdict Distribution")
        st.bar_chart(verdict_counts, use_container_width=True)
        verdict_export = verdict_counts.rename_axis("verdict").reset_index(name="count")
        st.download_button(
            "Download Verdict Data (CSV)",
            verdict_export.to_csv(index=False),
            "claims_explorer_verdict_distribution.csv",
            "text/csv",
            key="claims_verdict_chart_csv",
        )

    metric_counts = filtered_df["metric"].value_counts().head(10)
    metric_counts.index = metric_counts.index.str.replace("_", " ").str.title()
    with c2:
        st.caption("Top Metrics (Filtered)")
        st.bar_chart(metric_counts, use_container_width=True)
        metric_export = metric_counts.rename_axis("metric").reset_index(name="count")
        st.download_button(
            "Download Metric Data (CSV)",
            metric_export.to_csv(index=False),
            "claims_explorer_top_metrics.csv",
            "text/csv",
            key="claims_metric_chart_csv",
        )

    trend_df = filtered_df.copy()
    trend_df["status_group"] = trend_df["verdict"].map({
        "verified": "Verified",
        "close_match": "Verified",
        "mismatch": "Flagged",
        "misleading": "Flagged",
        "unverifiable": "Unverifiable",
    })
    trend = (
        trend_df.groupby(["year", "quarter_num", "status_group"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["Verified", "Flagged", "Unverifiable"], fill_value=0)
        .sort_index()
    )
    trend.index = [f"Q{q} {y}" for y, q in trend.index]
    st.caption("Claims Trend by Quarter")
    st.area_chart(trend, use_container_width=True)
    trend_export = trend.rename_axis("quarter").reset_index()
    st.download_button(
        "Download Trend Data (CSV)",
        trend_export.to_csv(index=False),
        "claims_explorer_quarter_trend.csv",
        "text/csv",
        key="claims_trend_chart_csv",
    )

    st.divider()

# Table view
if filtered:
    df = pd.DataFrame([{
        "Ticker": c["ticker"],
        "Quarter": c["quarter"],
        "Metric": c["metric"].replace("_", " ").title(),
        "Type": c["claim_type"].replace("_", " ").title(),
        "Claimed": c["claimed_raw"] or str(c.get("claimed_value", "")),
        "Actual": _fmt_actual(c.get("actual_value"), c["metric"]) if c.get("actual_value") is not None else "N/A",
        "Verdict": f"{VERDICT_ICONS.get(c['verdict'], '')} {c['verdict'].replace('_', ' ').upper()}",
        "Confidence": f"{c['confidence']:.0%}" if c.get("confidence") else "",
        "GAAP": c["gaap"].upper(),
    } for c in filtered])

    st.dataframe(df, use_container_width=True, hide_index=True)

    # Detail view
    st.divider()
    st.subheader("Claim Details")

    for i, claim in enumerate(filtered):
        icon = VERDICT_ICONS.get(claim["verdict"], "")
        metric_display = claim["metric"].replace("_", " ").title()

        with st.expander(
            f"{icon} {claim['ticker']} {claim['quarter']} - {metric_display} - {claim['verdict'].upper()}",
            expanded=(claim["verdict"] in ("mismatch", "misleading")),
        ):
            # Quote
            st.markdown(
                f'<div style="background:#1e293b;border-left:3px solid #6366f1;'
                f'padding:12px 16px;border-radius:0 8px 8px 0;font-style:italic;margin:8px 0">'
                f'"{html.escape(claim["quote_text"])}"</div>',
                unsafe_allow_html=True,
            )
            if claim.get("speaker"):
                st.caption(f"Speaker: {claim['speaker']}")

            # Values
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**Claimed**")
                st.code(claim.get("claimed_raw") or str(claim.get("claimed_value", "N/A")))
            with c2:
                st.markdown("**Actual**")
                st.code(_fmt_actual(claim.get("actual_value"), claim["metric"]))
            with c3:
                st.markdown("**Verdict**")
                st.markdown(f"**{icon} {claim['verdict'].replace('_', ' ').upper()}**")

            # Computation
            if claim.get("computation_detail"):
                st.markdown("**Computation**")
                st.info(claim["computation_detail"])

            # Explanation
            if claim.get("explanation"):
                st.markdown("**Explanation**")
                st.write(claim["explanation"])

            # Evidence
            if claim.get("financial_facts_used"):
                st.markdown("**Financial Facts Used**")
                for fact in claim["financial_facts_used"]:
                    fv = fact.get("value")
                    fv_str = _fmt_actual(fv, fact.get("field", "")) if fv is not None else "N/A"
                    st.caption(f"  {fact.get('field', '')}: Q{fact.get('fq', '')} {fact.get('fy', '')} = {fv_str}")

            # Computation steps
            if claim.get("computation_steps"):
                st.markdown("**Computation Steps**")
                for step in claim["computation_steps"]:
                    st.caption(f"  {step.get('step', '')}: {step.get('formula', '')} = {step.get('result', '')}")

            # Misleading flags
            if claim.get("misleading_flags"):
                st.markdown("**Misleading Flags**")
                for flag in claim["misleading_flags"]:
                    st.warning(flag.replace("_", " ").title())
                for reason in claim.get("misleading_reasons", []):
                    st.write(f"  {reason}")

            if claim.get("evidence_source"):
                st.caption(f"Data source: {claim['evidence_source']}")
else:
    st.info("No claims match the selected filters.")

# Export
st.divider()
if filtered and st.button("Export to CSV"):
    df_export = pd.DataFrame([{
        "ticker": c["ticker"],
        "quarter": c["quarter"],
        "metric": c["metric"],
        "claim_type": c["claim_type"],
        "claimed_value": c.get("claimed_value"),
        "actual_value": c.get("actual_value"),
        "verdict": c["verdict"],
        "gaap": c["gaap"],
        "quote_text": c["quote_text"],
        "explanation": c.get("explanation", ""),
    } for c in filtered])
    csv = df_export.to_csv(index=False)
    st.download_button("Download CSV", csv, "claims_export.csv", "text/csv")

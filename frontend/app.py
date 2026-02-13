"""EarningsLens - Streamlit Dashboard Entry Point."""

import streamlit as st

st.set_page_config(
    page_title="EarningsLens",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .verdict-verified { color: #22c55e; font-weight: bold; }
    .verdict-close_match { color: #eab308; font-weight: bold; }
    .verdict-mismatch { color: #ef4444; font-weight: bold; }
    .verdict-misleading { color: #f97316; font-weight: bold; }
    .verdict-unverifiable { color: #94a3b8; font-weight: bold; }
    .quote-text {
        background: #1e293b;
        border-left: 3px solid #6366f1;
        padding: 12px 16px;
        border-radius: 0 8px 8px 0;
        font-style: italic;
        margin: 8px 0;
    }
</style>
""", unsafe_allow_html=True)

st.title("EarningsLens")
st.markdown("**Automated verification of executive claims from earnings calls against reported financial data.**")
st.markdown("---")
st.markdown("Use the sidebar to navigate between pages:")
st.markdown("- **Dashboard**: Overview of all companies and aggregate stats")
st.markdown("- **Transcript Viewer**: Read transcripts with highlighted claims")
st.markdown("- **Claims Explorer**: Filter and explore individual claims with full evidence")
st.markdown("- **AI Analyst**: Ask grounded questions with source-cited retrieval")

st.page_link("pages/4_AI_Analyst.py", label="Open AI Analyst Chat", icon="🤖")

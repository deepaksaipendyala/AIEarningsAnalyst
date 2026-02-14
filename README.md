# EarningsLens

**Automated verification of executive claims from earnings calls against reported financial data.**

EarningsLens analyzes earnings call transcripts from 10 public companies (last 4 quarters each), extracts quantitative claims made by management (e.g., "revenue grew 15% year over year"), and verifies each claim against structured GAAP financial data. It flags discrepancies and misleading framing.

## Live Demo

**Streamlit Dashboard**: [aiearningsanalyst.streamlit.app](https://aiearningsanalyst.streamlit.app)

## Results Summary

| Metric | Count |
|--------|-------|
| Total claims extracted | 954 |
| Verified | 142 |
| Close match | 15 |
| Mismatch | 0 |
| Misleading | 0 |
| Unverifiable | 797 |
| **Verification accuracy** | **100.0%** (of verifiable claims) |

> 797 claims are "unverifiable" primarily because segment-level, non-GAAP, and forward-guidance claims cannot be validated against consolidated GAAP quarterly datasets.

## Architecture

```
Transcripts (Multi-source)  →  Gemini Flash (Extraction)  →  Python (Verification)  →  Streamlit (Dashboard)
         ↓                            ↓                            ↓
   Speaker-segmented            Quote-anchored              Deterministic math
   canonical text               structured claims           with tolerance matrix
         ↓                            ↓                            ↓
   FMP (Financials)            Span validation              Misleading heuristics
```

**Key design principle:** Separation of concerns. The LLM handles extraction (probabilistic — finding claims in unstructured text). Python handles verification (deterministic — all arithmetic, tolerance checks, and verdict production). No LLM is involved in any math.

## Companies (10, diverse sectors)

| Ticker | Company | Sector |
|--------|---------|--------|
| AAPL | Apple Inc. | Technology |
| MSFT | Microsoft Corp. | Technology |
| GOOGL | Alphabet Inc. | Technology |
| AMZN | Amazon.com Inc. | Consumer Cyclical |
| TSLA | Tesla Inc. | Automotive |
| NVDA | NVIDIA Corp. | Technology |
| META | Meta Platforms Inc. | Technology |
| JPM | JPMorgan Chase & Co. | Financial Services |
| JNJ | Johnson & Johnson | Healthcare |
| WMT | Walmart Inc. | Consumer Defensive |

## Data Sources

| Source | Purpose | Access |
|--------|---------|--------|
| **earningscall.biz** (primary) | Earnings call transcripts | Python library |
| **Motley Fool / FMP / mlq.ai** (fallback chain) | Transcript recovery chain | Scraping / API |
| **Financial Modeling Prep** | Quarterly income statements, cash flow | API key (250 req/day free) |
| **Gemini 3 Flash** (via OpenRouter) | Claim extraction with function calling | API key |

## Verification Logic

### Tolerance Matrix

| Metric | Tolerance | Rationale |
|--------|-----------|-----------|
| Revenue | ±0.5% | Rounds and presentation differences in calls |
| EPS | ±$0.005 | Tight EPS precision for per-share claims |
| Margins | ±0.5 pp | Verbal rounding in calls |
| Growth rates | ±1.0 pp | Often stated as "about 15%" |
| EBITDA/FCF | ±1.0% | More estimation involved |
| "Approximate" claims | 2× above | "About", "roughly", "approximately" |

### Verdict Labels

- **Verified**: Claimed value matches GAAP data within tolerance
- **Close Match**: Within loose tolerance (2-5%)
- **Mismatch**: Significant deviation from reported data
- **Misleading**: Numerically accurate but deceptive framing
- **Unverifiable**: Non-GAAP, guidance, or missing data

### Misleading Heuristics

1. **Cherry-picking timeframe**: Cites positive QoQ growth when YoY is negative (< -5%)
2. **GAAP/non-GAAP mixing**: EPS exceeds GAAP by >15% without "adjusted"/"non-GAAP" disclosure
3. **Low-base exaggeration**: >100% growth on a metric that's <1% of total revenue

### False Positive Prevention

The verification engine includes several heuristics to prevent false mismatches:

- **TTM/multi-period aggregation**: Verifies trailing-twelve-month (TTM), first-half, first-nine-month, and YTD totals when quarter-level data is available
- **Bank revenue handling**: Detects JPM-style net interest revenue vs FMP gross revenue
- **CapEx lease inclusion**: Recognizes "including finance leases" in capital expenditure claims
- **Total expenses vs OpEx**: Distinguishes "total costs and expenses" (COGS + OpEx) from operating expenses alone
- **Segment detection**: Identifies subset/segment metrics that don't match consolidated totals
- **BPS margin change**: Detects basis-point margin expansion/contraction claims
- **FCF/CapEx definition gaps**: Tolerates known definition differences (e.g., FCF with/without certain items)
- **Conflicting transcript values**: Downgrades hard mismatches when the same transcript contains a second value for the same metric/period that matches reported data

## Key Decisions

1. **Multi-source transcript fallback**: earningscall.biz → Motley Fool → FMP → mlq.ai (local + direct web hack) ensures high coverage across all 10 companies
2. **FMP as primary financial source**: Clean quarterly data with consistent field names; 5-quarter lookback on free tier covers most verification needs
3. **Quote anchoring with character offsets**: Every claim maps to exact transcript coordinates, enabling inline transcript highlighting and proving claims are grounded in actual text
4. **GAAP-only verification by default**: SEC and FMP data is GAAP. Non-GAAP claims flagged as unverifiable rather than producing false mismatches
5. **Conservative tolerance**: Errs toward "close match" because executives routinely round numbers in verbal presentation
6. **Three-tier misleading detection**: Operational heuristics with explicit thresholds rather than LLM-based "vibes"
7. **OpenRouter for extraction**: Uses Gemini 3 Flash Preview for fast, cost-effective structured claim extraction with function calling

## What I'd Improve With More Time

- **Non-GAAP reconciliation**: Parse 8-K press releases to extract non-GAAP-to-GAAP reconciliation tables, enabling verification of adjusted metrics
- **Segment-level verification**: Map segment names between calls and filings (e.g., "Cloud" vs "Intelligent Cloud")
- **Quarter-to-quarter narrative consistency**: Track whether this quarter's explanation contradicts last quarter's guidance
- **Broader annual/segment coverage**: Expand full-year verification beyond consolidated supported metrics and add segment-level mapping
- **Paid FMP tier**: Would unlock 8+ quarters of historical data, making more YoY growth claims verifiable (currently 797 unverifiable claims, many due to missing baseline quarters)
- **Regex fallback extractor**: Cross-validate LLM extraction with deterministic regex patterns
- **Gold-set evaluation**: Manually annotate 2-3 transcripts for precision/recall measurement

## Running Locally

### Prerequisites

```bash
# Python 3.11+
python --version

# API keys (only needed for re-running pipeline, not for viewing pre-computed results)
export FMP_API_KEY=your_key
export OPENROUTER_API_KEY=your_key
export ANALYST_APP_PASSWORD=your_password   # Optional: protect AI Analyst page
```

### Setup

```bash
cd earnings-verifier
pip install -e .
```

### Launch Dashboard (pre-computed data included)

```bash
streamlit run frontend/app.py
# Opens at http://localhost:8501
```

### Build RAG Index + Run AI Analyst

```bash
# Build hybrid index (SQL + vectors + entity graph)
python scripts/build_rag_index.py

# Then launch app and open "AI Analyst" page in sidebar
streamlit run frontend/app.py
```

### Re-run Pipeline (requires API keys)

```bash
# All 10 companies, last 4 quarters
python scripts/run_pipeline.py

# Single company
python scripts/run_pipeline.py --ticker AAPL

# Individual phases
python scripts/run_pipeline.py --phase ingest
python scripts/run_pipeline.py --phase extract
python scripts/run_pipeline.py --phase verify

# Force re-extraction even when claim cache exists
python scripts/run_pipeline.py --phase extract --force
```

### Run API Server

```bash
uvicorn backend.main:app --reload
# Opens at http://localhost:8000
# API docs at http://localhost:8000/docs
```

### AI Analyst API Endpoints

```bash
# Check index status
GET /api/v1/analyst/index/status

# Build/rebuild index
POST /api/v1/analyst/index/build
{
  "reset": true
}

# Retrieve grounded evidence only
POST /api/v1/analyst/retrieve
{
  "question": "Compare WMT and COST revenue in Q4 2025",
  "top_k": 8
}

# Full analyst response with citations
POST /api/v1/analyst/chat
{
  "question": "Why is NVDA Q4 2025 mostly unverifiable?",
  "top_k": 8
}
```

### Run Tests

```bash
pytest tests/ -v
```

## Project Structure

```
earnings-verifier/
├── backend/
│   ├── main.py                         # FastAPI app
│   ├── config.py                       # Pydantic Settings
│   ├── database.py                     # SQLAlchemy setup
│   ├── models/                         # SQLAlchemy ORM models
│   │   ├── company.py
│   │   ├── transcript.py
│   │   ├── financial_data.py
│   │   └── claim.py                    # Claim + Verdict models
│   ├── schemas/                        # Pydantic schemas
│   │   ├── claim.py                    # API response schemas
│   │   └── extraction.py              # LLM structured output schema
│   ├── api/
│   │   ├── dashboard.py               # Dashboard/claims routes
│   │   └── analyst.py                 # RAG index + analyst chat routes
│   └── services/
│       ├── ingestion/
│       │   ├── transcript_client.py    # Multi-source transcript fetching
│       │   └── fmp_client.py           # Financial data fetching
│       ├── extraction/
│       │   ├── llm_extractor.py        # Gemini structured output
│       │   ├── normalizer.py           # "$1.5B" → 1500000000
│       │   └── validator.py            # Quote span validation
│       ├── verification/
│       │   ├── metric_catalog.py       # Metric → FMP field mapping
│       │   ├── period_resolver.py      # Fiscal calendar handling
│       │   ├── compute.py             # Deterministic math (YoY, margins)
│       │   ├── tolerances.py          # Tolerance matrix
│       │   └── verdict_engine.py      # Final verdict + false positive prevention
│       ├── misleading/
│       │   └── heuristics.py          # 3 misleading heuristics
│       ├── rag/
│       │   ├── index_builder.py       # Builds SQL/vector/graph hybrid index
│       │   ├── retriever.py           # Hybrid lexical+dense+graph retrieval
│       │   └── analyst.py             # Citation-grounded analyst chat service
│       └── pipeline.py                # Orchestrator
├── frontend/
│   ├── app.py                         # Streamlit entry point
│   └── pages/
│       ├── 1_Dashboard.py             # Company overview + accuracy rates
│       ├── 2_Transcript_Viewer.py     # Transcript + inline claim highlighting
│       ├── 3_Claims_Explorer.py       # Filterable claims table + evidence
│       └── 4_AI_Analyst.py            # Grounded chat with citations
├── scripts/
│   ├── run_pipeline.py                # CLI pipeline runner
│   ├── build_rag_index.py             # RAG index builder
│   └── seed_companies.py             # Database seeder
├── tests/
│   ├── test_normalizer.py
│   ├── test_compute.py
│   └── test_tolerances.py
├── data/
│   ├── companies.json                 # 10 companies with metadata
│   ├── transcripts/                   # 40 cached transcripts
│   ├── claims/                        # 40 extracted claim files
│   └── verdicts/                      # 40 verification result files
├── pyproject.toml
├── requirements.txt                   # Streamlit Cloud dependencies
├── Dockerfile
└── .env.example
```

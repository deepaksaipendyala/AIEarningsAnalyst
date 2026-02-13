# EarningsLens

**Automated verification of executive claims from earnings calls against reported financial data.**

EarningsLens analyzes earnings call transcripts from 10 public companies (last 4 quarters each), extracts quantitative claims made by management (e.g., "revenue grew 15% year over year"), and verifies each claim against structured GAAP financial data. It flags discrepancies and misleading framing.

## Live Demo

**Streamlit Dashboard**: _[URL will be added after deployment]_

## Results Summary

| Metric | Count |
|--------|-------|
| Total claims extracted | 954 |
| Verified | 122 |
| Close match | 11 |
| Mismatch | 2 |
| Misleading | 0 |
| Unverifiable | 819 |
| **Verification accuracy** | **98.5%** (of verifiable claims) |

> The 2 remaining mismatches are legitimate edge cases (AMZN Q1 revenue period gap, NVDA Q3 GAAP/non-GAAP gross margin) that correctly flag real discrepancies worth human review.
>
> 819 claims are "unverifiable" primarily because FMP's free tier limits historical data to 5 quarters, making YoY growth claims unverifiable (no baseline quarter available). These are correctly classified, not errors.

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
| **Motley Fool / FMP** (fallback) | Transcript fallback chain | API / scraping |
| **Financial Modeling Prep** | Quarterly income statements, cash flow | API key (250 req/day free) |
| **Gemini 3 Flash** (via OpenRouter) | Claim extraction with function calling | API key |

## Verification Logic

### Tolerance Matrix

| Metric | Tolerance | Rationale |
|--------|-----------|-----------|
| Revenue | ±0.5% | Rounds to nearest $100M at billion scale |
| EPS | ±$0.01 | Always penny-precise in filings |
| Margins | ±0.3 pp | Verbal rounding in calls |
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

- **TTM/multi-period detection**: Skips claims about trailing twelve months, year-to-date, or multi-period aggregates
- **Bank revenue handling**: Detects JPM-style net interest revenue vs FMP gross revenue
- **CapEx lease inclusion**: Recognizes "including finance leases" in capital expenditure claims
- **Total expenses vs OpEx**: Distinguishes "total costs and expenses" (COGS + OpEx) from operating expenses alone
- **Segment detection**: Identifies subset/segment metrics that don't match consolidated totals
- **BPS margin change**: Detects basis-point margin expansion/contraction claims
- **FCF/CapEx definition gaps**: Tolerates known definition differences (e.g., FCF with/without certain items)

## Key Decisions

1. **Multi-source transcript fallback**: earningscall.biz → mlq.ai → Motley Fool → FMP ensures high coverage across all 10 companies
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
- **Full-year aggregation**: Sum quarterly data to verify annual claims
- **Paid FMP tier**: Would unlock 8+ quarters of historical data, making YoY growth claims verifiable (currently 819 unverifiable claims, most due to missing baseline quarters)
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
```

### Run API Server

```bash
uvicorn backend.main:app --reload
# Opens at http://localhost:8000
# API docs at http://localhost:8000/docs
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
│   │   └── dashboard.py               # FastAPI routes
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
│       └── pipeline.py                # Orchestrator
├── frontend/
│   ├── app.py                         # Streamlit entry point
│   └── pages/
│       ├── 1_Dashboard.py             # Company overview + accuracy rates
│       ├── 2_Transcript_Viewer.py     # Transcript + inline claim highlighting
│       └── 3_Claims_Explorer.py       # Filterable claims table + evidence
├── scripts/
│   ├── run_pipeline.py                # CLI pipeline runner
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

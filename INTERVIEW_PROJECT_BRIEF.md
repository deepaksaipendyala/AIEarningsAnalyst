# EarningsLens Interview Brief

## 1) One-Line Pitch
EarningsLens is a grounded AI financial analyst that extracts quantitative claims from earnings calls and verifies them deterministically against reported financial data, then serves explainable results in dashboards and a hybrid-RAG chat assistant.

## 2) Problem We Solved
Earnings call transcripts contain many numeric claims (revenue, margins, EPS, growth), but they are hard to audit quickly.

The project requirement was to:
1. Ingest transcript + financial data reliably.
2. Extract structured claims from unstructured text.
3. Verify claims with deterministic financial logic.
4. Surface results in a usable analyst UI.
5. Add an AI assistant that can answer grounded questions with citations.

## 3) What We Built (End-to-End)
1. Data ingestion pipeline for 10 companies x 4 quarters.
2. LLM-based claim extraction with strict schema and quote anchoring.
3. Deterministic verification engine with metric-specific tolerances.
4. SEC + FMP blended data strategy for better historical coverage.
5. Streamlit product UI (Dashboard, Transcript Viewer, Claims Explorer, AI Analyst).
6. Hybrid RAG index and chat assistant with retrieval traces and citations.

## 4) Architecture (Separation of Concerns)
1. **Ingestion (deterministic):** transcripts + FMP + SEC companyfacts.
2. **Extraction (probabilistic):** LLM extracts structured claims only.
3. **Verification (deterministic):** Python computes truth checks and verdicts.
4. **Serving/UI:** FastAPI + Streamlit for analytics and interactive investigation.
5. **RAG Analyst:** SQL + vector + graph retrieval, then grounded response generation.

Why this matters:
- LLM is used for understanding text, not for arithmetic truth.
- All math and verdicting are reproducible and auditable.

## 5) Key Engineering Work and Fixes

### A) Fiscal period mapping bug fix (high-impact)
Issue:
- FMP stable payloads had `calendarYear=None` and fiscal-year-based quarter labels.
- Non-calendar fiscal companies (WMT, NVDA, AAPL, MSFT) were failing period alignment.

Fix:
1. Added fiscal key extraction + calendar alias extraction from statement `date`.
2. Stored explicit alias map `calendar -> fiscal` for lookup.
3. Guarded alias usage to avoid over-matching fiscal claims.

Impact:
- Eliminated a major root cause of mass `unverifiable` for non-calendar FY companies.

### B) SEC fallback integration
Issue:
- FMP free tier period depth is limited.

Fix:
1. Added SEC CompanyFacts parser and cache.
2. Backfilled only missing metric-period points to preserve FMP as primary source.
3. Added support for key balance-sheet helpers (cash/marketable, debt, net cash).

Impact:
- Increased evidence coverage without sacrificing source consistency.

### C) Verification logic expansion
Implemented:
1. Full-year aggregation support.
2. TTM / first-half / first-nine-month / YTD style handling.
3. Basis-point margin change interpretation.
4. Quarter-to-quarter discrepancy step for growth narratives.
5. Conflict downgrade logic when transcript includes contradictory numbers.

### D) Precision controls
Implemented tighter tolerances where requested:
1. Revenue tight tolerance: `0.005` (0.5%).
2. EPS absolute tolerance: `$0.005`.
3. Gross/Operating margin tight tolerance: `0.005` (0.5 pp scale in engine convention).

### E) Extraction quality upgrades with context
Issue:
- `period` and `comparison_period` could drift in extraction.

Fix:
1. Injected compact SEC/FMP period map into extraction prompt.
2. Added transcript source URL fiscal-hint parsing when present.
3. Added force re-extraction flag to bypass cache quickly.

Command:
```bash
python scripts/run_pipeline.py --phase extract --force
```

### F) Product/UI improvements
1. Added charts across Dashboard, Claims Explorer, and Transcript Viewer.
2. Added AI Analyst Streamlit page with chat, source cards, retrieval diagnostics.
3. Added import/path fixes and markdown money-rendering fixes for clean UI output.

### G) RAG analyst system
Implemented full hybrid retrieval stack:
1. **Indexer:** transcript docs + claim/verdict docs + financial snapshot docs.
2. **Storage:** SQLite with chunks, vectors, entity nodes, graph edges.
3. **Retriever:** lexical (BM25) + dense similarity + entity boosts + graph boosts.
4. **API:** status/build/retrieve/chat endpoints.
5. **Fallback mode:** extractive answering when generation model key is absent.

Scoring strategy:
- `score = 0.45*dense + 0.35*lexical + 0.20*entity + prior`

## 6) Final Measured Results (Current Run)
After forced re-extraction + re-verification:

- Total claims: `906`
- Verified: `156`
- Close match: `12`
- Mismatch: `4`
- Misleading: `0`
- Unverifiable: `734`

RAG index status:
- Documents: `998`
- Chunks: `2981`
- Nodes: `82`
- Edges: `8014`

Quality gates:
- Test suite passing: `100 passed`

## 7) Why This Is Interesting “SOTA-Style” Work in 2 Days
This is compelling because it combines modern best practices from production AI systems rather than a single-model demo.

1. **Grounded-by-design:** retrieval + explicit citations.
2. **Deterministic truth engine:** all numerical checks in code, not LLM hallucination.
3. **Hybrid retrieval:** lexical + semantic + graph/entity signals.
4. **Operational robustness:** caching, force reruns, schema validation, fallback modes.
5. **Explainability:** verdict reasons, computation steps, evidence traces.
6. **Pragmatic speed:** shipped full pipeline + UI + API + tests under tight time.

## 8) What Makes It Interview-Strong
1. It solves a real analyst workflow problem, not a toy prompt app.
2. It shows full-stack ownership: data engineering, ML prompt design, backend, frontend, QA.
3. It demonstrates debugging under ambiguity (fiscal period bugs, context misalignment, UI rendering edge cases).
4. It balances product usefulness and technical rigor.

## 9) Limitations (Honest Assessment)
1. High `unverifiable` is expected for segment, non-GAAP, and guidance claims.
2. Paid financial data depth would improve YoY baseline coverage.
3. Segment-level mapping (e.g., AWS, iPhone, Cloud) can be expanded further.
4. Misleading classification can be expanded with more heuristics and calibration.

## 10) Next Steps (If Given More Time)
1. Segment-level verification with taxonomy mapping.
2. Non-GAAP reconciliation from 8-K earnings releases.
3. Better ranking with external embeddings + rerankers.
4. Evaluation harness with hand-labeled benchmark set.
5. Auto-generated analyst reports from verdict deltas quarter-over-quarter.

## 11) Live Demo Script for Interview
1. Build index:
```bash
python scripts/build_rag_index.py
```
2. Run app:
```bash
streamlit run frontend/app.py
```
3. Show verification pipeline:
```bash
python scripts/run_pipeline.py --phase verify
```
4. Ask AI Analyst:
- “What are the flagged claims for WMT?”
- “Show mismatches or misleading claims overall.”
- “Compare verified vs unverifiable claims for NVDA and AAPL.”

## 12) 60-Second Verbal Summary
In two days, I built an end-to-end earnings-claim verification platform: robust ingestion, structured claim extraction, deterministic financial verification, explainable UI, and a hybrid-RAG analyst assistant with citations. The key engineering win was fixing fiscal period mapping and adding SEC/FMP context to reduce comparison errors. The system is test-backed, reproducible, and production-shaped: clear separation of probabilistic extraction from deterministic truth checks, with traceable evidence at every step.

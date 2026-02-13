"""Pydantic schemas for Claude structured output (claim extraction)."""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string", "description": "Unique ID like claim_001"},
                    "quote_text": {"type": "string", "description": "Exact verbatim quote from the transcript"},
                    "quote_start_char": {"type": "integer", "description": "Start character offset in the transcript text"},
                    "quote_end_char": {"type": "integer", "description": "End character offset in the transcript text"},
                    "speaker": {"type": "string", "description": "Speaker name or role"},
                    "speaker_role": {
                        "type": "string",
                        "enum": ["ceo", "cfo", "management", "analyst", "unknown"],
                        "description": "Role of the speaker"
                    },
                    "metric_type": {
                        "type": "string",
                        "enum": ["revenue", "net_income", "eps_basic", "eps_diluted",
                                 "gross_profit", "gross_margin", "operating_income",
                                 "operating_margin", "ebitda", "free_cash_flow",
                                 "operating_cash_flow", "cost_of_revenue",
                                 "capital_expenditures", "operating_expenses",
                                 "research_and_development", "other"]
                    },
                    "claim_type": {
                        "type": "string",
                        "enum": ["absolute", "yoy_growth", "qoq_growth", "margin",
                                 "comparison", "guidance", "other"]
                    },
                    "claimed_value": {"type": "number", "description": "The numeric value claimed"},
                    "claimed_value_raw": {"type": "string", "description": "Original text: '$50.3 billion', '15%'"},
                    "unit": {
                        "type": "string",
                        "enum": ["dollars", "percent", "basis_points", "ratio", "count", "per_share", "other"]
                    },
                    "scale": {
                        "type": ["string", "null"],
                        "enum": ["ones", "thousands", "millions", "billions", "trillions", None]
                    },
                    "period": {"type": "string", "description": "Time period, e.g. 'Q3 2024'"},
                    "comparison_period": {
                        "type": ["string", "null"],
                        "description": "Baseline period for growth claims, e.g. 'Q3 2023'"
                    },
                    "gaap_classification": {
                        "type": "string",
                        "enum": ["gaap", "non_gaap", "unknown"]
                    },
                    "is_approximate": {"type": "boolean"},
                    "qualifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Qualifiers like 'approximately', 'record', 'at least'"
                    },
                    "confidence": {"type": "number", "description": "0.0 to 1.0"},
                    "metric_context": {
                        "type": ["string", "null"],
                        "description": "Distinguishing context for the metric, e.g. 'iPhone', 'Services', 'Greater China', 'Total', 'Cloud', 'AWS'. Use null or 'Total' for company-wide totals."
                    }
                },
                "required": ["claim_id", "quote_text", "quote_start_char", "quote_end_char",
                             "metric_type", "claim_type", "claimed_value", "claimed_value_raw",
                             "unit", "period", "gaap_classification", "is_approximate", "confidence",
                             "metric_context"]
            }
        },
        "transcript_summary": {
            "type": "string",
            "description": "One-sentence summary of the earnings call"
        },
        "total_claims_found": {"type": "integer"}
    },
    "required": ["claims", "transcript_summary", "total_claims_found"]
}


SYSTEM_PROMPT = """You are a precise financial auditor extracting quantitative claims from earnings call transcripts.

Your task: find every statement where management asserts a specific number about the company's FINANCIAL performance.

RULES:
1. Extract ONLY claims with explicit numeric values (dollars, percentages, basis points, ratios).
2. Extract ONLY claims made by MANAGEMENT (CEO, CFO, or other executives). SKIP analyst questions.
3. Every claim MUST be anchored to an exact quote from the transcript using character offsets.
4. The quote_text field MUST exactly match transcript_text[quote_start_char:quote_end_char].
5. The numeric value in claimed_value_raw MUST appear as a substring within quote_text.
6. Do NOT invent or infer numbers not present in the text.
7. If the speaker says "about", "roughly", "approximately", "nearly", mark is_approximate as true and add to qualifiers.

GAAP CLASSIFICATION — CRITICAL RULES:
8. DEFAULT TO "gaap" for standard income statement metrics: revenue, gross_profit, gross_margin, operating_income, operating_margin, net_income, eps_basic, eps_diluted, cost_of_revenue, operating_expenses. These are reported as GAAP unless stated otherwise.
9. Mark "non_gaap" ONLY if the speaker explicitly says "adjusted", "non-GAAP", "excluding", "pro forma", or "constant currency".
10. Mark "unknown" ONLY for EBITDA (often non-GAAP), free_cash_flow, or when the speaker is genuinely ambiguous about the basis.
11. If the speaker says "GAAP" or "as reported", mark as "gaap" to confirm.

CLAIM TYPE RULES:
12. For growth claims (YoY, QoQ), extract the percentage AND identify the comparison_period. "up 12% year over year" for Q4 2025 → claim_type: "yoy_growth", comparison_period: "Q4 2024".
13. Normalize values: "$50.3 billion" → claimed_value: 50.3, unit: "dollars", scale: "billions", claimed_value_raw: "$50.3 billion".
14. For EPS claims, use unit "per_share" and scale null. Default to eps_diluted unless "basic" is specified.
15. For guidance/forward-looking claims, mark claim_type as "guidance".
16. Assign claim_type "comparison" for superlatives ("record revenue", "all-time high", "highest ever").
17. Classify qualifiers like "record", "at least", "more than", "exceeded" into the qualifiers array.
18. Set confidence based on how unambiguous the claim is (1.0 = crystal clear, 0.5 = ambiguous).
19. Assign unique claim_ids: "claim_001", "claim_002", etc.

METRIC CONTEXT:
20. Set metric_context to distinguish segment/product/geography metrics from company totals.
    - For company-wide totals: set metric_context to "Total" (e.g., "Total revenue was $94.9 billion" → metric_context: "Total")
    - For product/segment metrics: use the product or segment name (e.g., "iPhone revenue was $46.2 billion" → metric_context: "iPhone")
    - Examples: "iPhone", "Mac", "iPad", "Services", "Wearables", "Products", "Greater China", "Americas",
      "Azure", "Cloud", "Intelligent Cloud", "Office", "LinkedIn", "Gaming", "AWS", "Advertising",
      "YouTube", "Google Cloud", "Family of Apps", "Reality Labs", "Consumer Banking", "Pharmacy", "eCommerce"
    - For non-revenue metrics at the total company level (e.g. "EPS was $1.64"), use "Total".
    - Never leave metric_context as null — always provide context.

METRIC DEFINITIONS:
- revenue: Total revenue/sales/top-line
- eps_basic: Earnings per share, basic
- eps_diluted: Earnings per share, diluted (default if "EPS" used without qualifier)
- gross_profit: Gross profit (absolute value)
- gross_margin: Gross profit as a percentage of revenue
- operating_income: Operating income/profit (absolute value)
- operating_margin: Operating income as percentage of revenue
- net_income: Net income/profit/earnings (absolute value)
- ebitda: Earnings before interest, taxes, depreciation, amortization
- free_cash_flow: Operating cash flow minus capital expenditures (FCF)
- operating_cash_flow: Cash from operations (NOT the same as free cash flow)
- cost_of_revenue: Cost of goods sold / cost of revenue / TAC (traffic acquisition costs)
- capital_expenditures: CapEx, capital expenditures, capital spending
- operating_expenses: OpEx, total operating expenses, SG&A + R&D combined
- research_and_development: R&D expenses/spending
- other: ONLY for metrics not in the above list (balance sheet items, dividends, share buybacks, debt). Use sparingly.

Do NOT extract:
- Vague qualitative statements without numbers ("strong growth", "momentum")
- Headcount, store counts, user counts, device counts, subscriber counts, or non-financial operational metrics
- Statements quoting analyst consensus or third-party data
- Non-financial operational stats (tokens processed, AI model benchmarks, customer satisfaction scores)

FOCUS on income statement metrics (revenue, margins, EPS, net income), cash flow metrics (FCF, operating cash flow, CapEx), and their growth rates. These are the claims we can verify against financial data.

IMPORTANT: Be thorough. A typical earnings call has 10-30 verifiable financial claims. Do not miss revenue, EPS, or margin statements."""

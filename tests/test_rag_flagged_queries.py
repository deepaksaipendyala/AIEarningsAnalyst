"""Tests for flagged-claim query behavior in hybrid retriever."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.rag.index_builder import RAGIndexBuilder
from backend.services.rag.retriever import HybridRetriever, parse_query_entities


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)


def _seed_flagged_dataset(base_dir: Path) -> None:
    _write_json(
        base_dir / "transcripts" / "WMT_Q4_2025.json",
        {
            "ticker": "WMT",
            "year": 2025,
            "quarter": 4,
            "title": "WMT Q4 2025 Earnings Call",
            "text": "Revenue commentary and guidance text.",
            "source": "test",
        },
    )

    _write_json(
        base_dir / "financials" / "WMT_fmp.json",
        {
            "ticker": "WMT",
            "income_statement": [
                {
                    "date": "2025-12-31",
                    "period": "Q4",
                    "fiscalYear": 2025,
                    "revenue": 170000000000.0,
                }
            ],
            "cash_flow": [],
        },
    )

    _write_json(
        base_dir / "verdicts" / "WMT_Q4_2025_verdicts.json",
        {
            "ticker": "WMT",
            "key": "WMT_Q4_2025",
            "year": 2025,
            "quarter": 4,
            "claims_with_verdicts": [
                {
                    "claim": {
                        "claim_id": "claim_mismatch",
                        "period": "Q4 2025",
                        "metric_type": "revenue",
                        "claim_type": "absolute",
                        "speaker": "CFO",
                        "quote_text": "Revenue was 3.2 billion",
                        "claimed_value": 3200000000.0,
                        "claimed_value_raw": "$3.2B",
                    },
                    "verification": {
                        "verdict": "mismatch",
                        "actual_value": 170000000000.0,
                        "computation_detail": "Direct lookup mismatch",
                    },
                },
                {
                    "claim": {
                        "claim_id": "claim_unverifiable",
                        "period": "Q4 2025",
                        "metric_type": "revenue",
                        "claim_type": "guidance",
                        "speaker": "CFO",
                        "quote_text": "Next quarter growth 5% to 6%",
                        "claimed_value": 5.0,
                        "claimed_value_raw": "5% to 6%",
                    },
                    "verification": {
                        "verdict": "unverifiable",
                        "actual_value": None,
                        "explanation": "Guidance claim",
                    },
                },
            ],
            "summary": {
                "total": 2,
                "verified": 0,
                "close_match": 0,
                "mismatch": 1,
                "misleading": 0,
                "unverifiable": 1,
            },
        },
    )


class TestFlaggedEntityParsing:
    def test_flagged_maps_to_mismatch_and_misleading(self):
        entities = parse_query_entities("show flagged claims for WMT")
        assert "mismatch" in entities["verdicts"]
        assert "misleading" in entities["verdicts"]


class TestFlaggedRetrievalFiltering:
    def test_flagged_query_prefers_mismatch_claims(self, tmp_path):
        data_dir = tmp_path / "data"
        db_path = data_dir / "rag" / "knowledge.db"
        _seed_flagged_dataset(data_dir)

        builder = RAGIndexBuilder(data_dir=data_dir, db_path=db_path, chunk_words=80, chunk_overlap=20)
        builder.build(reset=True)

        retriever = HybridRetriever(db_path=db_path)
        result = retriever.search("What are the flagged claims for WMT?", top_k=5)
        rows = result.get("results", [])

        assert rows
        top_text = rows[0].get("text", "")
        assert "Verdict: mismatch" in top_text
        assert rows[0].get("source_type") == "claim_verdict"

"""Regression test: flagged fallback answers should expose verdict labels."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.rag.analyst import AnalystChatbot
from backend.services.rag.index_builder import RAGIndexBuilder
from backend.services.rag.retriever import HybridRetriever


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)


def _seed_dataset(base_dir: Path) -> None:
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
                        "period": "FY 2025",
                        "metric_type": "revenue",
                        "claim_type": "absolute",
                        "speaker": "CFO",
                        "quote_text": "Currency headwind was about 3.2 billion",
                        "claimed_value": 3200000000.0,
                        "claimed_value_raw": "$3.2B",
                    },
                    "verification": {
                        "verdict": "mismatch",
                        "actual_value": 170000000000.0,
                    },
                }
            ],
            "summary": {
                "total": 1,
                "verified": 0,
                "close_match": 0,
                "mismatch": 1,
                "misleading": 0,
                "unverifiable": 0,
            },
        },
    )


class TestFlaggedFallbackAnswer:
    def test_fallback_mentions_mismatch(self, tmp_path):
        data_dir = tmp_path / "data"
        db_path = data_dir / "rag" / "knowledge.db"
        _seed_dataset(data_dir)

        RAGIndexBuilder(data_dir=data_dir, db_path=db_path).build(reset=True)
        bot = AnalystChatbot(retriever=HybridRetriever(db_path=db_path), api_key="")

        resp = bot.ask("What are the flagged claims for WMT?", top_k=4)
        answer = resp.get("answer", "").lower()

        assert "verdict=mismatch" in answer
        assert "flagged summary" in answer

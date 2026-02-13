"""Tests for RAG index build, retrieval quality, and analyst fallback output."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.rag.analyst import AnalystChatbot
from backend.services.rag.index_builder import RAGIndexBuilder, get_index_status
from backend.services.rag.retriever import HybridRetriever, parse_query_entities


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)


def _seed_minimal_dataset(base_dir: Path) -> None:
    _write_json(
        base_dir / "transcripts" / "WMT_Q1_2025.json",
        {
            "ticker": "WMT",
            "year": 2025,
            "quarter": 1,
            "title": "WMT Q1 2025 Earnings Call",
            "text": (
                "Walmart reported quarterly revenue of 150.0 billion dollars. "
                "Operating margin improved to 5.2 percent year over year."
            ),
            "source": "test",
        },
    )

    _write_json(
        base_dir / "verdicts" / "WMT_Q1_2025_verdicts.json",
        {
            "ticker": "WMT",
            "key": "WMT_Q1_2025",
            "year": 2025,
            "quarter": 1,
            "claims_with_verdicts": [
                {
                    "claim": {
                        "claim_id": "claim_1",
                        "period": "Q1 2025",
                        "metric_type": "revenue",
                        "claim_type": "absolute",
                        "speaker": "CFO",
                        "quote_text": "quarterly revenue of 150.0 billion dollars",
                        "claimed_value": 150000000000.0,
                        "claimed_value_raw": "$150.0B",
                    },
                    "verification": {
                        "verdict": "verified",
                        "actual_value": 150000000000.0,
                        "computation_detail": "Direct lookup",
                    },
                }
            ],
            "summary": {
                "total": 1,
                "verified": 1,
                "close_match": 0,
                "mismatch": 0,
                "misleading": 0,
                "unverifiable": 0,
            },
        },
    )

    _write_json(
        base_dir / "financials" / "WMT_fmp.json",
        {
            "ticker": "WMT",
            "income_statement": [
                {
                    "date": "2025-04-30",
                    "period": "Q1",
                    "fiscalYear": 2025,
                    "revenue": 150000000000.0,
                    "netIncome": 5000000000.0,
                    "grossProfit": 36000000000.0,
                    "operatingIncome": 7800000000.0,
                    "eps": 0.62,
                    "epsDiluted": 0.61,
                }
            ],
            "cash_flow": [
                {
                    "date": "2025-04-30",
                    "period": "Q1",
                    "fiscalYear": 2025,
                    "operatingCashFlow": 9000000000.0,
                    "capitalExpenditure": -2500000000.0,
                    "freeCashFlow": 6500000000.0,
                }
            ],
        },
    )


class TestParseQueryEntities:
    def test_extracts_ticker_period_metric(self):
        entities = parse_query_entities("How did WMT revenue in Q1 2025 compare?")
        assert "WMT" in entities["tickers"]
        assert (2025, 1) in entities["periods"]
        assert "revenue" in entities["metrics"]


class TestRAGBuildAndRetrieve:
    def test_build_index_and_retrieve_relevant_chunk(self, tmp_path):
        data_dir = tmp_path / "data"
        db_path = data_dir / "rag" / "knowledge.db"
        _seed_minimal_dataset(data_dir)

        builder = RAGIndexBuilder(data_dir=data_dir, db_path=db_path, chunk_words=80, chunk_overlap=20)
        stats = builder.build(reset=True)

        assert stats["documents"] >= 3
        assert stats["chunks"] >= 3

        status = get_index_status(db_path)
        assert status["exists"] is True
        assert status["documents"] >= 3

        retriever = HybridRetriever(db_path=db_path)
        result = retriever.search("What was WMT revenue in Q1 2025?", top_k=5)

        assert result["results"]
        assert result["results"][0]["ticker"] == "WMT"
        assert any(r["source_type"] == "financial_snapshot" for r in result["results"])


class TestAnalystFallback:
    def test_chatbot_returns_extractive_answer_without_api_key(self, tmp_path):
        data_dir = tmp_path / "data"
        db_path = data_dir / "rag" / "knowledge.db"
        _seed_minimal_dataset(data_dir)

        builder = RAGIndexBuilder(data_dir=data_dir, db_path=db_path, chunk_words=80, chunk_overlap=20)
        builder.build(reset=True)

        retriever = HybridRetriever(db_path=db_path)
        bot = AnalystChatbot(retriever=retriever, api_key="")

        response = bot.ask("Summarize WMT Q1 2025 revenue")

        assert response["model_used"] == "extractive-fallback"
        assert response["sources"]
        assert "[S1]" in response["answer"]
        assert response["citations"]

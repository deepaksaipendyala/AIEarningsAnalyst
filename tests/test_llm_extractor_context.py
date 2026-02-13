"""Tests for SEC/FMP context injection into extraction prompts."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.extraction.llm_extractor import (  # noqa: E402
    _build_user_prompt,
    _extract_source_url_fiscal_hint,
    _render_financial_context,
)


class TestSourceUrlHint:
    def test_extracts_fiscal_period_from_fool_url(self):
        meta = {
            "source_url": (
                "https://www.fool.com/earnings/call-transcripts/2025/05/15/"
                "walmart-wmt-q1-2026-earnings-call-transcript/"
            )
        }
        assert _extract_source_url_fiscal_hint(meta) == (2026, 1)

    def test_returns_none_when_url_not_matching(self):
        meta = {"source_url": "https://example.com/transcript"}
        assert _extract_source_url_fiscal_hint(meta) is None


class TestFinancialContextRendering:
    def test_renders_fmp_alias_and_sec_sections(self):
        fmp_data = {
            (2026, 1): {"revenue": 100.0, "eps_diluted": 1.23},
            (2025, 4): {"revenue": 90.0, "operating_income": 12.0},
            "_calendar_aliases": {(2025, 2): (2026, 1)},
        }
        sec_data = {
            (2025, 2): {"revenue": 88.0, "net_income": 9.0},
            (2024, 2): {"revenue": 80.0},
        }
        transcript_meta = {
            "source_url": (
                "https://www.fool.com/earnings/call-transcripts/2025/05/15/"
                "walmart-wmt-q1-2026-earnings-call-transcript/"
            )
        }

        context = _render_financial_context(
            ticker="WMT",
            transcript_year=2025,
            transcript_quarter=2,
            fmp_data=fmp_data,
            sec_data=sec_data,
            transcript_meta=transcript_meta,
        )

        assert "Transcript file label: Q2 2025" in context
        assert "Source URL fiscal hint: Q1 2026" in context
        assert "FMP periods (metric coverage):" in context
        assert "Q2 2025 -> Q1 2026" in context
        assert "SEC supplemental periods" in context


class TestPromptBuilding:
    def test_financial_context_is_embedded_into_user_prompt(self):
        prompt = _build_user_prompt(
            transcript_text="Revenue was $10 billion.",
            ticker="AAPL",
            year=2025,
            quarter=4,
            chunk_label="part 1/3",
            financial_context="- Context line",
        )

        assert "AAPL, Q4 2025 (part 1/3)" in prompt
        assert "<financial_context>" in prompt
        assert "- Context line" in prompt
        assert "<transcript>" in prompt
        assert "Revenue was $10 billion." in prompt

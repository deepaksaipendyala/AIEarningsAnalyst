"""Tests for mlq.ai fallback helpers in transcript client."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ingestion.transcript_client import (  # noqa: E402
    _extract_mlq_transcript_block,
    _map_to_mlq_fiscal_period,
    _mlq_period_candidates,
)


class TestMlqFiscalMapping:
    def test_nvda_year_offset_mapping(self):
        # NVDA calendar Q3 2025 is typically fiscal Q3 2026.
        assert _map_to_mlq_fiscal_period("NVDA", 2025, 3) == (2026, 3)

    def test_aapl_quarter_shift_mapping(self):
        # AAPL calendar Q4 2025 aligns with fiscal Q1 2026.
        assert _map_to_mlq_fiscal_period("AAPL", 2025, 4) == (2026, 1)

    def test_msft_quarter_shift_mapping(self):
        # MSFT calendar Q4 2025 aligns with fiscal Q2 2026.
        assert _map_to_mlq_fiscal_period("MSFT", 2025, 4) == (2026, 2)


class TestMlqCandidates:
    def test_candidates_include_requested_and_fiscal_mapped(self):
        candidates = _mlq_period_candidates("WMT", 2025, 3)
        assert (2025, 3) in candidates
        assert (2026, 3) in candidates


class TestMlqBlockExtraction:
    def test_extracts_transcript_block(self):
        html = """
        <html><body>
          <div class="card-body blog-post-style">
            <div class="transcript-content">
              <p><strong>Operator</strong>: Welcome everyone</p>
              <p><strong>CEO</strong>: Revenue was strong</p>
            </div>
          </div>
        </body></html>
        """
        block = _extract_mlq_transcript_block(html)
        assert block is not None
        assert "transcript-content" in block
        assert "Revenue was strong" in block

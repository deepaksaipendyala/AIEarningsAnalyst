"""Hybrid retrieval over SQL docs, vector chunks, and lightweight graph links."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.services.rag.index_builder import hash_embed_text, tokenize
from backend.services.verification.metric_catalog import METRIC_CATALOG


_PERIOD_Q_RE = re.compile(r"\bq([1-4])\s*[-/]?\s*(20\d{2})\b", re.IGNORECASE)
_PERIOD_Q_RE_ALT = re.compile(r"\b(20\d{2})\s*q([1-4])\b", re.IGNORECASE)
_PERIOD_FY_RE = re.compile(r"\bfy\s*(20\d{2})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

_METRIC_ALIASES = {
    "revenue": "revenue",
    "sales": "revenue",
    "turnover": "revenue",
    "net income": "net_income",
    "net earnings": "net_income",
    "gross profit": "gross_profit",
    "gross margin": "gross_margin",
    "operating income": "operating_income",
    "operating margin": "operating_margin",
    "ebitda": "ebitda",
    "eps": "eps_diluted",
    "diluted eps": "eps_diluted",
    "basic eps": "eps_basic",
    "free cash flow": "free_cash_flow",
    "operating cash flow": "operating_cash_flow",
    "cost of revenue": "cost_of_revenue",
    "capex": "capital_expenditures",
    "capital expenditure": "capital_expenditures",
    "operating expenses": "operating_expenses",
    "r&d": "research_and_development",
    "research and development": "research_and_development",
    "cash and marketable": "cash_and_marketable_securities",
    "total debt": "total_debt",
    "net cash": "net_cash",
}

_SOURCE_HINTS = {
    "transcript": "transcript",
    "call": "transcript",
    "claim": "claim_verdict",
    "verdict": "claim_verdict",
    "financial": "financial_snapshot",
    "balance sheet": "financial_snapshot",
}

_VERDICT_HINTS = {
    "verified": "verified",
    "close match": "close_match",
    "mismatch": "mismatch",
    "misleading": "misleading",
    "unverifiable": "unverifiable",
    "unverified": "unverifiable",
}

_FLAGGED_HINTS = (
    "flagged",
    "flag",
    "red flag",
    "problematic",
    "incorrect",
    "wrong",
)

_FLAGGED_VERDICTS = {"mismatch", "misleading"}


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    score: float
    score_breakdown: dict[str, float]
    source_type: str
    ticker: str | None
    year: int | None
    quarter: int | None
    period: str | None
    metric: str | None
    title: str
    text: str
    source_path: str | None
    metadata: dict[str, Any]


def _load_known_tickers() -> set[str]:
    path = settings.data_dir / "companies.json"
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            companies = json.load(f)
    except Exception:
        return set()
    return {
        str(c.get("ticker", "")).upper()
        for c in companies
        if c.get("ticker")
    }


def _coerce_period_label(year: int, quarter: int) -> str:
    if quarter == 0:
        return f"FY {year}"
    return f"Q{quarter} {year}"


def parse_query_entities(query: str) -> dict[str, Any]:
    """Parse query for ticker/period/metric/entity hints used by retrieval."""
    text = query or ""
    lower = text.lower()
    upper = text.upper()

    known_tickers = _load_known_tickers()
    tickers = sorted({
        t for t in known_tickers
        if re.search(rf"\b{re.escape(t)}\b", upper)
    })

    periods: list[tuple[int, int]] = []
    period_labels: set[str] = set()

    for m in _PERIOD_Q_RE.finditer(lower):
        q = int(m.group(1))
        y = int(m.group(2))
        periods.append((y, q))
        period_labels.add(_coerce_period_label(y, q))

    for m in _PERIOD_Q_RE_ALT.finditer(lower):
        y = int(m.group(1))
        q = int(m.group(2))
        periods.append((y, q))
        period_labels.add(_coerce_period_label(y, q))

    for m in _PERIOD_FY_RE.finditer(lower):
        y = int(m.group(1))
        periods.append((y, 0))
        period_labels.add(_coerce_period_label(y, 0))

    # If a year is present without an explicit quarter, keep it as a soft hint.
    years = sorted({int(m.group(1)) for m in _YEAR_RE.finditer(lower)})

    metrics = set()
    for phrase, metric in _METRIC_ALIASES.items():
        if phrase in lower:
            metrics.add(metric)

    for metric in METRIC_CATALOG:
        phrase = metric.replace("_", " ")
        if phrase in lower:
            metrics.add(metric)

    source_types = sorted({
        st for hint, st in _SOURCE_HINTS.items()
        if hint in lower
    })

    verdict_set = {
        v for hint, v in _VERDICT_HINTS.items()
        if hint in lower
    }
    if any(hint in lower for hint in _FLAGGED_HINTS):
        verdict_set.update(_FLAGGED_VERDICTS)
    verdicts = sorted(verdict_set)

    asks_latest = any(
        tok in lower
        for tok in ("latest", "most recent", "last quarter", "recent quarter", "current quarter")
    )
    asks_comparison = any(tok in lower for tok in ("compare", "vs", "versus", "relative to"))

    return {
        "tickers": tickers,
        "periods": sorted(set(periods)),
        "period_labels": sorted(period_labels),
        "years": years,
        "metrics": sorted(metrics),
        "source_types": source_types,
        "verdicts": verdicts,
        "asks_latest": asks_latest,
        "asks_comparison": asks_comparison,
    }


class HybridRetriever:
    """Hybrid retriever using BM25 + hash-embeddings + graph/entity boosts."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else settings.rag_db_path

        self._loaded = False
        self._chunks: list[dict[str, Any]] = []
        self._df: dict[str, int] = {}
        self._avg_len = 1.0
        self._chunk_nodes: dict[str, set[str]] = {}
        self._node_meta: dict[str, tuple[str, str]] = {}
        self._edges: set[tuple[str, str]] = set()
        self._latest_period_by_ticker: dict[str, tuple[int, int]] = {}

    def refresh(self) -> None:
        self._loaded = False
        self._chunks = []
        self._df = {}
        self._avg_len = 1.0
        self._chunk_nodes = {}
        self._node_meta = {}
        self._edges = set()
        self._latest_period_by_ticker = {}

    def is_ready(self) -> bool:
        self._ensure_loaded()
        return bool(self._chunks)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.db_path.exists():
            return

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                  c.chunk_id,
                  c.doc_id,
                  c.text,
                  c.token_count,
                  c.embedding_json,
                  d.source_type,
                  d.ticker,
                  d.year,
                  d.quarter,
                  d.period,
                  d.metric,
                  d.title,
                  d.source_path,
                  d.metadata_json
                FROM chunks c
                JOIN documents d ON d.doc_id = c.doc_id
                """
            ).fetchall()

            self._chunks = []
            df_counts: dict[str, int] = {}
            total_len = 0

            for row in rows:
                text = row["text"] or ""
                tokens = tokenize(text)
                tf: dict[str, int] = {}
                for tok in tokens:
                    tf[tok] = tf.get(tok, 0) + 1

                for tok in tf.keys():
                    df_counts[tok] = df_counts.get(tok, 0) + 1

                total_len += max(1, len(tokens))

                metadata = {}
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    metadata = {}

                embedding = []
                try:
                    embedding = json.loads(row["embedding_json"] or "[]")
                except Exception:
                    embedding = []

                chunk = {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "text": text,
                    "token_count": int(row["token_count"] or len(tokens)),
                    "tokens": tokens,
                    "tf": tf,
                    "embedding": embedding,
                    "source_type": row["source_type"],
                    "ticker": row["ticker"],
                    "year": row["year"],
                    "quarter": row["quarter"],
                    "period": row["period"],
                    "metric": row["metric"],
                    "title": row["title"] or row["doc_id"],
                    "source_path": row["source_path"],
                    "metadata": metadata,
                }
                self._chunks.append(chunk)

                ticker = row["ticker"]
                year = row["year"]
                quarter = row["quarter"]
                if ticker and isinstance(year, int) and isinstance(quarter, int):
                    current = self._latest_period_by_ticker.get(ticker)
                    candidate = (year, quarter)
                    if current is None or candidate > current:
                        self._latest_period_by_ticker[ticker] = candidate

            self._df = df_counts
            self._avg_len = (total_len / max(1, len(self._chunks))) if self._chunks else 1.0

            node_rows = conn.execute("SELECT node_id, node_type, label FROM nodes").fetchall()
            self._node_meta = {
                r["node_id"]: (r["node_type"], r["label"]) for r in node_rows
            }

            cn_rows = conn.execute("SELECT chunk_id, node_id FROM chunk_nodes").fetchall()
            self._chunk_nodes = {}
            for r in cn_rows:
                self._chunk_nodes.setdefault(r["chunk_id"], set()).add(r["node_id"])

            edge_rows = conn.execute("SELECT from_node, to_node FROM edges").fetchall()
            self._edges = {(r["from_node"], r["to_node"]) for r in edge_rows}

    def _bm25(self, q_tokens: list[str], chunk: dict[str, Any]) -> float:
        if not q_tokens:
            return 0.0

        score = 0.0
        k1 = 1.2
        b = 0.75
        n_docs = max(1, len(self._chunks))
        dl = max(1, chunk.get("token_count") or len(chunk.get("tokens", [])) or 1)

        for tok in set(q_tokens):
            tf = chunk["tf"].get(tok, 0)
            if tf == 0:
                continue
            df = self._df.get(tok, 0)
            if df == 0:
                continue

            idf = math.log(1 + ((n_docs - df + 0.5) / (df + 0.5)))
            denom = tf + k1 * (1 - b + b * (dl / max(1e-9, self._avg_len)))
            score += idf * ((tf * (k1 + 1)) / max(denom, 1e-9))

        return score

    def _cosine(self, a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        dim = min(len(a), len(b))
        if dim <= 0:
            return 0.0
        dot = 0.0
        for i in range(dim):
            dot += a[i] * b[i]
        return max(-1.0, min(1.0, dot))

    def _chunk_node_labels(self, chunk_id: str) -> dict[str, set[str]]:
        labels: dict[str, set[str]] = {}
        for nid in self._chunk_nodes.get(chunk_id, set()):
            node_type, label = self._node_meta.get(nid, ("", ""))
            if not node_type:
                continue
            labels.setdefault(node_type, set()).add((label or "").lower())
        return labels

    def _entity_boost(self, chunk: dict[str, Any], entities: dict[str, Any]) -> float:
        boost = 0.0
        node_labels = self._chunk_node_labels(chunk["chunk_id"])

        tickers = set(entities.get("tickers") or [])
        if tickers:
            if chunk.get("ticker") in tickers:
                boost += 0.45
            elif chunk.get("ticker"):
                boost -= 0.08

        periods = set(tuple(p) for p in (entities.get("periods") or []))
        if periods:
            yq = (chunk.get("year"), chunk.get("quarter"))
            if yq in periods:
                boost += 0.22
            elif chunk.get("period") and chunk.get("period").lower() in {
                p.lower() for p in entities.get("period_labels", [])
            }:
                boost += 0.22

        years = set(entities.get("years") or [])
        if years and chunk.get("year") in years:
            boost += 0.08

        metrics = set(entities.get("metrics") or [])
        if metrics:
            chunk_metric = (chunk.get("metric") or "").lower()
            if chunk_metric in metrics:
                boost += 0.22
            elif metrics.intersection(node_labels.get("metric", set())):
                boost += 0.14

        verdicts = set(entities.get("verdicts") or [])
        if verdicts and verdicts.intersection(node_labels.get("verdict", set())):
            boost += 0.10

        source_types = set(entities.get("source_types") or [])
        if source_types and chunk.get("source_type") in source_types:
            boost += 0.08

        # Graph association boost when query contains both ticker and metric.
        if tickers and metrics:
            for ticker in tickers:
                ticker_node = f"ticker:{ticker.lower()}"
                for metric in metrics:
                    metric_node = f"metric:{metric.lower()}"
                    if (ticker_node, metric_node) in self._edges:
                        boost += 0.06
                        break

        return max(0.0, min(boost, 1.0))

    def _prior_boost(self, chunk: dict[str, Any], entities: dict[str, Any]) -> float:
        boost = 0.0

        source_type = chunk.get("source_type")
        if source_type == "financial_snapshot":
            boost += 0.05
        elif source_type == "claim_verdict":
            boost += 0.06
        elif source_type == "transcript":
            boost += 0.03

        if entities.get("asks_latest"):
            ticker = chunk.get("ticker")
            y = chunk.get("year")
            q = chunk.get("quarter")
            latest = self._latest_period_by_ticker.get(ticker) if ticker else None
            if latest and (y, q) == latest:
                boost += 0.08

        return boost

    def search(
        self,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.time()
        self._ensure_loaded()

        if not self._chunks:
            return {
                "results": [],
                "query_entities": parse_query_entities(query),
                "latency_ms": int((time.time() - start) * 1000),
                "candidates": 0,
            }

        query_entities = parse_query_entities(query)
        q_tokens = tokenize(query)
        q_vec = hash_embed_text(query)

        candidates = self._chunks
        explicit_tickers = set(query_entities.get("tickers") or [])
        if explicit_tickers:
            candidates = [
                c for c in candidates
                if (c.get("ticker") in explicit_tickers)
            ]

        if filters:
            if filters.get("ticker"):
                t = str(filters["ticker"]).upper()
                candidates = [c for c in candidates if c.get("ticker") == t]
            if filters.get("source_type"):
                st = str(filters["source_type"]).lower()
                candidates = [c for c in candidates if c.get("source_type") == st]
            if filters.get("year") is not None:
                year = int(filters["year"])
                candidates = [c for c in candidates if c.get("year") == year]
            if filters.get("quarter") is not None:
                quarter = int(filters["quarter"])
                candidates = [c for c in candidates if c.get("quarter") == quarter]

        desired_verdicts = set(query_entities.get("verdicts") or [])
        if desired_verdicts:
            verdict_filtered = []
            for chunk in candidates:
                if chunk.get("source_type") != "claim_verdict":
                    continue
                labels = self._chunk_node_labels(chunk["chunk_id"])
                chunk_verdicts = labels.get("verdict", set())
                if desired_verdicts.intersection(chunk_verdicts):
                    verdict_filtered.append(chunk)
            if verdict_filtered:
                candidates = verdict_filtered

        scored: list[RetrievedChunk] = []
        for chunk in candidates:
            lexical = self._bm25(q_tokens, chunk)
            lexical_norm = min(1.0, lexical / 12.0)
            dense = (self._cosine(q_vec, chunk.get("embedding") or []) + 1.0) / 2.0
            entity = self._entity_boost(chunk, query_entities)
            prior = self._prior_boost(chunk, query_entities)

            final_score = (0.45 * dense) + (0.35 * lexical_norm) + (0.20 * entity) + prior
            if final_score < 0.05:
                continue

            scored.append(
                RetrievedChunk(
                    chunk_id=chunk["chunk_id"],
                    doc_id=chunk["doc_id"],
                    score=float(final_score),
                    score_breakdown={
                        "dense": round(dense, 4),
                        "lexical": round(lexical_norm, 4),
                        "entity": round(entity, 4),
                        "prior": round(prior, 4),
                    },
                    source_type=chunk.get("source_type") or "unknown",
                    ticker=chunk.get("ticker"),
                    year=chunk.get("year"),
                    quarter=chunk.get("quarter"),
                    period=chunk.get("period"),
                    metric=chunk.get("metric"),
                    title=chunk.get("title") or chunk["doc_id"],
                    text=chunk.get("text") or "",
                    source_path=chunk.get("source_path"),
                    metadata=chunk.get("metadata") or {},
                )
            )

        scored.sort(key=lambda x: x.score, reverse=True)

        # Diversity control: keep at most 2 chunks per document to avoid repetition.
        selected: list[RetrievedChunk] = []
        per_doc: dict[str, int] = {}
        for row in scored:
            if per_doc.get(row.doc_id, 0) >= 2:
                continue
            selected.append(row)
            per_doc[row.doc_id] = per_doc.get(row.doc_id, 0) + 1
            if len(selected) >= top_k:
                break

        results = []
        for i, item in enumerate(selected, start=1):
            results.append({
                "source_id": f"S{i}",
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "score": round(item.score, 4),
                "score_breakdown": item.score_breakdown,
                "source_type": item.source_type,
                "ticker": item.ticker,
                "year": item.year,
                "quarter": item.quarter,
                "period": item.period,
                "metric": item.metric,
                "title": item.title,
                "text": item.text,
                "source_path": item.source_path,
                "metadata": item.metadata,
            })

        return {
            "results": results,
            "query_entities": query_entities,
            "latency_ms": int((time.time() - start) * 1000),
            "candidates": len(candidates),
        }

    def retrieve(
        self,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.search(query=query, top_k=top_k, filters=filters).get("results", [])

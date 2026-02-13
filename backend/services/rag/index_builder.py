"""Build a hybrid RAG knowledge index from project datasets.

The index stores:
1) SQL document/chunk metadata
2) Dense vectors (deterministic hash embeddings)
3) Graph links between chunks and entities (ticker/period/metric/verdict)
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from backend.config import settings
from backend.services.ingestion.fmp_client import load_fmp_data


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.$%_/-]*")


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")]


def hash_embed_text(text: str, dim: int = 384) -> list[float]:
    """Deterministic local embedding (no external dependency)."""
    vec = [0.0] * dim
    if not text:
        return vec

    tf: dict[str, int] = {}
    for tok in tokenize(text):
        tf[tok] = tf.get(tok, 0) + 1

    for tok, freq in tf.items():
        h = hashlib.blake2b(tok.encode("utf-8"), digest_size=16).digest()
        idx = int.from_bytes(h[:8], "big") % dim
        sign = 1.0 if (h[8] % 2 == 0) else -1.0
        weight = 1.0 + math.log(freq)
        vec[idx] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def chunk_text(text: str, max_words: int, overlap_words: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]

    step = max(1, max_words - overlap_words)
    chunks = []
    for start in range(0, len(words), step):
        end = min(len(words), start + max_words)
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(words):
            break
    return chunks


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _node_id(node_type: str, label: str) -> str:
    return f"{node_type}:{_safe_slug(label)}"


@dataclass
class RAGDocument:
    doc_id: str
    source_type: str
    ticker: str | None
    year: int | None
    quarter: int | None
    period: str | None
    metric: str | None
    title: str
    text: str
    source_path: str
    metadata: dict
    entities: list[tuple[str, str]]


class RAGIndexBuilder:
    """Builds/refreshes the project RAG index."""

    def __init__(
        self,
        data_dir: Path | None = None,
        db_path: Path | None = None,
        chunk_words: int | None = None,
        chunk_overlap: int | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else settings.data_dir
        self.db_path = Path(db_path) if db_path else settings.rag_db_path
        self.chunk_words = int(chunk_words or settings.rag_chunk_words)
        self.chunk_overlap = int(chunk_overlap or settings.rag_chunk_overlap)

        self.transcripts_dir = self.data_dir / "transcripts"
        self.verdicts_dir = self.data_dir / "verdicts"
        self.financials_dir = self.data_dir / "financials"

    def build(self, reset: bool = True) -> dict:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if reset and self.db_path.exists():
            self.db_path.unlink()

        with sqlite3.connect(self.db_path) as conn:
            self._create_schema(conn)

            stats = {
                "documents": 0,
                "chunks": 0,
                "nodes": 0,
                "edges": 0,
            }

            for doc in self._iter_documents():
                inserted = self._insert_document(conn, doc)
                stats["documents"] += 1
                stats["chunks"] += inserted["chunks"]
                stats["nodes"] += inserted["new_nodes"]
                stats["edges"] += inserted["edges"]

            self._upsert_meta(conn, "built_at", datetime.now().isoformat())
            self._upsert_meta(conn, "documents", str(stats["documents"]))
            self._upsert_meta(conn, "chunks", str(stats["chunks"]))
            self._upsert_meta(conn, "nodes", str(self._count(conn, "nodes")))
            self._upsert_meta(conn, "edges", str(self._count(conn, "edges")))
            conn.commit()

        stats["nodes"] = int(get_index_status(self.db_path).get("nodes", 0))
        stats["edges"] = int(get_index_status(self.db_path).get("edges", 0))
        stats["db_path"] = str(self.db_path)
        return stats

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
              doc_id TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              ticker TEXT,
              year INTEGER,
              quarter INTEGER,
              period TEXT,
              metric TEXT,
              title TEXT,
              text TEXT NOT NULL,
              source_path TEXT,
              metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS chunks (
              chunk_id TEXT PRIMARY KEY,
              doc_id TEXT NOT NULL,
              chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              token_count INTEGER NOT NULL,
              embedding_json TEXT NOT NULL,
              FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
            );

            CREATE TABLE IF NOT EXISTS nodes (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              label TEXT NOT NULL,
              metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS edges (
              edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
              from_node TEXT NOT NULL,
              to_node TEXT NOT NULL,
              relation TEXT NOT NULL,
              weight REAL DEFAULT 1.0,
              evidence_chunk_id TEXT
            );

            CREATE TABLE IF NOT EXISTS chunk_nodes (
              chunk_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              PRIMARY KEY (chunk_id, node_id)
            );

            CREATE TABLE IF NOT EXISTS index_meta (
              key TEXT PRIMARY KEY,
              value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_docs_ticker_period ON documents (ticker, year, quarter);
            CREATE INDEX IF NOT EXISTS idx_docs_metric ON documents (metric);
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);
            CREATE INDEX IF NOT EXISTS idx_chunk_nodes_node ON chunk_nodes (node_id);
            CREATE INDEX IF NOT EXISTS idx_edges_from_to ON edges (from_node, to_node);
            """
        )

    def _upsert_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO index_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _count(self, conn: sqlite3.Connection, table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0] if row else 0)

    def _insert_document(self, conn: sqlite3.Connection, doc: RAGDocument) -> dict:
        conn.execute(
            """
            INSERT OR REPLACE INTO documents(
              doc_id, source_type, ticker, year, quarter, period, metric,
              title, text, source_path, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.doc_id,
                doc.source_type,
                doc.ticker,
                doc.year,
                doc.quarter,
                doc.period,
                doc.metric,
                doc.title,
                doc.text,
                doc.source_path,
                json.dumps(doc.metadata, ensure_ascii=True),
            ),
        )

        entity_nodes: list[str] = []
        new_nodes = 0
        for node_type, label in doc.entities:
            if not label:
                continue
            nid = _node_id(node_type, label)
            before = conn.execute(
                "SELECT 1 FROM nodes WHERE node_id = ?",
                (nid,),
            ).fetchone()
            if before is None:
                new_nodes += 1
            conn.execute(
                """
                INSERT OR IGNORE INTO nodes(node_id, node_type, label, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (nid, node_type, label, json.dumps({"label": label}, ensure_ascii=True)),
            )
            entity_nodes.append(nid)

        chunks = chunk_text(doc.text, self.chunk_words, self.chunk_overlap)
        if not chunks:
            chunks = [doc.text]

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc.doc_id}:{i}"
            embedding = hash_embed_text(chunk)
            conn.execute(
                """
                INSERT OR REPLACE INTO chunks(chunk_id, doc_id, chunk_index, text, token_count, embedding_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    doc.doc_id,
                    i,
                    chunk,
                    len(tokenize(chunk)),
                    json.dumps(embedding),
                ),
            )
            for nid in entity_nodes:
                conn.execute(
                    "INSERT OR IGNORE INTO chunk_nodes(chunk_id, node_id) VALUES (?, ?)",
                    (chunk_id, nid),
                )

        # Add lightweight graph edges for entity co-occurrence in this document.
        edges = 0
        ticker_node = next((n for n in entity_nodes if n.startswith("ticker:")), None)
        period_node = next((n for n in entity_nodes if n.startswith("period:")), None)
        chunk_ref = f"{doc.doc_id}:0"
        if ticker_node:
            for nid in entity_nodes:
                if nid == ticker_node:
                    continue
                conn.execute(
                    """
                    INSERT INTO edges(from_node, to_node, relation, weight, evidence_chunk_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (ticker_node, nid, "mentions", 1.0, chunk_ref),
                )
                edges += 1
        if period_node:
            for nid in entity_nodes:
                if nid == period_node or nid.startswith("ticker:"):
                    continue
                conn.execute(
                    """
                    INSERT INTO edges(from_node, to_node, relation, weight, evidence_chunk_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (period_node, nid, "period_association", 1.0, chunk_ref),
                )
                edges += 1

        return {"chunks": len(chunks), "new_nodes": new_nodes, "edges": edges}

    def _iter_documents(self) -> Iterable[RAGDocument]:
        yield from self._iter_transcript_docs()
        yield from self._iter_claim_verdict_docs()
        yield from self._iter_financial_docs()

    def _iter_transcript_docs(self) -> Iterable[RAGDocument]:
        if not self.transcripts_dir.exists():
            return
        for path in sorted(self.transcripts_dir.glob("*_Q*_*.json")):
            with open(path) as f:
                data = json.load(f)
            text = (data.get("text") or "").strip()
            if not text:
                continue
            ticker = (data.get("ticker") or path.stem.split("_")[0]).upper()
            year = int(data.get("year") or path.stem.split("_")[-1])
            quarter = int(data.get("quarter") or path.stem.split("_")[1].replace("Q", ""))
            period = f"Q{quarter} {year}"
            entities = [
                ("ticker", ticker),
                ("period", period),
                ("source", str(data.get("source") or "transcript")),
            ]
            yield RAGDocument(
                doc_id=f"transcript:{path.stem}",
                source_type="transcript",
                ticker=ticker,
                year=year,
                quarter=quarter,
                period=period,
                metric=None,
                title=str(data.get("title") or f"{ticker} {period} transcript"),
                text=text,
                source_path=str(path),
                metadata={
                    "call_date": data.get("call_date"),
                    "source": data.get("source"),
                    "source_url": data.get("source_url"),
                },
                entities=entities,
            )

    def _iter_claim_verdict_docs(self) -> Iterable[RAGDocument]:
        if not self.verdicts_dir.exists():
            return
        for path in sorted(self.verdicts_dir.glob("*_verdicts.json")):
            with open(path) as f:
                data = json.load(f)

            ticker = (data.get("ticker") or path.stem.split("_")[0]).upper()
            year = data.get("year")
            quarter = data.get("quarter")
            file_period = f"Q{quarter} {year}" if year and quarter else None
            key = data.get("key", path.stem.replace("_verdicts", ""))

            for row in data.get("claims_with_verdicts", []):
                claim = row.get("claim", {})
                verification = row.get("verification", {})
                claim_id = claim.get("claim_id") or f"claim_{hashlib.md5(str(row).encode()).hexdigest()[:8]}"
                period = claim.get("period") or file_period
                metric = claim.get("metric_type")
                verdict = verification.get("verdict", "unverifiable")
                claim_type = claim.get("claim_type", "other")

                pieces = [
                    f"Ticker: {ticker}",
                    f"Period: {period}",
                    f"Metric: {metric}",
                    f"Claim type: {claim_type}",
                    f"Speaker: {claim.get('speaker', 'Unknown')}",
                    f"Quote: {claim.get('quote_text', '')}",
                    f"Claimed value: {claim.get('claimed_value_raw', claim.get('claimed_value'))}",
                    f"Verdict: {verdict}",
                ]
                if verification.get("actual_value") is not None:
                    pieces.append(f"Actual value: {verification.get('actual_value')}")
                if verification.get("computation_detail"):
                    pieces.append(f"Computation: {verification.get('computation_detail')}")
                if verification.get("explanation"):
                    pieces.append(f"Explanation: {verification.get('explanation')}")
                text = "\n".join(str(x) for x in pieces if x)

                entities = [
                    ("ticker", ticker),
                    ("period", period or ""),
                    ("metric", metric or "other"),
                    ("claim_type", claim_type),
                    ("verdict", verdict),
                ]
                yield RAGDocument(
                    doc_id=f"claim:{key}:{claim_id}",
                    source_type="claim_verdict",
                    ticker=ticker,
                    year=year,
                    quarter=quarter,
                    period=period,
                    metric=metric,
                    title=f"{ticker} {period} {metric} claim {claim_id}",
                    text=text,
                    source_path=str(path),
                    metadata={
                        "claim_id": claim_id,
                        "key": key,
                        "quote_text": claim.get("quote_text"),
                        "speaker": claim.get("speaker"),
                        "verdict": verdict,
                    },
                    entities=entities,
                )

    def _iter_financial_docs(self) -> Iterable[RAGDocument]:
        if not self.financials_dir.exists():
            return
        for path in sorted(self.financials_dir.glob("*_fmp.json")):
            ticker = path.stem.replace("_fmp", "").upper()
            indexed = load_fmp_data(
                ticker,
                financials_dir=self.financials_dir,
                sec_dir=self.data_dir / "sec",
                enable_sec_fallback=False,
            )
            for yq in sorted(k for k in indexed.keys() if isinstance(k, tuple)):
                year, quarter = yq
                row = indexed.get(yq, {})
                metrics = {
                    k: v for k, v in row.items()
                    if not k.startswith("_") and isinstance(v, (int, float))
                }
                if not metrics:
                    continue

                period = f"Q{quarter} {year}"
                metric_lines = [f"{m}: {v}" for m, v in sorted(metrics.items())]
                text = (
                    f"Financial snapshot for {ticker} {period}.\n"
                    + "\n".join(metric_lines)
                )
                entities = [("ticker", ticker), ("period", period)]
                entities.extend(("metric", m) for m in metrics.keys())
                yield RAGDocument(
                    doc_id=f"financial:{ticker}:{year}:Q{quarter}",
                    source_type="financial_snapshot",
                    ticker=ticker,
                    year=year,
                    quarter=quarter,
                    period=period,
                    metric=None,
                    title=f"{ticker} financial snapshot {period}",
                    text=text,
                    source_path=str(path),
                    metadata={"metric_count": len(metrics)},
                    entities=entities,
                )


def get_index_status(db_path: Path | None = None) -> dict:
    path = Path(db_path) if db_path else settings.rag_db_path
    if not path.exists():
        return {"exists": False, "db_path": str(path)}

    with sqlite3.connect(path) as conn:
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        meta_rows = conn.execute("SELECT key, value FROM index_meta").fetchall()
        meta = {k: v for k, v in meta_rows}

    return {
        "exists": True,
        "db_path": str(path),
        "documents": int(doc_count),
        "chunks": int(chunk_count),
        "nodes": int(node_count),
        "edges": int(edge_count),
        "built_at": meta.get("built_at"),
    }

"""AI Analyst chat service with grounded RAG responses and citations."""

from __future__ import annotations

import re
from typing import Any

from backend.config import settings
from backend.services.rag.retriever import HybridRetriever


_SYSTEM_PROMPT = """You are an equity research analyst assistant.
Use ONLY the provided sources. Do not invent numbers or facts.
When you make a factual statement, cite one or more sources like [S1], [S2].
If evidence is insufficient, say so explicitly and ask for a narrower question.
Prefer concise, high-signal analysis and call out uncertainty.
"""

_CITATION_RE = re.compile(r"\[(S\d+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_KEY_VALUE_LINE_RE = re.compile(r"^([A-Za-z_ ]+):\s*(.*)$")
_INLINE_VERDICT_RE = re.compile(r"\bVerdict:\s*([A-Za-z_]+)", re.IGNORECASE)
_INLINE_METRIC_RE = re.compile(r"\bMetric:\s*([A-Za-z_]+)", re.IGNORECASE)
_INLINE_QUOTE_RE = re.compile(
    r"\bQuote:\s*(.+?)(?:\s+\bClaimed value:|\s+\bVerdict:|$)",
    re.IGNORECASE,
)


class AnalystChatbot:
    """Grounded QA over indexed earnings data using hybrid retrieval."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.retriever = retriever or HybridRetriever()
        self.model = model or settings.rag_generation_model
        self.api_key = settings.openrouter_api_key if api_key is None else api_key

    def ask(
        self,
        question: str,
        top_k: int = 8,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        question = (question or "").strip()
        if not question:
            return {
                "question": "",
                "answer": "Please provide a question.",
                "sources": [],
                "citations": [],
                "model_used": "none",
                "retrieval": {"results": 0},
            }

        search = self.retriever.search(question, top_k=top_k, filters=filters)
        sources = search.get("results", [])
        if not sources:
            return {
                "question": question,
                "answer": "No indexed evidence was found. Build the RAG index and retry.",
                "sources": [],
                "citations": [],
                "model_used": "none",
                "retrieval": {
                    "results": 0,
                    "latency_ms": search.get("latency_ms", 0),
                    "query_entities": search.get("query_entities", {}),
                },
            }

        context = self._build_context(sources)
        answer, model_used = self._generate_answer(question, context, history, sources)

        valid_ids = {s["source_id"] for s in sources}
        cited_ids = [c for c in _CITATION_RE.findall(answer) if c in valid_ids]
        if not cited_ids:
            cited_ids = [s["source_id"] for s in sources[: min(3, len(sources))]]

        return {
            "question": question,
            "answer": answer,
            "sources": sources,
            "citations": sorted(set(cited_ids), key=lambda x: int(x[1:])),
            "model_used": model_used,
            "retrieval": {
                "results": len(sources),
                "latency_ms": search.get("latency_ms", 0),
                "candidates": search.get("candidates", 0),
                "query_entities": search.get("query_entities", {}),
            },
        }

    def _build_context(self, sources: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for source in sources:
            sid = source["source_id"]
            title = source.get("title") or source.get("doc_id")
            ticker = source.get("ticker") or "N/A"
            period = source.get("period") or "N/A"
            source_type = source.get("source_type") or "unknown"
            metric = source.get("metric") or "n/a"
            snippet = self._compact_text(source.get("text", ""), limit=700)

            lines.append(
                f"[{sid}] title={title} | ticker={ticker} | period={period} | source={source_type} | metric={metric}\n"
                f"{snippet}"
            )

        return "\n\n".join(lines)

    def _generate_answer(
        self,
        question: str,
        context: str,
        history: list[dict[str, str]] | None,
        sources: list[dict[str, Any]],
    ) -> tuple[str, str]:
        if not self.api_key:
            return (self._fallback_answer(question, sources), "extractive-fallback")

        try:
            from openai import OpenAI
        except Exception:
            return (self._fallback_answer(question, sources), "extractive-fallback")

        messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]

        for m in (history or [])[-6:]:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})

        user_prompt = (
            "Question:\n"
            f"{question}\n\n"
            "Retrieved sources:\n"
            f"{context}\n\n"
            "Instructions:\n"
            "- Answer directly and cite sources inline as [S#].\n"
            "- If multiple periods or companies are referenced, separate them clearly.\n"
            "- If evidence is conflicting or incomplete, state that explicitly.\n"
            "- Keep the response under 220 words."
        )
        messages.append({"role": "user", "content": user_prompt})

        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key,
            )
            response = client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=900,
                messages=messages,
            )
            text = response.choices[0].message.content or ""
            text = text.strip()
            if text:
                return (text, self.model)
        except Exception:
            pass

        return (self._fallback_answer(question, sources), "extractive-fallback")

    def _fallback_answer(self, question: str, sources: list[dict[str, Any]]) -> str:
        q_lower = question.lower()
        asks_flagged = any(tok in q_lower for tok in ("flagged", "mismatch", "misleading"))

        lines = [
            "I could not run the generation model, so here is extractive evidence from the top retrieved sources.",
            f"Question: {question}",
        ]

        verdict_counts: dict[str, int] = {}
        for source in sources[: min(8, len(sources))]:
            sid = source["source_id"]
            ticker = source.get("ticker") or "N/A"
            period = source.get("period") or "N/A"
            parsed = self._parse_claim_source_text(source.get("text", ""))
            verdict = parsed.get("verdict")
            metric = parsed.get("metric")
            quote = parsed.get("quote")

            if verdict:
                verdict_key = verdict.lower()
                verdict_counts[verdict_key] = verdict_counts.get(verdict_key, 0) + 1

            if verdict or metric or quote:
                parts = [f"[{sid}] {ticker} {period}"]
                if verdict:
                    parts.append(f"verdict={verdict}")
                if metric:
                    parts.append(f"metric={metric}")
                if quote:
                    parts.append(f"quote={self._compact_text(quote, limit=120)}")
                lines.append("- " + " | ".join(parts))
            else:
                sentence = self._key_sentence(source.get("text", ""))
                lines.append(f"- [{sid}] {ticker} {period}: {sentence}")

        if asks_flagged and verdict_counts:
            mismatch = verdict_counts.get("mismatch", 0)
            misleading = verdict_counts.get("misleading", 0)
            lines.append(
                "Flagged summary from retrieved evidence: "
                f"mismatch={mismatch}, misleading={misleading}."
            )

        lines.append("Use these citations to inspect the exact underlying evidence.")
        return "\n".join(lines)

    def _parse_claim_source_text(self, text: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _KEY_VALUE_LINE_RE.match(line)
            if not match:
                continue
            key = match.group(1).strip().lower().replace(" ", "_")
            value = match.group(2).strip()
            if key in {"verdict", "metric", "quote", "period"} and value:
                parsed[key] = value

        blob = (text or "").strip()
        if "verdict" not in parsed:
            verdict_match = _INLINE_VERDICT_RE.search(blob)
            if verdict_match:
                parsed["verdict"] = verdict_match.group(1).strip()
        if "metric" not in parsed:
            metric_match = _INLINE_METRIC_RE.search(blob)
            if metric_match:
                parsed["metric"] = metric_match.group(1).strip()
        if "quote" not in parsed:
            quote_match = _INLINE_QUOTE_RE.search(blob)
            if quote_match:
                parsed["quote"] = quote_match.group(1).strip()
        return parsed

    def _compact_text(self, text: str, limit: int = 700) -> str:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _key_sentence(self, text: str) -> str:
        compact = self._compact_text(text, limit=400)
        parts = _SENTENCE_SPLIT_RE.split(compact)
        if not parts:
            return compact
        for part in parts:
            if any(ch.isdigit() for ch in part):
                return part.strip()
        return parts[0].strip()

"""API routes for RAG indexing and AI analyst chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services.rag import (
    AnalystChatbot,
    HybridRetriever,
    RAGIndexBuilder,
    get_index_status,
)


router = APIRouter()


class BuildIndexRequest(BaseModel):
    reset: bool = True


class RetrievalRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=20)
    filters: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=20)
    filters: dict[str, Any] | None = None
    history: list[ChatMessage] | None = None


_RETRIEVER: HybridRetriever | None = None
_CHATBOT: AnalystChatbot | None = None


def _get_retriever(refresh: bool = False) -> HybridRetriever:
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = HybridRetriever()
    if refresh:
        _RETRIEVER.refresh()
    return _RETRIEVER


def _get_chatbot(refresh: bool = False) -> AnalystChatbot:
    global _CHATBOT
    if _CHATBOT is None:
        _CHATBOT = AnalystChatbot(retriever=_get_retriever())
    if refresh:
        _CHATBOT.retriever.refresh()
    return _CHATBOT


@router.get("/analyst/index/status")
def analyst_index_status() -> dict[str, Any]:
    status = get_index_status()
    status["retriever_ready"] = _get_retriever().is_ready() if status.get("exists") else False
    return status


@router.post("/analyst/index/build")
def analyst_index_build(request: BuildIndexRequest) -> dict[str, Any]:
    try:
        builder = RAGIndexBuilder()
        build_stats = builder.build(reset=request.reset)
        _get_retriever(refresh=True)
        _get_chatbot(refresh=True)
        return {
            "ok": True,
            "build": build_stats,
            "status": get_index_status(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RAG index build failed: {exc}") from exc


@router.post("/analyst/retrieve")
def analyst_retrieve(request: RetrievalRequest) -> dict[str, Any]:
    retriever = _get_retriever()
    if not retriever.is_ready():
        raise HTTPException(status_code=404, detail="RAG index not found or empty. Build the index first.")
    return retriever.search(request.question, top_k=request.top_k, filters=request.filters)


@router.post("/analyst/chat")
def analyst_chat(request: ChatRequest) -> dict[str, Any]:
    chatbot = _get_chatbot()
    if not chatbot.retriever.is_ready():
        raise HTTPException(status_code=404, detail="RAG index not found or empty. Build the index first.")

    history = [m.model_dump() for m in (request.history or [])]
    return chatbot.ask(
        question=request.question,
        top_k=request.top_k,
        history=history,
        filters=request.filters,
    )

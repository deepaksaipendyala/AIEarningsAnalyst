"""Application configuration loaded from environment variables."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API Keys
    finnhub_api_key: str = ""
    fmp_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    earningscall_api_key: str = ""

    # Database
    database_url: str = "sqlite:///./data/earnings.db"

    # Paths
    data_dir: Path = Path(__file__).parent.parent / "data"

    # LLM (OpenRouter)
    extraction_model: str = "google/gemini-3-flash-preview"
    extraction_max_tokens: int = 16384

    # Pipeline
    quarters_to_fetch: int = 4

    # RAG / Analyst
    rag_generation_model: str = "openai/gpt-4.1-mini"
    rag_embedding_model: str = "openai/text-embedding-3-large"
    rag_embedding_mode: str = "hash"
    rag_chunk_words: int = 220
    rag_chunk_overlap: int = 40

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def financials_dir(self) -> Path:
        return self.data_dir / "financials"

    @property
    def claims_dir(self) -> Path:
        return self.data_dir / "claims"

    @property
    def verdicts_dir(self) -> Path:
        return self.data_dir / "verdicts"

    @property
    def sec_dir(self) -> Path:
        return self.data_dir / "sec"

    @property
    def rag_dir(self) -> Path:
        return self.data_dir / "rag"

    @property
    def rag_db_path(self) -> Path:
        return self.rag_dir / "knowledge.db"

    def ensure_dirs(self):
        for d in [
            self.transcripts_dir,
            self.financials_dir,
            self.claims_dir,
            self.verdicts_dir,
            self.sec_dir,
            self.rag_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()

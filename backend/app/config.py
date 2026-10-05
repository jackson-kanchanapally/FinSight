from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_name: str = "finsight"
    database_user: str = "finsight"
    database_password: str = "finsight_dev"
    database_host: str = "localhost"
    database_port: int = 5432
    event_monitor_enabled: bool = False
    event_monitor_interval_minutes: int = Field(default=30, ge=1)
    event_source_mode: Literal["fixture", "live", "none"] = "fixture"
    nse_source_enabled: bool = True
    bse_source_enabled: bool = False
    company_ir_source_enabled: bool = True
    live_source_timeout_seconds: float = Field(default=15, ge=3, le=60)
    market_data_enabled: bool = False
    market_data_provider: Literal["nse_mcp", "alpha_vantage", "manual"] = "nse_mcp"
    market_data_timeout_seconds: float = Field(default=12, ge=3, le=60)
    market_data_refresh_hour: int = Field(default=18, ge=0, le=23)
    market_data_refresh_minute: int = Field(default=30, ge=0, le=59)
    market_lens_tolerance_pct: float = Field(default=5, ge=0, le=100)
    alpha_vantage_api_key: str = ""
    document_storage_path: Path = (
        Path(__file__).resolve().parents[1] / "data" / "documents"
    )
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: float = Field(default=120, ge=1, le=600)
    quarterly_review_timeout_seconds: float = Field(default=360, ge=30, le=900)
    ollama_num_ctx: int = Field(default=8192, ge=4096, le=32768)
    ai_temperature: float = Field(default=0.1, ge=0, le=1)
    ai_max_context_chars: int = Field(default=24000, ge=2000, le=50000)
    ai_max_document_chars: int = Field(default=1800, ge=0, le=10000)
    company_memory_enabled: bool = True
    qdrant_base_url: str = "http://localhost:6333"
    memory_collection: str = "almonds_company_memory_v1"
    memory_embedding_model: str = "nomic-embed-text:latest"
    memory_embedding_dimensions: int = Field(default=768, ge=1)
    memory_timeout_seconds: float = Field(default=5, ge=0.1, le=60)
    memory_embedding_timeout_seconds: float = Field(default=45, ge=1, le=120)
    memory_chunk_chars: int = Field(default=1600, ge=400, le=4000)
    memory_overlap_chars: int = Field(default=160, ge=0, le=300)
    memory_context_chars: int = Field(default=2400, ge=400, le=6000)
    memory_min_score: float = Field(default=0.35, ge=0, le=1)

    model_config = SettingsConfigDict(
        env_file="../.env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


settings = Settings()

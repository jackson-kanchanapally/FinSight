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
    document_storage_path: Path = Path(__file__).resolve().parents[1] / "data" / "documents"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: float = Field(default=120, ge=1, le=600)
    ai_temperature: float = Field(default=0.1, ge=0, le=1)
    ai_max_context_chars: int = Field(default=12000, ge=2000, le=50000)
    ai_max_document_chars: int = Field(default=1800, ge=0, le=10000)

    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        return (f"postgresql+psycopg2://{self.database_user}:{self.database_password}"
                f"@{self.database_host}:{self.database_port}/{self.database_name}")


settings = Settings()

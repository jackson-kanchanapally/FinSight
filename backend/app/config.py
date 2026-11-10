from pathlib import Path
from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_name: str = "finsight"
    database_user: str = "finsight"
    database_password: str = "finsight_dev"
    database_host: str = "localhost"
    database_port: int = 5432
    research_automation_enabled: bool = True
    research_poll_seconds: int = Field(default=60, ge=10, le=3600)
    research_batch_size: int = Field(default=12, ge=1, le=100)
    event_monitor_enabled: bool = False
    event_monitor_interval_minutes: int = Field(default=30, ge=1)
    event_source_mode: Literal["fixture", "live", "none"] = "live"
    nse_source_enabled: bool = True
    bse_source_enabled: bool = False
    company_ir_source_enabled: bool = True
    live_source_timeout_seconds: float = Field(default=15, ge=3, le=60)
    telegram_enabled: bool = False
    ipo_monitor_enabled: bool = True
    ipo_check_hour_ist: int = Field(default=8, ge=0, le=8)
    ipo_min_gmp_pct: float = Field(default=10, ge=0, le=1000)
    ipo_max_pe: float = Field(default=40, gt=0, le=1000)
    ipo_gmp_max_age_hours: int = Field(default=24, ge=1, le=48)
    gold_monitor_enabled: bool = True
    gold_poll_minutes: int = Field(default=15, ge=5, le=60)
    gold_alert_times_ist: str = "11:00,17:00"
    gold_price_move_pct: float = Field(default=1, ge=0.1, le=25)
    gold_move_cooldown_minutes: int = Field(default=60, ge=15, le=1440)
    gold_source_timeout_seconds: float = Field(default=15, ge=3, le=60)
    gold_major_event_alerts_enabled: bool = True
    gold_major_event_alerts_since: datetime | None = None
    market_briefs_enabled: bool = True
    market_brief_times_ist: str = "08:45,11:00,12:30,16:00"
    market_brief_source_timeout_seconds: float = Field(default=12, ge=3, le=30)
    market_brief_ai_timeout_seconds: float = Field(default=90, ge=10, le=180)
    market_brief_direction_threshold_pct: float = Field(default=0.3, ge=0.1, le=1)
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    telegram_timeout_seconds: float = Field(default=10, ge=1, le=30)
    telegram_poll_seconds: int = Field(default=5, ge=3, le=300)
    telegram_max_event_age_hours: int = Field(default=48, ge=1, le=720)
    telegram_important_event_types: str = (
        "FINANCIAL_RESULTS,DIVIDEND,ORDER_WIN,ORDER_CANCELLATION,ACQUISITION,DIVESTMENT,CAPEX,"
        "MANAGEMENT_CHANGE,AUDITOR_CHANGE,BUYBACK,FUND_RAISE,CREDIT_RATING,"
        "PROMOTER_TRANSACTION,PLEDGE,REGULATORY,LITIGATION"
    )
    report_public_base_url: str = ""
    report_access_secret: SecretStr = SecretStr("")
    report_link_ttl_seconds: int = Field(default=86400, ge=300, le=86400)
    market_data_enabled: bool = False
    market_data_provider: Literal["nse_mcp", "alpha_vantage", "manual"] = "nse_mcp"
    market_data_timeout_seconds: float = Field(default=12, ge=3, le=60)
    market_data_refresh_hour: int = Field(default=18, ge=0, le=23)
    market_data_refresh_minute: int = Field(default=30, ge=0, le=59)
    market_lens_tolerance_pct: float = Field(default=5, ge=0, le=100)
    expectation_met_tolerance_pct: float = Field(default=5, ge=0, le=10)
    expectation_partial_tolerance_pct: float = Field(default=10, ge=10, le=100)
    expectation_percentage_tolerance_pp: float = Field(default=0.5, ge=0, le=5)
    expectation_min_sample: int = Field(default=20, ge=20)
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

    @field_validator("gold_alert_times_ist")
    @classmethod
    def validate_gold_times(cls, value: str) -> str:
        from datetime import datetime
        parts = [part.strip() for part in value.split(",")]
        try:
            parsed = [datetime.strptime(part, "%H:%M").strftime("%H:%M") for part in parts]
        except ValueError as exc:
            raise ValueError("Gold alert times must be two HH:MM values in IST") from exc
        if len(parsed) != 2 or parsed != sorted(set(parsed)):
            raise ValueError("Gold alert times must contain two distinct times in increasing order")
        return ",".join(parsed)

    @field_validator("market_brief_times_ist")
    @classmethod
    def validate_market_brief_times(cls, value: str) -> str:
        from datetime import datetime, time, timedelta
        values = [part.strip() for part in value.split(",")]
        # Preserve the prior three review times when adding the new update.
        if len(values) == 3:
            values.insert(1, "11:00")
        if any(len(part) != 5 for part in values):
            raise ValueError("Market times must use HH:MM in IST")
        parts = [time.fromisoformat(part) for part in values]
        if (len(parts) != 4 or parts != sorted(set(parts)) or
                not time(7) <= parts[0] <= time(9) or
                not time(10) <= parts[1] <= time(11, 30) or
                not time(11) <= parts[2] <= time(13, 30) or
                not time(16) <= parts[3] <= time(18)):
            raise ValueError("Market times require pre-open, midmorning, midday, and post-close slots")
        if datetime.combine(datetime.min.date(), parts[2]) - datetime.combine(datetime.min.date(), parts[1]) < timedelta(minutes=30):
            raise ValueError("Midmorning and midday market review windows must not overlap")
        return ",".join(part.strftime("%H:%M") for part in parts)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


settings = Settings()

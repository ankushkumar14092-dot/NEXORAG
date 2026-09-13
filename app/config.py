from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Primary LLM: Fireworks (OpenAI-compatible)
    fireworks_api_key: str = ""
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    fireworks_model: str = "accounts/fireworks/models/deepseek-v4-pro-0813"
    # Optional faster model for lower latency answers
    fireworks_fast_model: str = "accounts/fireworks/models/deepseek-v4p1-flash"
    prefer_fast_model: bool = False
    answer_max_tokens: int = 700

    # Optional fallback
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    whisper_model: str = "base"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    max_upload_mb: int = 500
    chunk_seconds: float = 45.0
    chunk_overlap_seconds: float = 5.0
    top_k: int = 6

    # Temporary RAM cache (process memory — not durable)
    ram_cache_ttl_seconds: float = 600.0
    ram_cache_max_entries: int = 256
    ram_cache_max_mb: int = 128

    # Operations / security
    rate_limit_per_minute: int = 120
    api_key: str = ""  # if set, required on /api/* (except health/ready/metrics)
    alert_webhook_url: str = ""  # optional POST JSON on 5xx


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "uploads").mkdir(exist_ok=True)
(settings.data_dir / "uploads" / "docs").mkdir(exist_ok=True)
(settings.data_dir / "audio").mkdir(exist_ok=True)
(settings.data_dir / "store").mkdir(exist_ok=True)
(settings.data_dir / "backups").mkdir(exist_ok=True)

"""Centralized configuration. Every value can be overridden via environment variables or a .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM provider selection ---------------------------------------------
    # "auto" picks the first provider that has a key: gemini -> openai -> anthropic -> offline
    llm_provider: str = "auto"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1

    # --- Server-side speech-to-text (optional; browser STT works without it) --
    # "auto" -> groq if GROQ_API_KEY, else openai if OPENAI_API_KEY, else disabled
    stt_provider: str = "auto"
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    openai_stt_model: str = "whisper-1"

    # --- Uploads & storage ----------------------------------------------------
    max_upload_mb: int = 30
    max_slides: int = 60
    render_width_px: int = 1280
    data_dir: str = str(ROOT_DIR / ".pitchmirror_data")
    session_ttl_minutes: int = 180

    # --- Analysis tuning ------------------------------------------------------
    analysis_min_new_words: int = 30  # interim analysis trigger on the current slide
    analysis_min_interval_s: float = 12.0
    auto_question_min_interval_s: float = 40.0
    enrich_slides_with_vision: bool = True

    # --- Server ---------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()

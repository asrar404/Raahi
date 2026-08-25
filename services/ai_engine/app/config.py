"""Configuration for the RAAHI AI engine."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    SERVICE_NAME: str = "raahi-ai-engine"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── LLM ─────────────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    LLM_TEMPERATURE: float = 0.0  # deterministic: this is extraction, not prose
    LLM_TIMEOUT_SECONDS: float = 25.0
    LLM_MAX_RETRIES: int = 1

    # ── Gateway (safety scoring lives behind PostGIS) ───────
    BACKEND_URL: str = "http://backend:8000"
    INTERNAL_API_KEY: str = ""
    BACKEND_TIMEOUT_SECONDS: float = 10.0

    # ── Geocoding ───────────────────────────────────────────
    # Built-in gazetteer is always tried first. Nominatim is opt-in because
    # its public endpoint is rate-limited to ~1 req/s and forbids heavy use.
    ENABLE_NOMINATIM: bool = False
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"
    NOMINATIM_USER_AGENT: str = "raahi-ai-engine/1.0 (contact@raahi.app)"
    GEOCODE_TIMEOUT_SECONDS: float = 6.0
    DEFAULT_CITY: str = "Delhi"

    # ── Planner utility weights (must sum to 1.0) ───────────
    W_COST: float = Field(default=0.35, ge=0, le=1)
    W_TIME: float = Field(default=0.30, ge=0, le=1)
    W_SAFETY: float = Field(default=0.35, ge=0, le=1)

    # Normalisation ceiling for journey duration, in minutes. A trip at or
    # beyond this scores 0 on the time axis.
    MAX_DURATION_MINS: int = 120
    # How many ranked routes to return
    TOP_N_ROUTES: int = 3
    # Absolute floor on budget so utility maths never divides by zero
    MIN_BUDGET: float = 10.0
    DEFAULT_BUDGET: float = 300.0

    # ── Safety ──────────────────────────────────────────────
    # Score assumed when the gateway cannot be reached
    FALLBACK_SAFETY_SCORE: float = 3.5
    # Routes below this are dropped when the user asked for safety priority
    MIN_SAFETY_FOR_PRIORITY: float = 2.5

    @property
    def llm_configured(self) -> bool:
        return bool(self.GROQ_API_KEY)

    @property
    def weight_sum(self) -> float:
        return self.W_COST + self.W_TIME + self.W_SAFETY


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

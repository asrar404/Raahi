"""Configuration for the RAAHI safety watcher."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    SERVICE_NAME: str = "raahi-safety-watcher"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── PostgreSQL ──────────────────────────────────────────
    DATABASE_URL: str = "postgresql://raahi:raahi_secure_pass@postgres:5432/raahi_db"
    DB_POOL_MIN: int = 1
    DB_POOL_MAX: int = 8

    # ── Redis stream ────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"
    TELEMETRY_STREAM: str = "raahi:telemetry"
    # Consumer group, so restarts resume from the last acknowledged entry
    # instead of skipping everything that arrived while the service was down.
    CONSUMER_GROUP: str = "raahi-watchers"
    CONSUMER_NAME: str = "watcher-1"
    # Entries pulled per XREADGROUP call
    BATCH_SIZE: int = 50
    # Blocking read timeout, seconds
    POLL_INTERVAL_SECS: int = 15

    # ── Peer services ───────────────────────────────────────
    BACKEND_URL: str = "http://backend:8000"
    AI_ENGINE_URL: str = "http://ai_engine:8001"
    INTERNAL_API_KEY: str = ""
    HTTP_TIMEOUT_SECONDS: float = 15.0

    # ── Safety thresholds ───────────────────────────────────
    RISK_THRESHOLD: int = Field(default=3, ge=1, le=5)
    OFF_ROUTE_THRESHOLD_M: float = 300.0
    # Consecutive off-route fixes before a reroute is offered. GPS in dense
    # urban areas drifts badly; a single bad fix must not trigger anything.
    OFF_ROUTE_STRIKES: int = Field(default=3, ge=1)
    # No movement for this long -> STATIONARY
    STATIONARY_THRESHOLD_SECS: int = 300
    # No movement for this long -> offer a reroute
    STATIONARY_REROUTE_SECS: int = 600
    # Metres of movement below which the traveller counts as stationary.
    # Consumer GPS is accurate to roughly 10-20 m in cities, so anything
    # tighter would read noise as motion.
    MOVEMENT_THRESHOLD_M: float = 25.0

    # ── Escalation control ──────────────────────────────────
    # Minimum gap between reroute offers for one trip. Without this, a
    # traveller genuinely off-route gets a new suggestion every 15 seconds.
    REROUTE_COOLDOWN_SECS: int = 180
    # SOS is idempotent per trip, but this bounds re-escalation if the first
    # attempt failed outright.
    SOS_RETRY_AFTER_SECS: int = 600
    # Drop in-memory context for trips that have gone silent this long
    TRIP_CONTEXT_TTL_SECS: int = 7200
    # How often to sweep stale contexts
    JANITOR_INTERVAL_SECS: int = 300

    @field_validator("DATABASE_URL")
    @classmethod
    def _strip_dialect(cls, v: str) -> str:
        """Normalise a SQLAlchemy-style DSN for asyncpg."""
        for bad, good in (
            ("postgresql+asyncpg://", "postgresql://"),
            ("postgres+asyncpg://", "postgresql://"),
            ("postgres://", "postgresql://"),
        ):
            if v.startswith(bad):
                return v.replace(bad, good, 1)
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

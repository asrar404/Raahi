"""Configuration for the RAAHI API gateway.

Every value is environment driven so the same image runs in dev and prod.
`settings()` is cached, so import it anywhere without re-parsing the env.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service identity ────────────────────────────────────
    SERVICE_NAME: str = "raahi-gateway"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── PostgreSQL ──────────────────────────────────────────
    DATABASE_URL: str = "postgresql://raahi:raahi_secure_pass@postgres:5432/raahi_db"
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 16
    DB_COMMAND_TIMEOUT: float = 20.0

    # ── Redis ───────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"
    TELEMETRY_STREAM: str = "raahi:telemetry"
    # Cap the stream so a runaway client cannot exhaust Redis memory
    TELEMETRY_STREAM_MAXLEN: int = 100_000

    # ── Supabase Auth ───────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    # JWT secret no longer used (Supabase uses RS256 via JWKS)
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_JWT_AUDIENCE: str = "authenticated"

    # ── Twilio ──────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    # False short-circuits to structured logs so local dev never
    # sends real SMS or places real calls to emergency contacts.
    TWILIO_ENABLED: bool = False

    # ── Peer services ───────────────────────────────────────
    AI_ENGINE_URL: str = "http://ai_engine:8001"
    # Shared secret for service-to-service calls (safety_watcher -> gateway,
    # ai_engine -> gateway). Sent as the X-Internal-Token header. Those
    # callers have no end-user JWT, so this is how they authenticate.
    INTERNAL_API_KEY: str = ""

    # ── Safety tuning ───────────────────────────────────────
    RISK_THRESHOLD: int = Field(default=3, ge=1, le=5)
    OFF_ROUTE_THRESHOLD_M: float = 300.0
    NEARBY_ALERT_RADIUS_M: float = 300.0
    REFUGE_SEARCH_RADIUS_M: float = 600.0
    REFUGE_MAX_RISK: int = Field(default=2, ge=1, le=5)

    # ── HTTP ────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["*"]

    @field_validator("DATABASE_URL")
    @classmethod
    def _strip_sqlalchemy_dialect(cls, v: str) -> str:
        """Accept a SQLAlchemy-style DSN but hand asyncpg what it expects.

        Compose files and tutorials commonly carry
        `postgresql+asyncpg://...`; asyncpg.connect rejects that prefix, so
        normalise it here instead of failing at first query.
        """
        for bad, good in (
            ("postgresql+asyncpg://", "postgresql://"),
            ("postgres+asyncpg://", "postgresql://"),
            ("postgres://", "postgresql://"),
        ):
            if v.startswith(bad):
                return v.replace(bad, good, 1)
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v):
        """Allow a comma-separated string as well as a JSON list."""
        if isinstance(v, str):
            if v.strip().startswith("["):
                return v
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"production", "prod"}

    @property
    def twilio_configured(self) -> bool:
        return bool(
            self.TWILIO_ENABLED
            and self.TWILIO_ACCOUNT_SID
            and self.TWILIO_AUTH_TOKEN
            and self.TWILIO_FROM_NUMBER
        )

    @property
    def auth_configured(self) -> bool:
        return bool(self.SUPABASE_JWT_SECRET)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Import-friendly singleton
settings = get_settings()

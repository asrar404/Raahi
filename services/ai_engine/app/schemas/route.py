"""Request/response envelopes for the planning endpoint."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import ParsedIntent, PlannedRoute


class PlanRequest(BaseModel):
    """Plan a journey from a natural-language request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_input: str = Field(min_length=3, max_length=1000)
    # Optional device position, used when the user says "from here"
    origin_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    origin_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    city: Optional[str] = Field(
        default=None, description="Disambiguation hint for the geocoder"
    )
    night_mode: Optional[bool] = Field(
        default=None, description="Override the server's time-of-day judgement"
    )


class ResolvedPlace(BaseModel):
    """Outcome of geocoding one place name."""

    query: str
    name: str
    lat: float
    lon: float
    city: Optional[str] = None
    source: str = Field(description="gazetteer | nominatim | device | provided")
    confidence: float = Field(default=1.0, ge=0, le=1)


class PlanResponse(BaseModel):
    routes: List[PlannedRoute] = Field(default_factory=list)
    intent: Optional[ParsedIntent] = None
    origin: Optional[ResolvedPlace] = None
    destination: Optional[ResolvedPlace] = None
    error: Optional[str] = None
    # Non-fatal issues worth surfacing: low geocoding confidence, LLM
    # unavailable and heuristics used, budget too tight for any option.
    warnings: List[str] = Field(default_factory=list)
    # True when the deterministic parser ran instead of the LLM
    used_fallback_parser: bool = False
    duration_ms: Optional[int] = None

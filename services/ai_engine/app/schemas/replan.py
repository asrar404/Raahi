"""Reroute (replanning) schemas.

Triggered mid-journey by safety_watcher when the traveller deviates from the
plan, stalls, or enters a high-risk zone.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.intent import ParsedIntent, PlannedRoute


class RerouteTrigger(str, Enum):
    OFF_ROUTE = "off_route"
    DELAY = "delay"
    RISK_ZONE = "risk_zone"
    BUDGET = "budget"
    MANUAL = "manual"


class RerouteRequest(BaseModel):
    trip_id: str
    intent: ParsedIntent
    current_lat: float = Field(ge=-90, le=90)
    current_lon: float = Field(ge=-180, le=180)
    elapsed_mins: int = Field(default=0, ge=0)
    spent_budget: float = Field(default=0.0, ge=0)
    trigger: RerouteTrigger = RerouteTrigger.MANUAL
    night_mode: Optional[bool] = None


class RerouteResponse(BaseModel):
    new_routes: List[PlannedRoute] = Field(default_factory=list)
    trip_id: Optional[str] = None
    trigger: Optional[str] = None
    # Budget still available after subtracting what has already been spent
    remaining_budget: Optional[float] = None
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    duration_ms: Optional[int] = None

"""Trip and trip-leg schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TransitMode(str, Enum):
    WALK = "walk"
    METRO = "metro"
    BUS = "bus"
    TRAIN = "train"
    AUTO = "auto"
    CAB = "cab"
    RAPIDO = "rapido"
    FERRY = "ferry"


class TripStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SOS = "sos"


class LegStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class LatLon(BaseModel):
    """A WGS84 coordinate.

    Bounds are validated because a silently swapped lat/lon lands the user in
    the ocean, and PostGIS will happily accept it.
    """

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


# ============================================================
# Input
# ============================================================
class TripLegIn(BaseModel):
    """One segment of a route being persisted."""

    model_config = ConfigDict(str_strip_whitespace=True)

    leg_order: int = Field(ge=0)
    mode: TransitMode
    from_name: str = Field(min_length=1)
    from_lat: float = Field(ge=-90, le=90)
    from_lon: float = Field(ge=-180, le=180)
    to_name: str = Field(min_length=1)
    to_lat: float = Field(ge=-90, le=90)
    to_lon: float = Field(ge=-180, le=180)
    # Full polyline as [[lat, lon], ...]. Off-route detection measures against
    # this, so a straight from->to fallback makes deviation checks coarse.
    route_coords: Optional[List[List[float]]] = None
    distance_km: Optional[float] = Field(default=None, ge=0)
    planned_cost: float = Field(default=0, ge=0)
    planned_duration_mins: Optional[int] = Field(default=None, ge=0)
    provider: Optional[str] = None
    safety_score: Optional[float] = Field(default=None, ge=0, le=5)

    @field_validator("route_coords")
    @classmethod
    def _check_coords(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        if v is None:
            return v
        for pair in v:
            if len(pair) != 2:
                raise ValueError("route_coords entries must be [lat, lon] pairs")
            lat, lon = pair
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                raise ValueError(f"coordinate out of range: {pair}")
        # A LineString needs 2+ points; drop a degenerate single point so the
        # DB stores NULL instead of failing the geometry cast.
        return v if len(v) >= 2 else None

    def line_wkt(self) -> Optional[str]:
        """LINESTRING WKT for route_line, or None when unavailable.

        Falls back to a two-point line between endpoints so off-route
        detection has something to measure against.
        """
        coords = self.route_coords or [
            [self.from_lat, self.from_lon],
            [self.to_lat, self.to_lon],
        ]
        if len(coords) < 2:
            return None
        # WKT is "lon lat"
        points = ", ".join(f"{lon} {lat}" for lat, lon in coords)
        return f"LINESTRING({points})"


class TripCreate(BaseModel):
    """Persist a route the user selected on the RouteSelection screen."""

    model_config = ConfigDict(str_strip_whitespace=True)

    origin_name: str = Field(min_length=1)
    origin_lat: float = Field(ge=-90, le=90)
    origin_lon: float = Field(ge=-180, le=180)
    dest_name: str = Field(min_length=1)
    dest_lat: float = Field(ge=-90, le=90)
    dest_lon: float = Field(ge=-180, le=180)
    budget_ceiling: float = Field(gt=0)
    time_deadline: Optional[datetime] = None
    transit_prefs: List[TransitMode] = Field(
        default_factory=lambda: [
            TransitMode.METRO, TransitMode.BUS, TransitMode.AUTO, TransitMode.CAB
        ]
    )
    total_planned_cost: Optional[float] = Field(default=None, ge=0)
    planned_eta: Optional[datetime] = None
    utility_score: Optional[float] = Field(default=None, ge=0, le=1)
    safety_priority: bool = True
    raw_intent: Optional[str] = None
    # Serialised ParsedIntent, replayed verbatim by the AI engine on reroute
    intent_json: Dict[str, Any] = Field(default_factory=dict)
    legs: List[TripLegIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_legs(self) -> "TripCreate":
        if self.legs:
            orders = [leg.leg_order for leg in self.legs]
            if len(set(orders)) != len(orders):
                raise ValueError("leg_order values must be unique within a trip")
        return self

    @property
    def computed_planned_cost(self) -> float:
        """Explicit total if given, otherwise the sum of the legs."""
        if self.total_planned_cost is not None:
            return self.total_planned_cost
        return round(sum(leg.planned_cost for leg in self.legs), 2)


class TripStatusUpdate(BaseModel):
    status: TripStatus


class LegStatusUpdate(BaseModel):
    status: LegStatus
    actual_cost: Optional[float] = Field(default=None, ge=0)
    actual_duration_mins: Optional[int] = Field(default=None, ge=0)


# ============================================================
# Output
# ============================================================
class TripLegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    leg_order: int
    mode: str
    from_name: str
    from_lat: float
    from_lon: float
    to_name: str
    to_lat: float
    to_lon: float
    route_coords: Optional[List[List[float]]] = None
    distance_km: Optional[float] = None
    planned_cost: float = 0
    actual_cost: Optional[float] = None
    planned_duration_mins: Optional[int] = None
    actual_duration_mins: Optional[int] = None
    provider: Optional[str] = None
    booking_ref: Optional[str] = None
    status: str = "pending"
    departed_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    safety_score: Optional[float] = None


class TripSummary(BaseModel):
    """Compact form for trip history lists."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    origin_name: str
    dest_name: str
    budget_ceiling: float
    total_planned_cost: Optional[float] = None
    total_actual_cost: float = 0
    planned_eta: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    status: str
    origin_name: str
    origin_lat: float
    origin_lon: float
    dest_name: str
    dest_lat: float
    dest_lon: float
    budget_ceiling: float
    time_deadline: Optional[datetime] = None
    transit_prefs: List[str] = Field(default_factory=list)
    total_planned_cost: Optional[float] = None
    total_actual_cost: float = 0
    planned_eta: Optional[datetime] = None
    actual_eta: Optional[datetime] = None
    utility_score: Optional[float] = None
    safety_priority: bool = True
    raw_intent: Optional[str] = None
    intent_json: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    legs: List[TripLegOut] = Field(default_factory=list)

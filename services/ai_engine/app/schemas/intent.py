"""Core intent and route schemas.

`ParsedIntent` is the contract between the LLM and everything downstream, and
is also what the gateway stores in `trips.intent_json` so a reroute can replay
the original request.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TransitMode(str, Enum):
    WALK = "walk"
    METRO = "metro"
    BUS = "bus"
    TRAIN = "train"
    AUTO = "auto"
    CAB = "cab"
    RAPIDO = "rapido"

    @property
    def label(self) -> str:
        return {
            TransitMode.WALK: "Walk",
            TransitMode.METRO: "Metro",
            TransitMode.BUS: "Bus",
            TransitMode.TRAIN: "Local Train",
            TransitMode.AUTO: "Auto Rickshaw",
            TransitMode.CAB: "Cab",
            TransitMode.RAPIDO: "Bike Taxi",
        }[self]

    @property
    def is_public(self) -> bool:
        """Public transit is cheaper and, at night, usually safer than a
        private vehicle with a stranger."""
        return self in (TransitMode.METRO, TransitMode.BUS, TransitMode.TRAIN)


class ParsedIntent(BaseModel):
    """Structured travel intent extracted from a free-text request.

    Coordinates are Optional because the LLM extracts *names*; the geocoding
    node fills the lat/lon in afterwards. Asking an LLM for coordinates
    directly produces confident, wrong numbers.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    source_raw: str = Field(description="Origin exactly as the user wrote it")
    source_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    source_lon: Optional[float] = Field(default=None, ge=-180, le=180)

    destination_raw: str = Field(description="Destination as the user wrote it")
    dest_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    dest_lon: Optional[float] = Field(default=None, ge=-180, le=180)

    budget_ceiling: float = Field(default=300.0, gt=0, description="Max total spend, INR")
    time_deadline: Optional[str] = Field(default=None, description="ISO 8601, or null")

    preferred_modes: List[TransitMode] = Field(
        default_factory=lambda: [TransitMode.METRO, TransitMode.BUS]
    )
    safety_priority: bool = True
    night_travel: bool = Field(
        default=False, description="Journey falls in the 22:00-06:00 IST window"
    )
    city: Optional[str] = Field(default=None, description="Resolved city, aids geocoding")
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("preferred_modes", mode="before")
    @classmethod
    def _coerce_modes(cls, v):
        """Salvage a partially-valid mode list from LLM output.

        Models routinely emit "taxi", "rickshaw" or "subway". Mapping the
        common synonyms and dropping the rest beats failing the whole parse.
        """
        if v is None:
            return [TransitMode.METRO, TransitMode.BUS]
        if isinstance(v, (str, TransitMode)):
            v = [v]

        synonyms = {
            "subway": "metro", "underground": "metro", "mrt": "metro",
            "taxi": "cab", "uber": "cab", "ola": "cab", "car": "cab",
            "rickshaw": "auto", "autorickshaw": "auto", "tuktuk": "auto",
            "bike": "rapido", "biketaxi": "rapido", "motorcycle": "rapido",
            "walking": "walk", "foot": "walk", "onfoot": "walk",
            "local": "train", "suburban": "train", "railway": "train",
        }
        valid = {m.value for m in TransitMode}

        out: list[str] = []
        for item in v:
            # Enum members must be read via .value. str(TransitMode.METRO)
            # yields "TransitMode.METRO", which matches nothing and would
            # silently discard every already-valid mode.
            raw = item.value if isinstance(item, TransitMode) else str(item)
            key = raw.strip().lower().replace(" ", "").replace("_", "")
            mapped = key if key in valid else synonyms.get(key)
            if mapped and mapped not in out:
                out.append(mapped)

        return out or [TransitMode.METRO.value, TransitMode.BUS.value]

    @field_validator("budget_ceiling", mode="before")
    @classmethod
    def _coerce_budget(cls, v):
        """Accept "₹500", "500 rupees", "1,200" and similar."""
        if v is None:
            return 300.0
        if isinstance(v, (int, float)):
            return float(v) if v > 0 else 300.0
        cleaned = "".join(ch for ch in str(v) if ch.isdigit() or ch == ".")
        try:
            value = float(cleaned)
            return value if value > 0 else 300.0
        except ValueError:
            return 300.0

    @property
    def has_coordinates(self) -> bool:
        return None not in (self.source_lat, self.source_lon, self.dest_lat, self.dest_lon)


class RouteLeg(BaseModel):
    """One segment of a candidate route."""

    model_config = ConfigDict(str_strip_whitespace=True)

    leg_order: int = Field(ge=0)
    mode: TransitMode
    from_name: str
    from_lat: float = Field(ge=-90, le=90)
    from_lon: float = Field(ge=-180, le=180)
    to_name: str
    to_lat: float = Field(ge=-90, le=90)
    to_lon: float = Field(ge=-180, le=180)
    distance_km: float = Field(ge=0)
    planned_cost: float = Field(ge=0)
    duration_mins: int = Field(ge=0)
    provider: Optional[str] = None
    safety_score: float = Field(default=3.5, ge=0, le=5)

    @property
    def midpoint(self) -> tuple[float, float]:
        """Approximate midpoint, used as the safety sampling point.

        Linear interpolation is fine at intra-city scale — the error over a
        few kilometres is far smaller than the resolution of a safety zone.
        """
        return ((self.from_lat + self.to_lat) / 2, (self.from_lon + self.to_lon) / 2)


class PlannedRoute(BaseModel):
    """A complete ranked itinerary."""

    route_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    legs: List[RouteLeg] = Field(min_length=1)
    total_cost: float = Field(ge=0)
    total_duration: int = Field(ge=0)
    utility_score: float = Field(ge=0, le=1)
    safety_rating: float = Field(ge=0, le=5)
    summary: str = ""
    # Populated when the route is within budget but uncomfortably close, or
    # crosses a zone the traveller should know about before committing.
    warnings: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_totals(self) -> "PlannedRoute":
        """Keep declared totals consistent with the legs.

        A route whose headline cost disagrees with the sum of its legs would
        let a traveller commit to something they cannot afford.
        """
        leg_cost = round(sum(leg.planned_cost for leg in self.legs), 2)
        if abs(leg_cost - self.total_cost) > 0.5:
            self.total_cost = leg_cost
        leg_time = sum(leg.duration_mins for leg in self.legs)
        if leg_time != self.total_duration:
            self.total_duration = leg_time
        return self

    @property
    def mode_sequence(self) -> List[str]:
        return [leg.mode.value for leg in self.legs]

    @property
    def transfers(self) -> int:
        """Vehicle changes, ignoring walking connections."""
        vehicle_legs = [leg for leg in self.legs if leg.mode != TransitMode.WALK]
        return max(0, len(vehicle_legs) - 1)

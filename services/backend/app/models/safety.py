"""Safety, SOS and crowdsourced-report schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.trip import LatLon


class ReportCategory(str, Enum):
    HARASSMENT = "harassment"
    THEFT = "theft"
    POOR_LIGHTING = "poor_lighting"
    UNSAFE_AREA = "unsafe_area"
    ACCIDENT = "accident"
    FLOODING = "flooding"
    ROAD_BLOCKED = "road_blocked"
    SAFE_SPOT = "safe_spot"
    POLICE_PRESENT = "police_present"


class SOSTriggerSource(str, Enum):
    AUTO = "auto"        # gateway detected a high-risk zone from telemetry
    MANUAL = "manual"    # traveller pressed the SOS button
    WATCHER = "watcher"  # safety_watcher state machine escalated


# ============================================================
# SOS
# ============================================================
class SOSRequest(BaseModel):
    """Raise an SOS.

    lat/lon are optional: when the button is pressed the phone may not have a
    fresh fix, so the gateway falls back to the last stored telemetry point.
    """

    trip_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    trigger_source: SOSTriggerSource = SOSTriggerSource.MANUAL
    risk_info: Dict[str, Any] = Field(default_factory=dict)
    notify_contacts: bool = True
    message: Optional[str] = None


class SOSResponse(BaseModel):
    sos_event_id: Optional[UUID] = None
    trip_id: Optional[UUID] = None
    location: Optional[LatLon] = None
    subscribers_notified: int = 0
    contacts_alerted: int = 0
    sms_sent: int = 0
    calls_placed: int = 0
    twilio_enabled: bool = False
    safe_refuges: List[Dict[str, Any]] = Field(default_factory=list)
    already_active: bool = Field(
        default=False,
        description="True when an unresolved SOS already existed for this trip",
    )


class SOSResolveRequest(BaseModel):
    trip_id: UUID
    restore_status: str = Field(
        default="active",
        description="Trip status to restore once the SOS is cleared",
    )


class NotifyContactsRequest(BaseModel):
    """Direct escalation hook used by safety_watcher.

    Contacts may be passed inline so the watcher does not need a second round
    trip during an emergency; when omitted the gateway loads them from the
    user record.
    """

    trip_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    contacts: Optional[List[Dict[str, Any]]] = None
    user_name: Optional[str] = None
    zone_name: Optional[str] = None
    voice: bool = True


class NotifyContactsResponse(BaseModel):
    contacts_alerted: int = 0
    sms_sent: int = 0
    sms_failed: int = 0
    calls_placed: int = 0
    calls_failed: int = 0
    twilio_enabled: bool = False
    dry_run: bool = False


# ============================================================
# Reroute fan-out
# ============================================================
class RerouteBroadcast(BaseModel):
    """Push freshly planned routes to every subscriber of a trip."""

    trip_id: UUID
    new_routes: List[Dict[str, Any]] = Field(default_factory=list)
    trigger: str = Field(
        default="manual",
        description="off_route | delay | risk_zone | manual",
    )


# ============================================================
# Risk reads
# ============================================================
class RiskZoneOut(BaseModel):
    zone_id: UUID
    zone_name: str
    risk_score: int
    risk_factors: List[str] = Field(default_factory=list)


class SafeRefugeOut(BaseModel):
    zone_id: UUID
    zone_name: str
    risk_score: int
    distance_m: float


class AlertOut(BaseModel):
    report_id: UUID
    category: str
    severity: int
    distance_m: float
    description: Optional[str] = None
    lat: float
    lon: float
    created_at: datetime


class RiskCheckResponse(BaseModel):
    in_high_risk: bool
    max_risk: int = 0
    risk_zones: List[RiskZoneOut] = Field(default_factory=list)
    off_route: bool = False
    nearby_alerts: List[AlertOut] = Field(default_factory=list)
    safe_refuges: List[SafeRefugeOut] = Field(default_factory=list)
    safety_score: Optional[float] = None
    night_mode: bool = False


class ZoneOut(BaseModel):
    """Safety zone with GeoJSON geometry, for the map overlay."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    city: str
    risk_score: int
    night_risk_score: Optional[int] = None
    time_sensitive: bool = False
    risk_factors: List[str] = Field(default_factory=list)
    verified_by: str = "ml"
    geojson: str
    center_lat: float
    center_lon: float


# ============================================================
# Scoring (AI engine)
# ============================================================
class ScorePointsRequest(BaseModel):
    """Batch safety scoring.

    The AI engine calls this once per candidate plan with every leg midpoint,
    rather than once per point.
    """

    points: List[LatLon] = Field(min_length=1, max_length=200)
    night_mode: Optional[bool] = None


class ScorePointsResponse(BaseModel):
    scores: List[float]
    night_mode: bool
    average: float


# ============================================================
# Crowdsourced reports
# ============================================================
class ReportCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    category: ReportCategory
    description: Optional[str] = Field(default=None, max_length=500)
    severity: int = Field(default=3, ge=1, le=5)
    ttl_hours: int = Field(default=24, ge=1, le=720)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    severity: int
    description: Optional[str] = None
    lat: float
    lon: float
    verified: bool = False
    upvotes: int = 0
    downvotes: int = 0
    expires_at: datetime
    created_at: datetime


class ReportVote(BaseModel):
    direction: str = Field(pattern="^(up|down)$")

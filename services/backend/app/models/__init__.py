"""Pydantic request/response schemas for the gateway API."""

from app.models.budget import (
    BudgetAlert,
    BudgetSummary,
    ExpenseCreate,
    ExpenseOut,
)
from app.models.safety import (
    NotifyContactsRequest,
    ReportCreate,
    ReportOut,
    RerouteBroadcast,
    RiskCheckResponse,
    RiskZoneOut,
    SafeRefugeOut,
    ScorePointsRequest,
    ScorePointsResponse,
    SOSRequest,
    SOSResponse,
    ZoneOut,
)
from app.models.trip import (
    LatLon,
    TransitMode,
    TripCreate,
    TripLegIn,
    TripLegOut,
    TripOut,
    TripStatus,
    TripStatusUpdate,
    TripSummary,
)
from app.models.user import (
    EmergencyContact,
    EmergencyContactsUpdate,
    UserProfile,
    UserProfileUpdate,
)

__all__ = [
    # user
    "EmergencyContact",
    "EmergencyContactsUpdate",
    "UserProfile",
    "UserProfileUpdate",
    # trip
    "LatLon",
    "TransitMode",
    "TripCreate",
    "TripLegIn",
    "TripLegOut",
    "TripOut",
    "TripStatus",
    "TripStatusUpdate",
    "TripSummary",
    # safety
    "NotifyContactsRequest",
    "ReportCreate",
    "ReportOut",
    "RerouteBroadcast",
    "RiskCheckResponse",
    "RiskZoneOut",
    "SafeRefugeOut",
    "ScorePointsRequest",
    "ScorePointsResponse",
    "SOSRequest",
    "SOSResponse",
    "ZoneOut",
    # budget
    "BudgetAlert",
    "BudgetSummary",
    "ExpenseCreate",
    "ExpenseOut",
]

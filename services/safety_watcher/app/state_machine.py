"""Per-trip safety state machine.

    IDLE ─▶ NAVIGATING ⇄ STATIONARY
                │  ⇄ OFF_ROUTE
                │
                ├─▶ HIGH_RISK_ZONE ─▶ SOS_TRIGGERED
                └─▶ COMPLETED

State lives in memory, keyed by trip_id, because it is derived from a stream of
fixes rather than being authoritative. Losing it on restart costs a few minutes
of accumulated strike counts, not correctness — and PostGIS still holds every
telemetry point.

Escalation flags (`sos_triggered`, `last_reroute_at`) are what make repeated
alerts safe. Without them, a traveller genuinely standing in a high-risk zone
would have their emergency contacts SMSed every fifteen seconds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from math import asin, cos, radians, sin, sqrt
from typing import Any, Optional

log = logging.getLogger(__name__)


class TripState(Enum):
    IDLE = auto()
    NAVIGATING = auto()
    STATIONARY = auto()       # no meaningful movement for a while
    OFF_ROUTE = auto()        # deviating beyond threshold, strikes exceeded
    HIGH_RISK_ZONE = auto()   # inside a zone at/above the risk threshold
    SOS_TRIGGERED = auto()    # contacts alerted
    COMPLETED = auto()

    @property
    def is_alarming(self) -> bool:
        return self in (TripState.HIGH_RISK_ZONE, TripState.SOS_TRIGGERED)

    @property
    def is_terminal(self) -> bool:
        return self is TripState.COMPLETED


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two coordinates, in metres."""
    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(min(1.0, sqrt(a)))


@dataclass
class TripContext:
    """Rolling safety state for one in-progress trip."""

    trip_id: str
    user_id: str
    state: TripState = TripState.IDLE

    # Last known position
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    last_moved_at: datetime = field(default_factory=_utcnow)
    last_seen_at: datetime = field(default_factory=_utcnow)

    # Escalation bookkeeping
    sos_triggered: bool = False
    sos_triggered_at: Optional[datetime] = None
    last_reroute_at: Optional[datetime] = None
    reroute_count: int = 0

    # Debounce counters
    off_route_count: int = 0
    stationary_secs: int = 0

    # Cached from the telemetry payload
    contacts: list = field(default_factory=list)
    spent_budget: float = 0.0
    fixes_seen: int = 0

    # ── Transitions ─────────────────────────────────────────
    def transition(self, new_state: TripState) -> bool:
        """Move to a new state. Returns True if the state actually changed.

        The return value is what lets callers act on transitions rather than
        on steady state, which is the difference between one alert and a
        continuous stream of them.
        """
        if new_state is self.state:
            return False

        previous = self.state
        self.state = new_state

        level = logging.WARNING if new_state.is_alarming else logging.INFO
        log.log(level, "[%s] %s -> %s", self.trip_id, previous.name, new_state.name)
        return True

    # ── Movement ────────────────────────────────────────────
    def update_position(self, lat: float, lon: float, threshold_m: float) -> bool:
        """Record a fix and update stationary timing. Returns True if moved."""
        now = _utcnow()
        self.last_seen_at = now
        self.fixes_seen += 1

        if self.last_lat is None or self.last_lon is None:
            # First fix: nothing to compare against, so treat it as movement
            # rather than starting the traveller off as already stationary.
            self.last_lat, self.last_lon = lat, lon
            self.last_moved_at = now
            self.stationary_secs = 0
            return True

        distance = haversine_m(self.last_lat, self.last_lon, lat, lon)
        moved = distance > threshold_m

        if moved:
            self.last_moved_at = now
            self.stationary_secs = 0
        else:
            self.stationary_secs = int((now - self.last_moved_at).total_seconds())

        self.last_lat, self.last_lon = lat, lon
        return moved

    # ── Escalation gates ────────────────────────────────────
    def may_trigger_sos(self, retry_after_secs: int) -> bool:
        """Whether an SOS may fire now.

        Blocks repeat escalation while one is active. The retry window covers
        the case where the first attempt failed entirely, so a genuine
        emergency is not permanently suppressed by one bad request.
        """
        if not self.sos_triggered:
            return True
        if self.sos_triggered_at is None:
            return False
        elapsed = (_utcnow() - self.sos_triggered_at).total_seconds()
        return elapsed > retry_after_secs

    def mark_sos(self) -> None:
        self.sos_triggered = True
        self.sos_triggered_at = _utcnow()
        self.transition(TripState.SOS_TRIGGERED)

    def may_reroute(self, cooldown_secs: int) -> bool:
        """Whether a reroute may be offered, respecting the cooldown."""
        if self.last_reroute_at is None:
            return True
        return (_utcnow() - self.last_reroute_at).total_seconds() > cooldown_secs

    def mark_reroute(self) -> None:
        self.last_reroute_at = _utcnow()
        self.reroute_count += 1

    def is_stale(self, ttl_secs: int) -> bool:
        """Whether this context has gone quiet long enough to discard."""
        return (_utcnow() - self.last_seen_at) > timedelta(seconds=ttl_secs)

    def snapshot(self) -> dict[str, Any]:
        """Serialisable view, for the /trips debug endpoint."""
        return {
            "trip_id": self.trip_id,
            "user_id": self.user_id,
            "state": self.state.name,
            "last_lat": self.last_lat,
            "last_lon": self.last_lon,
            "stationary_secs": self.stationary_secs,
            "off_route_count": self.off_route_count,
            "sos_triggered": self.sos_triggered,
            "reroute_count": self.reroute_count,
            "fixes_seen": self.fixes_seen,
            "contacts_known": len(self.contacts),
            "last_seen_at": self.last_seen_at.isoformat(),
        }

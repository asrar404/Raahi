"""Transit option generation.

**This is a synthetic transit model, not a live feed.** It produces
realistically-shaped multi-modal options from a fare and speed model
calibrated against published Indian operator tariffs. It does not know
timetables, real-time delays, service outages, or where stations actually are.

Fare and speed sources (approximate, 2024-25):
  * Delhi Metro   — distance slabs, Rs 10 to Rs 60
  * BEST / DTC bus — distance slabs, Rs 5 to Rs 25
  * Auto rickshaw — Rs 30 flagfall + ~Rs 11/km (Delhi meter)
  * Cab           — Rs 50 base + ~Rs 14/km (aggregator, non-surge)
  * Bike taxi     — Rs 25 base + ~Rs 7/km
  * Suburban rail — Rs 5 to Rs 30 second class

Replacing this module is the single integration point for real data:
implement `fetch_transit_options` against GTFS static + GTFS-RT, the Ola /
Uber / Rapido estimate APIs, and a routing engine such as OSRM or Valhalla.
The rest of the planner is agnostic to where options come from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any, Iterable, Optional, Sequence

from app.schemas.intent import TransitMode

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0


# ============================================================
# Geometry
# ============================================================
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(a)))


def interpolate(
    lat1: float, lon1: float, lat2: float, lon2: float, fraction: float
) -> tuple[float, float]:
    """Point at `fraction` along the straight line between two coordinates.

    Linear rather than great-circle: over intra-city distances the difference
    is centimetres, and this keeps the code obvious.
    """
    return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)


# Road distance exceeds straight-line distance. 1.35 is a typical detour
# index for dense Indian cities; metro alignments are straighter.
ROAD_FACTOR = 1.35
RAIL_FACTOR = 1.15


# ============================================================
# Mode model
# ============================================================
@dataclass(frozen=True)
class ModeProfile:
    """Cost, speed and suitability model for one transit mode."""

    mode: TransitMode
    base_fare: float
    per_km: float
    # Effective door-to-door speed, including stops and traffic
    avg_speed_kmh: float
    # Fixed overhead in minutes: ticketing, waiting, platform time
    access_mins: int
    min_km: float
    max_km: float
    provider: str
    # Baseline safety before any location-specific adjustment (0-5)
    base_safety: float
    # Fare slabs as (upper_km_bound, fare). Takes precedence over per_km.
    slabs: Optional[tuple[tuple[float, float], ...]] = None

    def fare(self, km: float) -> float:
        if self.slabs:
            for bound, price in self.slabs:
                if km <= bound:
                    return price
            return self.slabs[-1][1]
        return round(self.base_fare + self.per_km * km, 2)

    def duration_mins(self, km: float) -> int:
        travel = (km / self.avg_speed_kmh) * 60
        return max(1, int(round(travel + self.access_mins)))

    def suits(self, km: float) -> bool:
        return self.min_km <= km <= self.max_km


PROFILES: dict[TransitMode, ModeProfile] = {
    TransitMode.WALK: ModeProfile(
        mode=TransitMode.WALK,
        base_fare=0.0, per_km=0.0, avg_speed_kmh=4.5, access_mins=0,
        min_km=0.0, max_km=2.5, provider="On foot",
        # Walking is cheap but the most exposed mode, especially after dark
        base_safety=3.0,
    ),
    TransitMode.METRO: ModeProfile(
        mode=TransitMode.METRO,
        base_fare=10.0, per_km=1.8, avg_speed_kmh=32.0, access_mins=8,
        min_km=1.5, max_km=60.0, provider="Metro Rail",
        # Staffed, CCTV-monitored, women's coach available
        base_safety=4.6,
        slabs=((2, 10), (5, 20), (12, 30), (21, 40), (32, 50), (1e9, 60)),
    ),
    TransitMode.BUS: ModeProfile(
        mode=TransitMode.BUS,
        base_fare=5.0, per_km=1.2, avg_speed_kmh=17.0, access_mins=7,
        min_km=1.0, max_km=40.0, provider="City Bus",
        base_safety=3.8,
        slabs=((4, 5), (8, 10), (12, 15), (20, 20), (1e9, 25)),
    ),
    TransitMode.TRAIN: ModeProfile(
        mode=TransitMode.TRAIN,
        base_fare=5.0, per_km=0.9, avg_speed_kmh=35.0, access_mins=9,
        min_km=3.0, max_km=90.0, provider="Suburban Rail",
        # Fast and cheap, but severe overcrowding
        base_safety=3.6,
        slabs=((10, 5), (20, 10), (35, 15), (60, 25), (1e9, 30)),
    ),
    TransitMode.AUTO: ModeProfile(
        mode=TransitMode.AUTO,
        base_fare=30.0, per_km=11.0, avg_speed_kmh=20.0, access_mins=4,
        min_km=0.5, max_km=25.0, provider="Auto Rickshaw",
        base_safety=3.5,
    ),
    TransitMode.CAB: ModeProfile(
        mode=TransitMode.CAB,
        base_fare=50.0, per_km=14.0, avg_speed_kmh=24.0, access_mins=5,
        min_km=1.0, max_km=100.0, provider="Cab",
        # Enclosed, GPS-tracked, driver identity on record
        base_safety=4.2,
    ),
    TransitMode.RAPIDO: ModeProfile(
        mode=TransitMode.RAPIDO,
        base_fare=25.0, per_km=7.0, avg_speed_kmh=26.0, access_mins=3,
        min_km=0.8, max_km=25.0, provider="Bike Taxi",
        # Cheap and quick, but two-wheeler exposure and no enclosure
        base_safety=3.0,
    ),
}


# ============================================================
# Leg construction
# ============================================================
def _leg(
    order: int,
    mode: TransitMode,
    from_name: str,
    from_pt: tuple[float, float],
    to_name: str,
    to_pt: tuple[float, float],
) -> dict[str, Any]:
    """Build one leg dict, applying the appropriate detour factor."""
    profile = PROFILES[mode]
    straight = haversine_km(from_pt[0], from_pt[1], to_pt[0], to_pt[1])
    factor = RAIL_FACTOR if mode in (TransitMode.METRO, TransitMode.TRAIN) else ROAD_FACTOR
    km = round(straight * factor, 3)

    return {
        "leg_order": order,
        "mode": mode.value,
        "from_name": from_name,
        "from_lat": round(from_pt[0], 6),
        "from_lon": round(from_pt[1], 6),
        "to_name": to_name,
        "to_lat": round(to_pt[0], 6),
        "to_lon": round(to_pt[1], 6),
        "distance_km": km,
        "planned_cost": profile.fare(km),
        "duration_mins": profile.duration_mins(km),
        "provider": profile.provider,
        # Replaced by the real PostGIS score in safety_scorer
        "base_safety": profile.base_safety,
    }


def _access_label(mode: TransitMode, place: str) -> str:
    """Plausible boarding-point name near a place."""
    return {
        TransitMode.METRO: f"{place} Metro Station",
        TransitMode.BUS: f"{place} Bus Stop",
        TransitMode.TRAIN: f"{place} Railway Station",
    }.get(mode, place)


# Fraction of the journey spent walking to/from a boarding point.
ACCESS_FRACTION = 0.12
# Absolute cap on an access leg, in km. The fraction alone breaks down over
# distance: 12% of a 14 km trip is a 1.7 km walk at each end, which nobody
# does. Real first/last-mile walks top out around a kilometre, after which
# people take an auto or a feeder bus.
MAX_ACCESS_KM = 1.0
# Below this, the access walk is not worth modelling separately
MIN_ACCESS_KM = 0.35


def _access_fraction(total_km: float) -> float:
    """Access-leg fraction, capped so long trips do not imply long walks."""
    if total_km <= 0:
        return 0.0
    return min(ACCESS_FRACTION, MAX_ACCESS_KM / total_km)


def _direct_option(
    mode: TransitMode,
    src_name: str, src: tuple[float, float],
    dst_name: str, dst: tuple[float, float],
) -> Optional[dict[str, Any]]:
    """Single-vehicle door-to-door option."""
    km = haversine_km(*src, *dst) * ROAD_FACTOR
    profile = PROFILES[mode]
    if not profile.suits(km):
        return None

    leg = _leg(0, mode, src_name, src, dst_name, dst)
    return {
        "legs": [leg],
        "summary": f"Direct {profile.provider.lower()} from {src_name} to {dst_name}",
        "strategy": f"direct_{mode.value}",
    }


def _trunk_option(
    trunk: TransitMode,
    src_name: str, src: tuple[float, float],
    dst_name: str, dst: tuple[float, float],
    access_mode: TransitMode = TransitMode.WALK,
) -> Optional[dict[str, Any]]:
    """access -> trunk -> access, the classic public-transit shape.

    Boarding points are synthesised by interpolating along the corridor. Real
    stations are rarely exactly there, which is precisely why this module is
    the designated replacement point for GTFS data.
    """
    total_km = haversine_km(*src, *dst)
    profile = PROFILES[trunk]
    fraction = _access_fraction(total_km)

    board = interpolate(*src, *dst, fraction)
    alight = interpolate(*src, *dst, 1 - fraction)
    trunk_km = haversine_km(*board, *alight) * RAIL_FACTOR
    if not profile.suits(trunk_km):
        return None

    access_km = total_km * fraction
    board_name = _access_label(trunk, src_name)
    alight_name = _access_label(trunk, dst_name)

    legs: list[dict[str, Any]] = []
    order = 0

    if access_km >= MIN_ACCESS_KM:
        if not PROFILES[access_mode].suits(access_km * ROAD_FACTOR):
            return None
        legs.append(_leg(order, access_mode, src_name, src, board_name, board))
        order += 1
    else:
        board, board_name = src, src_name

    legs.append(_leg(order, trunk, board_name, board, alight_name, alight))
    order += 1

    if access_km >= MIN_ACCESS_KM:
        legs.append(_leg(order, access_mode, alight_name, alight, dst_name, dst))

    access_label = "walk" if access_mode == TransitMode.WALK else access_mode.value
    return {
        "legs": legs,
        "summary": (
            f"{access_label.capitalize()} to {board_name}, "
            f"{profile.provider.lower()} to {alight_name}, then {access_label}"
        ),
        "strategy": f"{access_mode.value}_{trunk.value}",
    }


def _mixed_option(
    trunk: TransitMode,
    last_mile: TransitMode,
    src_name: str, src: tuple[float, float],
    dst_name: str, dst: tuple[float, float],
    access_mode: TransitMode = TransitMode.WALK,
) -> Optional[dict[str, Any]]:
    """access -> trunk -> paid last mile.

    Reflects the common real-world compromise: take the metro most of the way,
    then an auto for the final stretch because the walk is unpleasant or the
    hour is late.
    """
    total_km = haversine_km(*src, *dst)
    if total_km < 3.0:
        return None

    profile = PROFILES[trunk]
    board = interpolate(*src, *dst, _access_fraction(total_km))
    # Leave a longer tail for the paid last mile than for a walk
    alight = interpolate(*src, *dst, 0.78)

    trunk_km = haversine_km(*board, *alight) * RAIL_FACTOR
    last_km = haversine_km(*alight, *dst) * ROAD_FACTOR
    if not profile.suits(trunk_km) or not PROFILES[last_mile].suits(last_km):
        return None

    access_km = haversine_km(*src, *board) * ROAD_FACTOR
    if not PROFILES[access_mode].suits(access_km):
        return None

    board_name = _access_label(trunk, src_name)
    alight_name = f"{dst_name} approach"

    legs = [
        _leg(0, access_mode, src_name, src, board_name, board),
        _leg(1, trunk, board_name, board, alight_name, alight),
        _leg(2, last_mile, alight_name, alight, dst_name, dst),
    ]
    access_label = "Walk" if access_mode == TransitMode.WALK else PROFILES[access_mode].provider
    return {
        "legs": legs,
        "summary": (
            f"{access_label} to {board_name}, {profile.provider.lower()} most of the way, "
            f"then {PROFILES[last_mile].provider.lower()} to {dst_name}"
        ),
        "strategy": f"{trunk.value}_plus_{last_mile.value}",
    }


def _walk_only(
    src_name: str, src: tuple[float, float],
    dst_name: str, dst: tuple[float, float],
) -> Optional[dict[str, Any]]:
    """Walking the whole way, when it is genuinely short and free."""
    km = haversine_km(*src, *dst) * ROAD_FACTOR
    if km > 2.5:
        return None
    return {
        "legs": [_leg(0, TransitMode.WALK, src_name, src, dst_name, dst)],
        "summary": f"Walk from {src_name} to {dst_name} ({km:.1f} km)",
        "strategy": "walk_only",
    }


# ============================================================
# Public entry point
# ============================================================
async def fetch_transit_options(
    src_lat: float,
    src_lon: float,
    dst_lat: float,
    dst_lon: float,
    modes: Sequence[Any],
    src_name: str = "Origin",
    dst_name: str = "Destination",
    max_options: int = 12,
    allow_walking: bool = True,
) -> list[dict[str, Any]]:
    """Generate candidate routes between two points.

    `modes` is the user's preference list. Walking is normally permitted as a
    connector regardless, since no realistic public-transit journey exists
    without it.

    `allow_walking=False` overrides that and substitutes a paid access mode.
    It exists for the risk-zone reroute case: telling someone to walk out of
    an area flagged as dangerous defeats the purpose of the reroute.

    Returns a list of option dicts:
        {"legs": [...], "summary": str, "strategy": str}

    Async so the signature is unchanged when this is swapped for real
    network-backed providers.
    """
    src = (src_lat, src_lon)
    dst = (dst_lat, dst_lon)
    total_km = haversine_km(*src, *dst)

    if total_km < 0.05:
        log.info("Origin and destination are effectively identical (%.3f km)", total_km)
        return []

    allowed = _normalise_modes(modes, allow_walking=allow_walking)

    # Access mode for trunk journeys: on foot normally, auto when walking is
    # off the table.
    access_mode = TransitMode.WALK if allow_walking else TransitMode.AUTO

    log.info(
        "Generating options for %.2f km (%s -> %s) modes=%s walking=%s",
        total_km, src_name, dst_name, [m.value for m in allowed], allow_walking,
    )

    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(option: Optional[dict[str, Any]]) -> None:
        if option is None:
            return
        # Deduplicate on the traveller's view of the route: mode sequence plus
        # a 10-rupee cost bucket. Different generation strategies routinely
        # converge on the same practical journey, and two cards reading
        # "Auto → Metro → Auto" a rupee apart is just a wasted slot.
        signature = "|".join(leg["mode"] for leg in option["legs"])
        cost_bucket = round(sum(leg["planned_cost"] for leg in option["legs"]) / 10)
        key = f"{signature}:{cost_bucket}"
        if key in seen:
            return
        seen.add(key)
        options.append(option)

    # Walk-only, when plausible
    if allow_walking and TransitMode.WALK in allowed:
        add(_walk_only(src_name, src, dst_name, dst))

    # Trunk public transit with access legs
    for trunk in (TransitMode.METRO, TransitMode.TRAIN, TransitMode.BUS):
        if trunk in allowed:
            add(_trunk_option(trunk, src_name, src, dst_name, dst, access_mode=access_mode))

    # Direct private modes
    for mode in (TransitMode.AUTO, TransitMode.RAPIDO, TransitMode.CAB):
        if mode in allowed:
            add(_direct_option(mode, src_name, src, dst_name, dst))

    # Trunk plus paid last mile
    for trunk in (TransitMode.METRO, TransitMode.TRAIN):
        if trunk not in allowed:
            continue
        for last_mile in (TransitMode.AUTO, TransitMode.RAPIDO):
            if last_mile in allowed:
                add(_mixed_option(trunk, last_mile, src_name, src, dst_name, dst,
                                  access_mode=access_mode))

    # Auto to the metro, for when the access walk is too long
    if (allow_walking and TransitMode.METRO in allowed
            and TransitMode.AUTO in allowed and total_km > 6):
        add(_trunk_option(
            TransitMode.METRO, src_name, src, dst_name, dst,
            access_mode=TransitMode.AUTO,
        ))

    # Last resort: never return nothing. A traveller with an impossible
    # preference set is better served by one workable suggestion.
    if not options:
        log.warning("No option matched the preferred modes; falling back to auto/cab")
        add(_direct_option(TransitMode.AUTO, src_name, src, dst_name, dst))
        add(_direct_option(TransitMode.CAB, src_name, src, dst_name, dst))

    log.info("Generated %d candidate options", len(options))
    return options[:max_options]


def _normalise_modes(modes: Iterable[Any], allow_walking: bool = True) -> set[TransitMode]:
    """Coerce mixed strings/enums into a mode set.

    Walking is added as a connector unless explicitly disallowed.
    """
    allowed: set[TransitMode] = set()
    for m in modes or []:
        if isinstance(m, TransitMode):
            allowed.add(m)
            continue
        try:
            allowed.add(TransitMode(str(m).strip().lower()))
        except ValueError:
            log.debug("Ignoring unknown mode %r", m)

    if not allowed or allowed == {TransitMode.WALK}:
        allowed |= {TransitMode.METRO, TransitMode.BUS, TransitMode.AUTO}

    if allow_walking:
        # Walking is the glue between every other mode
        allowed.add(TransitMode.WALK)
    else:
        allowed.discard(TransitMode.WALK)
        # Something has to cover the first and last mile
        allowed.add(TransitMode.AUTO)

    return allowed


def estimate_fare(mode: str, km: float) -> Optional[float]:
    """Fare for one mode over a distance. Exposed for UI estimates."""
    try:
        return PROFILES[TransitMode(mode)].fare(km)
    except (KeyError, ValueError):
        return None

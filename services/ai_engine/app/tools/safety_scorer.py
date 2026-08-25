"""Route safety scoring.

Safety lives in PostGIS (zone polygons plus live crowdsourced reports), which
the AI engine has no direct access to, so scores come from the gateway's
`POST /api/v1/safety/score-points` endpoint.

Two decisions worth noting:

* **One batched call per route, not one per leg.** A plan with 12 candidate
  options and 3 legs each is 36 points; batching turns 36 round trips into 1.
* **A gateway outage degrades rather than fails.** The mode's baseline safety
  is used instead. Refusing to plan a journey because the safety service is
  down would leave the traveller with no options at all, which is the worse
  outcome. Any route scored this way is flagged so the UI can say so.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import httpx

from app.config import settings
from app.schemas.intent import TransitMode

log = logging.getLogger(__name__)

# Weight of the location score against the mode's inherent safety. Where you
# are matters more than what you are travelling in, but not overwhelmingly:
# a cab through a risky area is safer than walking through it.
W_LOCATION = 0.65
W_MODE = 0.35

# Walking legs are more exposed to their surroundings than enclosed vehicles,
# so location risk counts for more of their score.
W_LOCATION_WALK = 0.85
W_MODE_WALK = 0.15


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.INTERNAL_API_KEY:
        headers["X-Internal-Token"] = settings.INTERNAL_API_KEY
    return headers


async def _fetch_scores(
    points: Sequence[tuple[float, float]], night_mode: Optional[bool] = None
) -> Optional[list[float]]:
    """Batch-score coordinates via the gateway. None means unavailable."""
    if not points:
        return []

    payload = {
        "points": [{"lat": lat, "lon": lon} for lat, lon in points],
        "night_mode": night_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.BACKEND_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.BACKEND_URL}/api/v1/safety/score-points",
                json=payload,
                headers=_headers(),
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        log.warning("Safety scoring rejected (%s): %s",
                    exc.response.status_code, exc.response.text[:200])
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Safety scoring unavailable: %s", exc)
        return None

    scores = data.get("scores")
    if not isinstance(scores, list) or len(scores) != len(points):
        log.warning("Safety scoring returned %s scores for %d points",
                    len(scores) if isinstance(scores, list) else "invalid", len(points))
        return None

    return [float(s) for s in scores]


def _blend(location_score: float, mode: str) -> float:
    """Combine a location score with the mode's inherent safety."""
    from app.tools.transit_api import PROFILES

    try:
        mode_safety = PROFILES[TransitMode(mode)].base_safety
    except (KeyError, ValueError):
        mode_safety = 3.5

    is_walk = mode == TransitMode.WALK.value
    w_loc = W_LOCATION_WALK if is_walk else W_LOCATION
    w_mode = W_MODE_WALK if is_walk else W_MODE

    return round(max(0.0, min(5.0, location_score * w_loc + mode_safety * w_mode)), 2)


def _midpoint(leg: dict[str, Any]) -> tuple[float, float]:
    return (
        (float(leg["from_lat"]) + float(leg["to_lat"])) / 2,
        (float(leg["from_lon"]) + float(leg["to_lon"])) / 2,
    )


async def score_route_safety(
    legs: Sequence[dict[str, Any]], night_mode: Optional[bool] = None
) -> float:
    """Aggregate safety score for one route's legs (0-5, 5 = safest).

    Kept for compatibility with the single-route call shape. Prefer
    `score_options` when scoring a whole candidate set.
    """
    if not legs:
        return settings.FALLBACK_SAFETY_SCORE

    scores = await _fetch_scores([_midpoint(leg) for leg in legs], night_mode)
    if scores is None:
        scores = [settings.FALLBACK_SAFETY_SCORE] * len(legs)

    blended = [_blend(s, leg["mode"]) for s, leg in zip(scores, legs)]
    return _aggregate(blended, legs)


def _aggregate(leg_scores: Sequence[float], legs: Sequence[dict[str, Any]]) -> float:
    """Distance-weighted mean, pulled down by the worst leg.

    A pure average lets a long safe metro ride mask a 15-minute walk through
    somewhere genuinely dangerous — which is exactly the part of the journey
    the traveller needs warning about. The worst leg therefore carries 30% of
    the final score.
    """
    if not leg_scores:
        return settings.FALLBACK_SAFETY_SCORE

    weights = [max(float(leg.get("distance_km") or 0.1), 0.1) for leg in legs]
    total_weight = sum(weights)
    weighted = sum(s * w for s, w in zip(leg_scores, weights)) / total_weight
    worst = min(leg_scores)

    return round(max(0.0, min(5.0, weighted * 0.7 + worst * 0.3)), 2)


async def score_options(
    options: Sequence[dict[str, Any]], night_mode: Optional[bool] = None
) -> tuple[list[float], bool]:
    """Score every option in one round trip.

    Returns (per-option scores, degraded) where `degraded` is True when the
    gateway was unreachable and baselines were substituted. Each leg is also
    annotated in place with its own `safety_score`, so the UI can highlight
    the risky segment rather than only the route total.
    """
    if not options:
        return [], False

    # Flatten every leg midpoint, remembering which option it came from
    points: list[tuple[float, float]] = []
    spans: list[tuple[int, int]] = []
    for option in options:
        start = len(points)
        for leg in option["legs"]:
            points.append(_midpoint(leg))
        spans.append((start, len(points)))

    scores = await _fetch_scores(points, night_mode)
    degraded = scores is None
    if scores is None:
        log.warning(
            "Falling back to mode baselines for %d legs across %d options",
            len(points), len(options),
        )
        scores = [settings.FALLBACK_SAFETY_SCORE] * len(points)

    results: list[float] = []
    for option, (start, end) in zip(options, spans):
        legs = option["legs"]
        blended = [
            _blend(score, leg["mode"])
            for score, leg in zip(scores[start:end], legs)
        ]
        # Annotate in place so the client can surface the weakest leg
        for leg, leg_score in zip(legs, blended):
            leg["safety_score"] = leg_score
        results.append(_aggregate(blended, legs))

    return results, degraded


async def health() -> dict[str, Any]:
    """Whether the safety scoring backend is reachable."""
    scores = await _fetch_scores([(28.6315, 77.2167)])
    return {
        "reachable": scores is not None,
        "backend_url": settings.BACKEND_URL,
        "sample_score": scores[0] if scores else None,
    }

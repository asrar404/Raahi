"""Route planning and ranking.

The pipeline is deliberately deterministic once intent is parsed:

    generate options -> filter by budget -> score safety -> rank by utility

The LLM's only involvement is extracting intent upstream. Fares, durations and
safety scores all come from explicit models, because a traveller deciding
whether they can afford to get home needs a number that is reproducible and
auditable, not one a language model produced.

Utility:

    U = W_COST * norm_cost + W_TIME * norm_time + W_SAFETY * norm_safety

with each term normalised to 0-1 and higher always meaning better.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from app.config import settings
from app.schemas.intent import ParsedIntent, PlannedRoute, RouteLeg, TransitMode
from app.tools.budget_filter import filter_with_report, route_cost
from app.tools.safety_scorer import score_options
from app.tools.transit_api import PROFILES, fetch_transit_options

log = logging.getLogger(__name__)


def compute_utility(
    cost: float,
    duration: int,
    safety: float,
    budget: float,
    max_duration: int | None = None,
) -> float:
    """Blend cost, time and safety into a single 0-1 score.

    Budget is floored at MIN_BUDGET: a zero or negative ceiling (possible
    mid-journey once spending is subtracted) would divide by zero.
    """
    max_duration = max_duration or settings.MAX_DURATION_MINS
    safe_budget = max(budget, settings.MIN_BUDGET)

    # Cheaper is better; anything at or above the ceiling scores 0
    norm_cost = 1.0 - min(cost / safe_budget, 1.0)
    # Faster is better; anything at or beyond max_duration scores 0
    norm_time = 1.0 - min(duration / max(max_duration, 1), 1.0)
    # Safety is already 0-5
    norm_safety = max(0.0, min(safety / 5.0, 1.0))

    weight_sum = settings.weight_sum or 1.0
    utility = (
        settings.W_COST * norm_cost
        + settings.W_TIME * norm_time
        + settings.W_SAFETY * norm_safety
    ) / weight_sum

    return round(max(0.0, min(1.0, utility)), 4)


def _describe(option: dict[str, Any], safety: float, cost: float, duration: int) -> str:
    """Human-readable summary, used when the generator did not supply one."""
    if option.get("summary"):
        return option["summary"]

    modes = [leg["mode"] for leg in option["legs"]]
    labels = [PROFILES[TransitMode(m)].provider for m in modes]
    return f"{' → '.join(labels)} · ₹{cost:.0f} · {duration} min · safety {safety:.1f}/5"


def _warnings_for(
    option: dict[str, Any],
    safety: float,
    cost: float,
    intent: ParsedIntent,
) -> list[str]:
    """Surface anything the traveller should know before committing.

    These are shown on the route card. Omitting them would mean presenting a
    route that crosses a flagged zone as though it were unremarkable.
    """
    notes: list[str] = []
    legs = option["legs"]

    if option.get("over_budget"):
        notes.append(
            f"₹{option.get('excess_inr', 0):.0f} over your ₹{intent.budget_ceiling:.0f} budget"
        )
    elif cost > intent.budget_ceiling * 0.8:
        notes.append(f"Uses {cost / max(intent.budget_ceiling, 1) * 100:.0f}% of your budget")

    walk_km = sum(
        float(leg.get("distance_km") or 0)
        for leg in legs if leg["mode"] == TransitMode.WALK.value
    )
    if intent.night_travel and walk_km > 0.5:
        notes.append(f"{walk_km:.1f} km of walking after dark")
    elif walk_km > 1.5:
        notes.append(f"{walk_km:.1f} km of walking")

    weakest = min((float(leg.get("safety_score") or 5) for leg in legs), default=5.0)
    if weakest < 3.0:
        weak_leg = min(legs, key=lambda l: float(l.get("safety_score") or 5))
        notes.append(
            f"The {weak_leg['mode']} leg to {weak_leg['to_name']} scores "
            f"{weakest:.1f}/5 on safety"
        )

    transfers = len([leg for leg in legs if leg["mode"] != TransitMode.WALK.value]) - 1
    if transfers >= 2:
        notes.append(f"{transfers} transfers")

    if safety < settings.MIN_SAFETY_FOR_PRIORITY and intent.safety_priority:
        notes.append("Below your safety preference — shown because options are limited")

    return notes


def _to_route(
    option: dict[str, Any],
    safety: float,
    intent: ParsedIntent,
) -> Optional[PlannedRoute]:
    """Build a validated PlannedRoute, or None if the legs are unusable."""
    legs: list[RouteLeg] = []

    for raw in option["legs"]:
        payload = {k: v for k, v in raw.items() if k != "base_safety"}
        # score_options annotates each leg; fall back to the route score.
        payload["safety_score"] = float(payload.get("safety_score") or safety)
        try:
            legs.append(RouteLeg(**payload))
        except Exception as exc:  # noqa: BLE001
            log.warning("Dropping malformed leg %r: %s", raw.get("to_name"), exc)
            return None

    if not legs:
        return None

    cost = round(sum(leg.planned_cost for leg in legs), 2)
    duration = sum(leg.duration_mins for leg in legs)
    utility = compute_utility(cost, duration, safety, intent.budget_ceiling)

    return PlannedRoute(
        legs=legs,
        total_cost=cost,
        total_duration=duration,
        utility_score=utility,
        safety_rating=safety,
        summary=_describe(option, safety, cost, duration),
        warnings=_warnings_for(option, safety, cost, intent),
    )


async def generate_routes(
    intent: ParsedIntent,
    src_name: Optional[str] = None,
    dst_name: Optional[str] = None,
    night_mode: Optional[bool] = None,
    allow_walking: bool = True,
) -> tuple[list[PlannedRoute], list[str]]:
    """Produce ranked routes for a resolved intent.

    `allow_walking=False` forbids walking legs entirely, substituting a paid
    access mode. Used when rerouting someone out of a high-risk zone.

    Returns (routes, warnings). Raises ValueError when coordinates are missing
    — geocoding is the caller's job and there is nothing sensible to plan
    without it.
    """
    if not intent.has_coordinates:
        raise ValueError(
            "Intent is missing coordinates; geocode source and destination first"
        )

    warnings: list[str] = []
    night = intent.night_travel if night_mode is None else night_mode

    # ── 1. Candidate generation ─────────────────────────────
    options = await fetch_transit_options(
        src_lat=intent.source_lat,
        src_lon=intent.source_lon,
        dst_lat=intent.dest_lat,
        dst_lon=intent.dest_lon,
        modes=intent.preferred_modes,
        src_name=src_name or intent.source_raw,
        dst_name=dst_name or intent.destination_raw,
        allow_walking=allow_walking,
    )
    if not options:
        return [], ["No viable route between those two points"]

    # ── 2. Hard budget filter ───────────────────────────────
    affordable, report = filter_with_report(options, intent.budget_ceiling)
    if report["over_budget"]:
        warnings.append(
            f"Nothing fits ₹{intent.budget_ceiling:.0f}. The cheapest option is "
            f"₹{report['cheapest']:.0f}."
        )
    elif report["dropped"]:
        warnings.append(
            f"{report['dropped']} option(s) were over your ₹{intent.budget_ceiling:.0f} budget"
        )

    # ── 3. Safety scoring (single batched call) ─────────────
    safety_scores, degraded = await score_options(affordable, night_mode=night)
    if degraded:
        warnings.append(
            "Live safety data was unavailable; scores are estimates based on transit mode"
        )

    # ── 4. Build and rank ───────────────────────────────────
    routes: list[PlannedRoute] = []
    for option, safety in zip(affordable, safety_scores):
        route = _to_route(option, safety, intent)
        if route is not None:
            routes.append(route)

    if not routes:
        return [], warnings + ["Could not build a valid route from the available options"]

    # When safety was requested, drop clearly unsafe routes — but never all of
    # them. Leaving someone with zero options is not a safety outcome.
    if intent.safety_priority:
        acceptable = [r for r in routes if r.safety_rating >= settings.MIN_SAFETY_FOR_PRIORITY]
        if acceptable:
            dropped = len(routes) - len(acceptable)
            if dropped:
                warnings.append(f"{dropped} option(s) were filtered out as too unsafe")
            routes = acceptable
        else:
            warnings.append(
                "Every available option scores low on safety. Consider travelling later "
                "or with company."
            )

    routes.sort(key=lambda r: (r.utility_score, r.safety_rating), reverse=True)
    top = routes[: settings.TOP_N_ROUTES]

    log.info(
        "Ranked %d routes (returning %d): %s",
        len(routes), len(top),
        [f"{r.mode_sequence}=U{r.utility_score:.3f}/S{r.safety_rating:.1f}/₹{r.total_cost:.0f}"
         for r in top],
    )
    return top, warnings


def rank_only(
    routes: Sequence[PlannedRoute], budget: float, max_duration: int | None = None
) -> list[PlannedRoute]:
    """Recompute utility and re-sort an existing set of routes.

    Used on reroute, where the remaining budget differs from the original and
    the same routes therefore rank differently.
    """
    for route in routes:
        route.utility_score = compute_utility(
            route.total_cost, route.total_duration, route.safety_rating, budget, max_duration
        )
    return sorted(routes, key=lambda r: (r.utility_score, r.safety_rating), reverse=True)

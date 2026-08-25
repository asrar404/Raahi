"""Mid-journey replanning.

Rerouting is not the same problem as planning. The traveller is already out
there, partway through, possibly lost or frightened, and the constraints have
changed:

* The origin is wherever they are now, not where they started.
* The budget is what remains after what they have already spent.
* Simplicity beats optimality. Someone who is stranded should not be handed a
  three-transfer itinerary.
* On a risk_zone trigger, leaving the area outranks saving money.

The trigger therefore reshapes the intent before replanning, rather than just
re-running the original plan from a new point.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.schemas.intent import ParsedIntent, PlannedRoute, TransitMode
from app.schemas.replan import RerouteTrigger
from app.tools.budget_filter import remaining_budget

log = logging.getLogger(__name__)

# Enclosed, tracked modes. Preferred when the trigger is a risk zone or when
# replanning after dark.
SAFE_MODES = (TransitMode.CAB, TransitMode.AUTO, TransitMode.METRO)


def adjust_intent_for_reroute(
    intent: ParsedIntent,
    current_lat: float,
    current_lon: float,
    spent_budget: float,
    trigger: RerouteTrigger,
    night_mode: Optional[bool] = None,
) -> tuple[ParsedIntent, list[str], bool]:
    """Rewrite the intent to reflect the current situation.

    Returns (adjusted intent, warnings, allow_walking).

    `allow_walking` is returned separately rather than encoded by removing
    WALK from preferred_modes, because the route generator treats walking as a
    universal connector and adds it back regardless. On a risk_zone trigger
    that would produce exactly the advice we must not give: walk out of the
    dangerous area.
    """
    warnings: list[str] = []
    night = intent.night_travel if night_mode is None else night_mode
    allow_walking = True

    budget_left = remaining_budget(intent.budget_ceiling, spent_budget, settings.MIN_BUDGET)
    if spent_budget >= intent.budget_ceiling:
        warnings.append(
            f"You have already spent ₹{spent_budget:.0f} of your ₹{intent.budget_ceiling:.0f} "
            f"budget. Showing options up to ₹{budget_left:.0f}."
        )

    modes = list(intent.preferred_modes)
    safety_priority = intent.safety_priority

    if trigger == RerouteTrigger.RISK_ZONE:
        # Getting out takes precedence over cost. Add enclosed modes even if
        # the traveller originally excluded them.
        for mode in SAFE_MODES:
            if mode not in modes:
                modes.append(mode)
        modes = [m for m in modes if m != TransitMode.WALK] or [TransitMode.AUTO]
        allow_walking = False
        safety_priority = True
        warnings.append("Prioritising getting you out of this area over cost")

    elif trigger == RerouteTrigger.DELAY:
        # Stationary for a long time usually means the expected service never
        # came. Offer on-demand modes.
        for mode in (TransitMode.AUTO, TransitMode.RAPIDO, TransitMode.CAB):
            if mode not in modes:
                modes.append(mode)
        warnings.append("You have not moved in a while — including on-demand options")

    elif trigger == RerouteTrigger.BUDGET:
        # Strip the expensive modes and lean on public transit
        modes = [m for m in modes if m != TransitMode.CAB] or [TransitMode.BUS]
        warnings.append("Filtering to cheaper options to protect your remaining budget")

    if night and allow_walking and trigger != RerouteTrigger.OFF_ROUTE:
        warnings.append("Minimising walking legs because it is after dark")

    adjusted = intent.model_copy(update={
        "source_raw": "Current location",
        "source_lat": current_lat,
        "source_lon": current_lon,
        "budget_ceiling": budget_left,
        "preferred_modes": modes,
        "safety_priority": safety_priority,
        "night_travel": night,
    })

    log.info(
        "Reroute intent adjusted (trigger=%s): budget ₹%.0f -> ₹%.0f, modes %s, walking=%s",
        trigger.value, intent.budget_ceiling, budget_left,
        [m.value for m in modes], allow_walking,
    )
    return adjusted, warnings, allow_walking


def prefer_simple(routes: list[PlannedRoute], trigger: RerouteTrigger) -> list[PlannedRoute]:
    """Re-rank to favour simpler routes when the situation is urgent.

    A mid-journey reroute is not the moment to optimise the last few rupees.
    Fewer transfers and less walking matter more, so utility is nudged rather
    than trusted outright.
    """
    if trigger not in (RerouteTrigger.RISK_ZONE, RerouteTrigger.DELAY):
        return routes

    def penalty(route: PlannedRoute) -> float:
        walk_km = sum(
            leg.distance_km for leg in route.legs if leg.mode == TransitMode.WALK
        )
        # 4% per transfer, 3% per km on foot
        return route.transfers * 0.04 + walk_km * 0.03

    adjusted = sorted(
        routes,
        key=lambda r: (r.utility_score - penalty(r), r.safety_rating),
        reverse=True,
    )

    if adjusted and adjusted[0] is not routes[0]:
        log.info("Reroute re-ranked to favour a simpler itinerary")
    return adjusted

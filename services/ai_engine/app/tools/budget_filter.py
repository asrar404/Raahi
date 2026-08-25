"""Budget filtering.

The budget ceiling is a hard constraint, not a preference. Someone travelling
on Rs 150 cannot take a Rs 400 cab, no matter how well it scores on safety or
speed, so over-budget options are removed before ranking rather than being
penalised during it.

`filter_with_report` additionally explains what was dropped, so the UI can say
"3 faster options were over your Rs 150 budget" instead of silently showing
fewer results.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

log = logging.getLogger(__name__)

# Fares are estimates, so allow a small tolerance rather than discarding an
# option that lands one rupee over on a rounding boundary.
TOLERANCE_INR = 1.0


def route_cost(option: dict[str, Any]) -> float:
    """Total planned cost of an option."""
    return round(sum(float(leg.get("planned_cost") or 0) for leg in option.get("legs", [])), 2)


def hard_filter_budget(
    routes: Sequence[dict[str, Any]], ceiling: float
) -> list[dict[str, Any]]:
    """Drop every option whose total cost exceeds the ceiling."""
    if ceiling <= 0:
        log.warning("Non-positive budget ceiling (%s); returning routes unfiltered", ceiling)
        return list(routes)

    limit = ceiling + TOLERANCE_INR
    kept = [r for r in routes if route_cost(r) <= limit]

    dropped = len(routes) - len(kept)
    if dropped:
        log.info("Budget filter removed %d/%d options over Rs %.2f",
                 dropped, len(routes), ceiling)
    return kept


def filter_with_report(
    routes: Sequence[dict[str, Any]], ceiling: float
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter by budget and describe what happened.

    When nothing fits, the cheapest option is returned anyway, flagged
    `over_budget`. A traveller who needs to get somewhere is better served by
    "the cheapest way is Rs 210, which is Rs 60 over" than by an empty screen.
    """
    if not routes:
        return [], {"kept": 0, "dropped": 0, "cheapest": None, "over_budget": False}

    costs = [route_cost(r) for r in routes]
    cheapest = min(costs)
    kept = hard_filter_budget(routes, ceiling)

    report: dict[str, Any] = {
        "kept": len(kept),
        "dropped": len(routes) - len(kept),
        "cheapest": cheapest,
        "ceiling": ceiling,
        "over_budget": False,
    }

    if not kept:
        cheapest_option = routes[costs.index(cheapest)]
        cheapest_option["over_budget"] = True
        cheapest_option["excess_inr"] = round(cheapest - ceiling, 2)
        report["over_budget"] = True
        report["kept"] = 1
        log.warning(
            "No option fits Rs %.2f; returning the cheapest at Rs %.2f (Rs %.2f over)",
            ceiling, cheapest, cheapest - ceiling,
        )
        return [cheapest_option], report

    return kept, report


def remaining_budget(ceiling: float, spent: float, floor: float = 10.0) -> float:
    """Budget still available mid-journey.

    Clamped to `floor` rather than zero or a negative number: a traveller who
    has already overspent still needs a way home, and a zero ceiling would
    filter out every option including walking.
    """
    return max(floor, round(ceiling - spent, 2))

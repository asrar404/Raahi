"""Reroute graph.

    adjust_intent -> replan -> simplify -> END

`adjust_intent` moves the origin to the traveller's current position, subtracts
what they have already spent, and reshapes mode preferences according to what
triggered the reroute. `simplify` then re-ranks to favour fewer transfers,
because a mid-journey replan is not the moment to optimise the last few rupees.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.planner_agent import generate_routes
from app.agents.reroute_agent import adjust_intent_for_reroute, prefer_simple
from app.schemas.intent import ParsedIntent, PlannedRoute
from app.schemas.replan import RerouteTrigger

log = logging.getLogger(__name__)


class RerouteState(TypedDict, total=False):
    # Input
    trip_id: str
    intent: ParsedIntent
    current_lat: float
    current_lon: float
    elapsed_mins: int
    spent_budget: float
    trigger: RerouteTrigger
    night_mode: Optional[bool]

    # Working state
    adjusted_intent: Optional[ParsedIntent]
    new_routes: Optional[List[PlannedRoute]]
    remaining_budget: Optional[float]
    allow_walking: bool

    # Diagnostics
    error: Optional[str]
    warnings: List[str]


async def node_adjust_intent(state: RerouteState) -> RerouteState:
    """Rebase the intent on the current position and remaining budget."""
    try:
        adjusted, warnings, allow_walking = adjust_intent_for_reroute(
            intent=state["intent"],
            current_lat=state["current_lat"],
            current_lon=state["current_lon"],
            spent_budget=state.get("spent_budget", 0.0),
            trigger=state["trigger"],
            night_mode=state.get("night_mode"),
        )
        return {
            **state,
            "adjusted_intent": adjusted,
            "remaining_budget": adjusted.budget_ceiling,
            "allow_walking": allow_walking,
            "warnings": list(state.get("warnings", [])) + warnings,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Reroute intent adjustment failed")
        return {**state, "error": f"Could not adjust the plan: {exc}"}


async def node_replan(state: RerouteState) -> RerouteState:
    """Generate fresh routes from the traveller's current position."""
    intent = state.get("adjusted_intent")
    if intent is None:
        return {**state, "error": "No adjusted intent to replan from"}

    if not intent.has_coordinates:
        # The destination came from the stored trip, so this should not happen.
        return {**state, "error": "Destination coordinates are missing from the trip"}

    try:
        routes, warnings = await generate_routes(
            intent,
            src_name="Current location",
            dst_name=intent.destination_raw,
            night_mode=state.get("night_mode"),
            allow_walking=state.get("allow_walking", True),
        )
        return {
            **state,
            "new_routes": routes,
            "warnings": list(state.get("warnings", [])) + warnings,
            "error": None if routes else "No alternative route found from here",
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Replanning failed")
        return {**state, "error": f"Replanning failed: {exc}"}


async def node_simplify(state: RerouteState) -> RerouteState:
    """Favour fewer transfers and less walking when the situation is urgent."""
    routes = state.get("new_routes")
    if not routes:
        return state
    return {**state, "new_routes": prefer_simple(routes, state["trigger"])}


def after_adjust(state: RerouteState) -> str:
    return END if state.get("error") else "replan"


def after_replan(state: RerouteState) -> str:
    # Still run simplify on a partial result; the error is reported alongside.
    return "simplify" if state.get("new_routes") else END


def _build_graph():
    workflow = StateGraph(RerouteState)
    workflow.add_node("adjust_intent", node_adjust_intent)
    workflow.add_node("replan", node_replan)
    workflow.add_node("simplify", node_simplify)

    workflow.set_entry_point("adjust_intent")
    workflow.add_conditional_edges("adjust_intent", after_adjust,
                                   {"replan": "replan", END: END})
    workflow.add_conditional_edges("replan", after_replan,
                                   {"simplify": "simplify", END: END})
    workflow.add_edge("simplify", END)
    return workflow.compile()


reroute_graph = _build_graph()


async def run_reroute(
    trip_id: str,
    intent: ParsedIntent,
    lat: float,
    lon: float,
    elapsed: int = 0,
    spent: float = 0.0,
    trigger: str | RerouteTrigger = RerouteTrigger.MANUAL,
    night_mode: Optional[bool] = None,
) -> dict[str, Any]:
    """Run the reroute workflow end to end."""
    started = time.perf_counter()

    if isinstance(trigger, str):
        try:
            trigger = RerouteTrigger(trigger)
        except ValueError:
            log.warning("Unknown reroute trigger %r, treating as manual", trigger)
            trigger = RerouteTrigger.MANUAL

    initial: RerouteState = {
        "trip_id": trip_id,
        "intent": intent,
        "current_lat": lat,
        "current_lon": lon,
        "elapsed_mins": elapsed,
        "spent_budget": spent,
        "trigger": trigger,
        "night_mode": night_mode,
        "adjusted_intent": None,
        "new_routes": None,
        "remaining_budget": None,
        "error": None,
        "warnings": [],
    }

    result = await reroute_graph.ainvoke(initial)
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)

    log.info(
        "Reroute for trip %s (%s) finished in %dms: %d routes, error=%s",
        trip_id, trigger.value, result["duration_ms"],
        len(result.get("new_routes") or []), result.get("error"),
    )
    return result

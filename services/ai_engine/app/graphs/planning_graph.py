"""Planning graph.

    parse_intent -> resolve_places -> plan_route -> (retry?) -> END

`resolve_places` is the node the original design was missing. The parser
returns place *names*; route generation needs coordinates. Without a geocoding
step in between, planning fails on every request that does not already carry
lat/lon.

Retries re-enter at `plan_route` rather than `parse_intent`. Re-parsing is
pointless when the parse succeeded and it was generation that failed, and the
LLM call is the slowest step in the graph.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.intent_parser import parse_intent
from app.agents.planner_agent import generate_routes
from app.schemas.intent import ParsedIntent, PlannedRoute
from app.schemas.route import ResolvedPlace
from app.tools.geocode import geocode, infer_city

log = logging.getLogger(__name__)

MAX_RETRIES = 2


class PlanningState(TypedDict, total=False):
    # Input
    user_input: str
    device_lat: Optional[float]
    device_lon: Optional[float]
    city_hint: Optional[str]
    night_mode: Optional[bool]

    # Working state
    parsed_intent: Optional[ParsedIntent]
    origin: Optional[ResolvedPlace]
    destination: Optional[ResolvedPlace]
    routes: Optional[List[PlannedRoute]]

    # Diagnostics
    error: Optional[str]
    warnings: List[str]
    retry_count: int
    used_fallback_parser: bool


# ============================================================
# Nodes
# ============================================================
async def node_parse_intent(state: PlanningState) -> PlanningState:
    """Extract structured intent from the request."""
    try:
        intent, used_fallback = await parse_intent(
            state["user_input"],
            device_lat=state.get("device_lat"),
            device_lon=state.get("device_lon"),
            city=state.get("city_hint"),
        )
        warnings = list(state.get("warnings", []))
        if used_fallback:
            warnings.append(
                "Parsed without the language model. Try 'from <place> to <place> "
                "under ₹<amount>' if this looks wrong."
            )
        if intent.confidence < 0.5:
            warnings.append(
                f"Low confidence reading your request (heading to "
                f"'{intent.destination_raw}'). Please check before starting."
            )
        return {
            **state,
            "parsed_intent": intent,
            "used_fallback_parser": used_fallback,
            "warnings": warnings,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Intent parsing failed")
        return {**state, "error": f"Could not understand the request: {exc}"}


async def node_resolve_places(state: PlanningState) -> PlanningState:
    """Turn place names into coordinates."""
    intent = state.get("parsed_intent")
    if intent is None:
        return {**state, "error": "No intent to resolve"}

    warnings = list(state.get("warnings", []))
    city_hint = state.get("city_hint") or intent.city

    origin: Optional[ResolvedPlace] = None
    destination: Optional[ResolvedPlace] = None

    # Coordinates already on the intent (device position, or a replan)
    if intent.source_lat is not None and intent.source_lon is not None:
        origin = ResolvedPlace(
            query=intent.source_raw, name=intent.source_raw,
            lat=intent.source_lat, lon=intent.source_lon,
            city=city_hint, source="provided", confidence=1.0,
        )
    else:
        origin = await geocode(
            intent.source_raw, city_hint,
            state.get("device_lat"), state.get("device_lon"),
        )

    # Resolve the destination first if the origin gave us a better city hint
    destination = await geocode(
        intent.destination_raw,
        city_hint or (origin.city if origin else None),
        state.get("device_lat"), state.get("device_lon"),
    )

    # Retry the origin with the destination's city — helps for
    # "sector 18 to Connaught Place", where only the latter names a city
    if origin is None and destination is not None and destination.city:
        origin = await geocode(intent.source_raw, destination.city,
                               state.get("device_lat"), state.get("device_lon"))

    if origin is None:
        return {
            **state,
            "error": (
                f"Could not find '{intent.source_raw}'. Try a nearby landmark or "
                "metro station, or enable location access."
            ),
            "warnings": warnings,
        }
    if destination is None:
        return {
            **state,
            "error": (
                f"Could not find '{intent.destination_raw}'. Try a nearby landmark "
                "or metro station."
            ),
            "warnings": warnings,
        }

    for place, label in ((origin, "origin"), (destination, "destination")):
        if place.confidence < 0.8:
            warnings.append(
                f"Interpreted the {label} '{place.query}' as {place.name} — "
                "please confirm."
            )

    resolved_city = infer_city(origin, destination)
    updated_intent = intent.model_copy(update={
        "source_lat": origin.lat, "source_lon": origin.lon,
        "dest_lat": destination.lat, "dest_lon": destination.lon,
        "city": resolved_city or intent.city,
    })

    log.info(
        "Resolved %s (%.4f,%.4f) -> %s (%.4f,%.4f) in %s",
        origin.name, origin.lat, origin.lon,
        destination.name, destination.lat, destination.lon, resolved_city,
    )

    return {
        **state,
        "parsed_intent": updated_intent,
        "origin": origin,
        "destination": destination,
        "warnings": warnings,
        "error": None,
    }


async def node_plan_route(state: PlanningState) -> PlanningState:
    """Generate, score and rank candidate routes."""
    intent = state.get("parsed_intent")
    if intent is None:
        return {**state, "error": "No intent to plan from"}

    try:
        origin = state.get("origin")
        destination = state.get("destination")
        routes, plan_warnings = await generate_routes(
            intent,
            src_name=origin.name if origin else None,
            dst_name=destination.name if destination else None,
            night_mode=state.get("night_mode"),
        )
        warnings = list(state.get("warnings", [])) + plan_warnings

        if not routes:
            return {
                **state,
                "routes": [],
                "warnings": warnings,
                "error": "No route found within your budget and preferences",
                "retry_count": state.get("retry_count", 0) + 1,
            }

        return {**state, "routes": routes, "warnings": warnings, "error": None}
    except Exception as exc:  # noqa: BLE001
        log.exception("Route planning failed")
        return {
            **state,
            "error": f"Route planning failed: {exc}",
            "retry_count": state.get("retry_count", 0) + 1,
        }


# ============================================================
# Edges
# ============================================================
def after_parse(state: PlanningState) -> str:
    return END if state.get("error") else "resolve_places"


def after_resolve(state: PlanningState) -> str:
    return END if state.get("error") else "plan_route"


def after_plan(state: PlanningState) -> str:
    """Retry generation on failure, up to MAX_RETRIES.

    Loops back to plan_route, not parse_intent: the parse already succeeded,
    and re-running the LLM would add seconds of latency for no benefit.
    """
    if state.get("routes"):
        return END
    if state.get("error") and state.get("retry_count", 0) < MAX_RETRIES:
        log.info("Retrying route planning (attempt %d)", state.get("retry_count", 0) + 1)
        return "plan_route"
    return END


# ============================================================
# Graph
# ============================================================
def _build_graph():
    workflow = StateGraph(PlanningState)
    workflow.add_node("parse_intent", node_parse_intent)
    workflow.add_node("resolve_places", node_resolve_places)
    workflow.add_node("plan_route", node_plan_route)

    workflow.set_entry_point("parse_intent")
    workflow.add_conditional_edges("parse_intent", after_parse,
                                   {"resolve_places": "resolve_places", END: END})
    workflow.add_conditional_edges("resolve_places", after_resolve,
                                   {"plan_route": "plan_route", END: END})
    workflow.add_conditional_edges("plan_route", after_plan,
                                   {"plan_route": "plan_route", END: END})
    return workflow.compile()


planning_graph = _build_graph()


async def run_planning(
    user_input: str,
    device_lat: Optional[float] = None,
    device_lon: Optional[float] = None,
    city: Optional[str] = None,
    night_mode: Optional[bool] = None,
) -> dict[str, Any]:
    """Run the planning workflow end to end."""
    started = time.perf_counter()

    initial: PlanningState = {
        "user_input": user_input,
        "device_lat": device_lat,
        "device_lon": device_lon,
        "city_hint": city,
        "night_mode": night_mode,
        "parsed_intent": None,
        "origin": None,
        "destination": None,
        "routes": None,
        "error": None,
        "warnings": [],
        "retry_count": 0,
        "used_fallback_parser": False,
    }

    result = await planning_graph.ainvoke(initial)
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)

    log.info(
        "Planning finished in %dms: %d routes, error=%s",
        result["duration_ms"], len(result.get("routes") or []), result.get("error"),
    )
    return result

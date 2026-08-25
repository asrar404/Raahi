"""RAAHI AI Engine entrypoint.

Stateless planning service. Owns no database: safety scores come from the
gateway's PostGIS functions, and trip state lives in the gateway.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.graphs.planning_graph import run_planning
from app.graphs.reroute_graph import run_reroute
from app.schemas.replan import RerouteRequest, RerouteResponse
from app.schemas.route import PlanRequest, PlanResponse
from app.tools import safety_scorer
from app.tools.geocode import gazetteer_size, geocode

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s v%s", settings.SERVICE_NAME, __version__)
    log.info("Gazetteer loaded with %d places", gazetteer_size())

    if not settings.llm_configured:
        log.warning(
            "GROQ_API_KEY is not set. Intent parsing will use the deterministic "
            "fallback, which handles common phrasings only."
        )
    else:
        log.info("LLM intent parsing enabled (model=%s)", settings.GROQ_MODEL)

    if abs(settings.weight_sum - 1.0) > 0.01:
        log.warning(
            "Utility weights sum to %.2f rather than 1.0; scores will be normalised",
            settings.weight_sum,
        )

    yield
    log.info("Shutting down %s", settings.SERVICE_NAME)


app = FastAPI(
    title="RAAHI AI Engine",
    description=(
        "Converts natural-language travel requests into ranked, budget-capped, "
        "safety-scored multi-modal routes."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error", "detail": str(exc)},
    )


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest) -> PlanResponse:
    """Plan a journey from a plain-language request.

    Always returns 200 with an `error` field rather than an HTTP error status:
    the client shows the message inline on the input screen, and a 4xx/5xx
    would be indistinguishable from a network failure.
    """
    result = await run_planning(
        user_input=req.user_input,
        device_lat=req.origin_lat,
        device_lon=req.origin_lon,
        city=req.city,
        night_mode=req.night_mode,
    )

    return PlanResponse(
        routes=result.get("routes") or [],
        intent=result.get("parsed_intent"),
        origin=result.get("origin"),
        destination=result.get("destination"),
        error=result.get("error"),
        warnings=result.get("warnings") or [],
        used_fallback_parser=result.get("used_fallback_parser", False),
        duration_ms=result.get("duration_ms"),
    )


@app.post("/reroute", response_model=RerouteResponse)
async def reroute(req: RerouteRequest) -> RerouteResponse:
    """Replan from the traveller's current position."""
    result = await run_reroute(
        trip_id=req.trip_id,
        intent=req.intent,
        lat=req.current_lat,
        lon=req.current_lon,
        elapsed=req.elapsed_mins,
        spent=req.spent_budget,
        trigger=req.trigger,
        night_mode=req.night_mode,
    )

    return RerouteResponse(
        new_routes=result.get("new_routes") or [],
        trip_id=req.trip_id,
        trigger=req.trigger.value,
        remaining_budget=result.get("remaining_budget"),
        error=result.get("error"),
        warnings=result.get("warnings") or [],
        duration_ms=result.get("duration_ms"),
    )


@app.get("/geocode")
async def geocode_endpoint(
    q: str,
    city: str | None = None,
) -> dict[str, Any]:
    """Resolve a place name. Exposed for autocomplete and debugging."""
    if not q or not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Query 'q' is required"
        )
    place = await geocode(q, city)
    if place is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Could not resolve {q!r}"
        )
    return place.model_dump()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": __version__,
        "llm_configured": settings.llm_configured,
        "model": settings.GROQ_MODEL if settings.llm_configured else None,
        "gazetteer_places": gazetteer_size(),
        "utility_weights": {
            "cost": settings.W_COST,
            "time": settings.W_TIME,
            "safety": settings.W_SAFETY,
        },
    }


@app.get("/health/deps")
async def health_deps() -> dict[str, Any]:
    """Check the gateway dependency. Slower — do not poll this frequently."""
    return {"safety_backend": await safety_scorer.health()}


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "RAAHI AI Engine",
        "version": __version__,
        "endpoints": ["/plan", "/reroute", "/geocode", "/health"],
        "docs": "/docs",
    }

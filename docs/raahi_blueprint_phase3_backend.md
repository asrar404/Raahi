# RAAHI — Phase 3: Backend & AI Engine Microservices

## File: `services/backend/app/main.py`

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, trips, safety, budget, websocket
from app.services.db import init_db
from app.middleware.logging import RequestLoggingMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="RAAHI API Gateway", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RequestLoggingMiddleware)

app.include_router(auth.router,     prefix="/api/v1/auth",     tags=["Auth"])
app.include_router(trips.router,    prefix="/api/v1/trips",    tags=["Trips"])
app.include_router(safety.router,   prefix="/api/v1/safety",   tags=["Safety"])
app.include_router(budget.router,   prefix="/api/v1/budget",   tags=["Budget"])
app.include_router(websocket.router, prefix="/ws",             tags=["WebSocket"])

@app.get("/health")
async def health(): return {"status": "ok", "service": "raahi-gateway"}
```

---

## File: `services/backend/app/services/ws_manager.py`

```python
from typing import Dict, Set
from fastapi import WebSocket
import json, asyncio

class ConnectionManager:
    def __init__(self):
        # trip_id -> set of websocket connections
        self.active: Dict[str, Set[WebSocket]] = {}

    async def connect(self, trip_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(trip_id, set()).add(ws)

    def disconnect(self, trip_id: str, ws: WebSocket):
        if trip_id in self.active:
            self.active[trip_id].discard(ws)

    async def broadcast(self, trip_id: str, event_type: str, payload: dict):
        msg = json.dumps({"event": event_type, "data": payload})
        dead = set()
        for ws in self.active.get(trip_id, set()):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(trip_id, ws)

    async def send_sos_alert(self, trip_id: str, location: dict, risk_info: dict):
        await self.broadcast(trip_id, "SOS_ALERT", {
            "location": location, "risk": risk_info,
            "message": "HIGH RISK ZONE DETECTED. SOS triggered."
        })

    async def send_reroute(self, trip_id: str, new_route: dict):
        await self.broadcast(trip_id, "REROUTE", {"new_route": new_route})

manager = ConnectionManager()
```

---

## File: `services/backend/app/routers/websocket.py`

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.services.ws_manager import manager
from app.services.postgis import check_risk_and_deviation
from app.services.db import get_db
import json, asyncio

router = APIRouter()

@router.websocket("/trip/{trip_id}")
async def trip_ws(trip_id: str, ws: WebSocket, db=Depends(get_db)):
    await manager.connect(trip_id, ws)
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") == "TELEMETRY":
                lat = data["lat"]; lon = data["lon"]
                # Persist telemetry
                await db.execute(
                    "INSERT INTO live_gps_telemetry "
                    "(trip_id, user_id, location, accuracy_m, speed_kmh) "
                    "VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3,$4),4326), $5, $6)",
                    trip_id, data["user_id"], lon, lat,
                    data.get("accuracy"), data.get("speed")
                )
                # Safety check
                risk = await check_risk_and_deviation(db, trip_id, lat, lon)
                if risk["in_high_risk"]:
                    await manager.send_sos_alert(trip_id, {"lat": lat, "lon": lon}, risk)
    except WebSocketDisconnect:
        manager.disconnect(trip_id, ws)
```

---

## File: `services/backend/app/services/postgis.py`

```python
from asyncpg import Connection
from typing import Optional
import logging

log = logging.getLogger(__name__)

async def check_risk_and_deviation(
    db, trip_id: str, lat: float, lon: float,
    night_mode: bool = False
) -> dict:
    # Check if in high-risk zone
    risk_rows = await db.fetch(
        "SELECT * FROM fn_get_risk_zone($1, $2, $3, $4)",
        lat, lon, 3, night_mode
    )
    in_high_risk = len(risk_rows) > 0
    risk_info = [dict(r) for r in risk_rows]

    # Check route deviation
    off_route = await db.fetchval(
        "SELECT fn_is_off_route($1, $2, $3)", trip_id, lat, lon
    )

    # Nearby alerts
    alerts = await db.fetch(
        "SELECT * FROM fn_nearby_alerts($1, $2, 300)", lat, lon
    )

    # Safe refuges if at risk
    refuges = []
    if in_high_risk:
        refuges = await db.fetch(
            "SELECT * FROM fn_find_safe_refuges($1, $2, 600, 2)", lat, lon
        )

    return {
        "in_high_risk": in_high_risk,
        "risk_zones": risk_info,
        "off_route": bool(off_route),
        "nearby_alerts": [dict(a) for a in alerts],
        "safe_refuges": [dict(r) for r in refuges]
    }
```

---

## File: `services/backend/app/services/twilio_notifier.py`

```python
from twilio.rest import Client
from app.config import settings
import logging

log = logging.getLogger(__name__)
client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

async def send_sos_sms(contacts: list[dict], user_name: str, lat: float, lon: float):
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    body = (
        f"🚨 RAAHI SOS ALERT\n"
        f"{user_name} has entered a HIGH-RISK area.\n"
        f"Last known location: {maps_link}\n"
        f"Please check on them immediately."
    )
    for contact in contacts:
        try:
            client.messages.create(
                body=body, from_=settings.TWILIO_FROM_NUMBER, to=contact["phone"]
            )
            log.info(f"SOS SMS sent to {contact['phone']}")
        except Exception as e:
            log.error(f"SMS failed for {contact['phone']}: {e}")

async def make_sos_voice_call(contacts: list[dict], user_name: str, lat: float, lon: float):
    twiml = (
        f'<Response><Say voice="alice" language="en-IN">'
        f'Emergency alert from RAAHI. {user_name} has triggered an SOS. '
        f'They may be in danger. Please contact them immediately.'
        f'</Say></Response>'
    )
    for contact in contacts[:2]:  # voice call top 2 contacts
        try:
            client.calls.create(
                twiml=twiml, from_=settings.TWILIO_FROM_NUMBER, to=contact["phone"]
            )
        except Exception as e:
            log.error(f"Voice call failed: {e}")
```

---

## File: `services/ai_engine/app/schemas/intent.py`

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

class TransitMode(str, Enum):
    WALK   = "walk"
    METRO  = "metro"
    BUS    = "bus"
    TRAIN  = "train"
    AUTO   = "auto"
    CAB    = "cab"
    RAPIDO = "rapido"

class ParsedIntent(BaseModel):
    source_raw:       str
    source_lat:       Optional[float]
    source_lon:       Optional[float]
    destination_raw:  str
    dest_lat:         Optional[float]
    dest_lon:         Optional[float]
    budget_ceiling:   float = Field(..., description="Max total spend in INR")
    time_deadline:    Optional[str] = Field(None, description="ISO8601 deadline")
    preferred_modes:  List[TransitMode] = [TransitMode.METRO, TransitMode.BUS]
    safety_priority:  bool = True
    confidence:       float = Field(default=1.0, ge=0, le=1)

class RouteLeg(BaseModel):
    leg_order:      int
    mode:           TransitMode
    from_name:      str
    from_lat:       float
    from_lon:       float
    to_name:        str
    to_lat:         float
    to_lon:         float
    distance_km:    float
    planned_cost:   float
    duration_mins:  int
    provider:       Optional[str]
    safety_score:   float = Field(ge=0, le=5)

class PlannedRoute(BaseModel):
    route_id:       str
    legs:           List[RouteLeg]
    total_cost:     float
    total_duration: int
    utility_score:  float
    safety_rating:  float
    summary:        str
```

---

## File: `services/ai_engine/app/agents/intent_parser.py`

```python
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from app.schemas.intent import ParsedIntent
from app.config import settings
import json

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=settings.GROQ_API_KEY)
parser = JsonOutputParser(pydantic_object=ParsedIntent)

SYSTEM_PROMPT = """You are RAAHI's intent parser for Indian urban travel.
Extract structured travel intent from user's plain-text query.

Return ONLY valid JSON matching this schema:
{schema}

Rules:
- Budget must be numeric INR (e.g. "500 rupees" → 500.0)
- If no budget mentioned, default to 300.0
- preferred_modes only from: walk, metro, bus, train, auto, cab, rapido
- If user is female or mentions safety, set safety_priority=true
- time_deadline in ISO8601 or null
- confidence: your confidence 0-1 in the parse"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT.format(schema=ParsedIntent.model_json_schema())),
    ("human", "{user_input}")
])

chain = prompt | llm | parser

async def parse_intent(user_input: str) -> ParsedIntent:
    result = await chain.ainvoke({"user_input": user_input})
    return ParsedIntent(**result)
```

---

## File: `services/ai_engine/app/agents/planner_agent.py`

```python
from app.schemas.intent import ParsedIntent, PlannedRoute, RouteLeg, TransitMode
from app.tools.safety_scorer import score_route_safety
from app.tools.transit_api import fetch_transit_options
from app.tools.budget_filter import hard_filter_budget
from typing import List
import uuid, asyncio

# Utility weights
W_COST   = 0.35
W_TIME   = 0.30
W_SAFETY = 0.35

def compute_utility(cost: float, duration: int, safety: float,
                    budget: float, max_duration: int = 120) -> float:
    norm_cost   = 1.0 - min(cost / budget, 1.0)          # higher is better
    norm_time   = 1.0 - min(duration / max_duration, 1.0) # lower duration = higher score
    norm_safety = safety / 5.0
    return round(W_COST * norm_cost + W_TIME * norm_time + W_SAFETY * norm_safety, 4)

async def generate_routes(intent: ParsedIntent) -> List[PlannedRoute]:
    # 1. Fetch raw transit options from APIs / GTFS cache
    raw_options = await fetch_transit_options(
        src_lat=intent.source_lat, src_lon=intent.source_lon,
        dst_lat=intent.dest_lat,   dst_lon=intent.dest_lon,
        modes=intent.preferred_modes
    )

    # 2. Hard filter: remove routes exceeding budget
    filtered = hard_filter_budget(raw_options, intent.budget_ceiling)

    # 3. Score safety for each route
    scored = []
    for option in filtered:
        safety = await score_route_safety(option["legs"])
        total_cost = sum(l["planned_cost"] for l in option["legs"])
        total_dur  = sum(l["duration_mins"] for l in option["legs"])
        utility    = compute_utility(total_cost, total_dur, safety, intent.budget_ceiling)
        legs = [RouteLeg(**l, safety_score=safety) for l in option["legs"]]
        scored.append(PlannedRoute(
            route_id=str(uuid.uuid4()),
            legs=legs,
            total_cost=total_cost,
            total_duration=total_dur,
            utility_score=utility,
            safety_rating=safety,
            summary=option.get("summary", "")
        ))

    # 4. Sort by utility (descending), return top 3
    scored.sort(key=lambda r: r.utility_score, reverse=True)
    return scored[:3]
```

---

## File: `services/ai_engine/app/graphs/planning_graph.py`

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, List
from app.agents.intent_parser import parse_intent
from app.agents.planner_agent import generate_routes
from app.schemas.intent import ParsedIntent, PlannedRoute

class PlanningState(TypedDict):
    user_input:    str
    parsed_intent: Optional[ParsedIntent]
    routes:        Optional[List[PlannedRoute]]
    error:         Optional[str]
    retry_count:   int

async def node_parse_intent(state: PlanningState) -> PlanningState:
    try:
        intent = await parse_intent(state["user_input"])
        return {**state, "parsed_intent": intent, "error": None}
    except Exception as e:
        return {**state, "error": str(e)}

async def node_plan_route(state: PlanningState) -> PlanningState:
    try:
        routes = await generate_routes(state["parsed_intent"])
        return {**state, "routes": routes}
    except Exception as e:
        return {**state, "error": str(e), "retry_count": state["retry_count"] + 1}

def should_retry(state: PlanningState) -> str:
    if state.get("error") and state["retry_count"] < 2:
        return "parse_intent"
    if state.get("routes"):
        return END
    return END

workflow = StateGraph(PlanningState)
workflow.add_node("parse_intent", node_parse_intent)
workflow.add_node("plan_route",   node_plan_route)
workflow.set_entry_point("parse_intent")
workflow.add_edge("parse_intent", "plan_route")
workflow.add_conditional_edges("plan_route", should_retry)
planning_graph = workflow.compile()

async def run_planning(user_input: str) -> dict:
    result = await planning_graph.ainvoke({
        "user_input": user_input,
        "parsed_intent": None,
        "routes": None,
        "error": None,
        "retry_count": 0
    })
    return result
```

---

## File: `services/ai_engine/app/graphs/reroute_graph.py`

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, List
from app.agents.planner_agent import generate_routes
from app.schemas.intent import ParsedIntent, PlannedRoute
from datetime import datetime

class RerouteState(TypedDict):
    trip_id:       str
    intent:        ParsedIntent
    current_lat:   float
    current_lon:   float
    elapsed_mins:  int
    spent_budget:  float
    trigger:       str   # 'off_route' | 'delay' | 'risk_zone'
    new_routes:    Optional[List[PlannedRoute]]
    error:         Optional[str]

async def node_adjust_intent(state: RerouteState) -> RerouteState:
    """Update intent with current position and remaining budget."""
    updated = state["intent"].model_copy(update={
        "source_lat":     state["current_lat"],
        "source_lon":     state["current_lon"],
        "budget_ceiling": state["intent"].budget_ceiling - state["spent_budget"],
    })
    return {**state, "intent": updated}

async def node_replan(state: RerouteState) -> RerouteState:
    try:
        routes = await generate_routes(state["intent"])
        return {**state, "new_routes": routes}
    except Exception as e:
        return {**state, "error": str(e)}

workflow = StateGraph(RerouteState)
workflow.add_node("adjust_intent", node_adjust_intent)
workflow.add_node("replan",        node_replan)
workflow.set_entry_point("adjust_intent")
workflow.add_edge("adjust_intent", "replan")
workflow.add_edge("replan", END)
reroute_graph = workflow.compile()

async def run_reroute(trip_id: str, intent: ParsedIntent,
                      lat: float, lon: float,
                      elapsed: int, spent: float, trigger: str) -> dict:
    return await reroute_graph.ainvoke({
        "trip_id": trip_id, "intent": intent,
        "current_lat": lat, "current_lon": lon,
        "elapsed_mins": elapsed, "spent_budget": spent,
        "trigger": trigger, "new_routes": None, "error": None
    })
```

---

## File: `services/ai_engine/app/main.py`

```python
from fastapi import FastAPI
from pydantic import BaseModel
from app.graphs.planning_graph import run_planning
from app.graphs.reroute_graph import run_reroute
from app.schemas.intent import ParsedIntent

app = FastAPI(title="RAAHI AI Engine", version="1.0.0")

class PlanRequest(BaseModel):
    user_input: str

class RerouteRequest(BaseModel):
    trip_id:     str
    intent:      ParsedIntent
    current_lat: float
    current_lon: float
    elapsed_mins: int
    spent_budget: float
    trigger:     str

@app.post("/plan")
async def plan(req: PlanRequest):
    result = await run_planning(req.user_input)
    return {"routes": [r.model_dump() for r in (result["routes"] or [])],
            "intent": result["parsed_intent"].model_dump() if result["parsed_intent"] else None,
            "error":  result.get("error")}

@app.post("/reroute")
async def reroute(req: RerouteRequest):
    result = await run_reroute(
        req.trip_id, req.intent, req.current_lat, req.current_lon,
        req.elapsed_mins, req.spent_budget, req.trigger
    )
    return {"new_routes": [r.model_dump() for r in (result["new_routes"] or [])],
            "error": result.get("error")}

@app.get("/health")
async def health(): return {"status": "ok", "service": "raahi-ai-engine"}
```

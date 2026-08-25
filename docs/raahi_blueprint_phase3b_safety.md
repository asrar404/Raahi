# RAAHI — Phase 3B: Safety Watcher Microservice

## File: `services/safety_watcher/app/state_machine.py`

```python
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

class TripState(Enum):
    IDLE            = auto()
    NAVIGATING      = auto()
    STATIONARY      = auto()   # not moving >5 mins
    OFF_ROUTE       = auto()
    HIGH_RISK_ZONE  = auto()
    SOS_TRIGGERED   = auto()
    COMPLETED       = auto()

@dataclass
class TripContext:
    trip_id:          str
    user_id:          str
    state:            TripState = TripState.IDLE
    last_lat:         Optional[float] = None
    last_lon:         Optional[float] = None
    last_moved_at:    datetime = field(default_factory=datetime.utcnow)
    sos_triggered:    bool = False
    off_route_count:  int = 0
    stationary_secs:  int = 0
    contacts:         list = field(default_factory=list)

    def transition(self, new_state: TripState):
        print(f"[{self.trip_id}] {self.state.name} → {new_state.name}")
        self.state = new_state
```

---

## File: `services/safety_watcher/app/geofence_evaluator.py`

```python
import asyncpg, os
from app.state_machine import TripContext, TripState
from datetime import datetime, timezone

DB_URL = os.getenv("DATABASE_URL")

async def evaluate(ctx: TripContext, lat: float, lon: float) -> dict:
    conn = await asyncpg.connect(DB_URL)
    try:
        # 1. Risk zone check
        risk_rows = await conn.fetch(
            "SELECT * FROM fn_get_risk_zone($1,$2,$3,$4)",
            lat, lon, 3, _is_night()
        )
        in_high_risk = len(risk_rows) > 0

        # 2. Route deviation
        off_route = await conn.fetchval(
            "SELECT fn_is_off_route($1,$2,$3)", ctx.trip_id, lat, lon
        )

        # 3. Stationary detection
        now = datetime.now(timezone.utc)
        moved = _has_moved(ctx.last_lat, ctx.last_lon, lat, lon, threshold_m=20)
        if moved:
            ctx.last_moved_at = now
            ctx.stationary_secs = 0
        else:
            ctx.stationary_secs = int((now - ctx.last_moved_at).total_seconds())

        ctx.last_lat = lat; ctx.last_lon = lon

        # 4. State transitions
        if in_high_risk and not ctx.sos_triggered:
            ctx.transition(TripState.HIGH_RISK_ZONE)
        elif off_route:
            ctx.off_route_count += 1
            if ctx.off_route_count >= 3:
                ctx.transition(TripState.OFF_ROUTE)
        elif ctx.stationary_secs > 300:  # 5 min stationary
            ctx.transition(TripState.STATIONARY)
        else:
            ctx.off_route_count = 0
            ctx.transition(TripState.NAVIGATING)

        return {
            "state": ctx.state.name,
            "in_high_risk": in_high_risk,
            "risk_zones": [dict(r) for r in risk_rows],
            "off_route": bool(off_route),
            "stationary_secs": ctx.stationary_secs,
        }
    finally:
        await conn.close()

def _is_night() -> bool:
    hour = datetime.now(timezone.utc).hour + 5  # IST offset approx
    return hour < 6 or hour >= 22

def _has_moved(lat1, lon1, lat2, lon2, threshold_m=20) -> bool:
    if lat1 is None or lon1 is None:
        return True
    from math import radians, sin, cos, sqrt, atan2
    R = 6371000
    φ1, φ2 = radians(lat1), radians(lat2)
    dφ = radians(lat2-lat1); dλ = radians(lon2-lon1)
    a = sin(dφ/2)**2 + cos(φ1)*cos(φ2)*sin(dλ/2)**2
    dist = R * 2 * atan2(sqrt(a), sqrt(1-a))
    return dist > threshold_m
```

---

## File: `services/safety_watcher/app/sos_pipeline.py`

```python
import httpx, os, asyncio
from app.state_machine import TripContext, TripState

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

async def trigger_sos(ctx: TripContext, lat: float, lon: float, risk_info: dict):
    if ctx.sos_triggered:
        return  # idempotent
    ctx.sos_triggered = True
    ctx.transition(TripState.SOS_TRIGGERED)

    async with httpx.AsyncClient() as client:
        # 1. Notify backend WebSocket clients
        await client.post(f"{BACKEND_URL}/api/v1/safety/sos", json={
            "trip_id": ctx.trip_id,
            "user_id": ctx.user_id,
            "lat": lat, "lon": lon,
            "risk_info": risk_info
        })

        # 2. Send Twilio SMS + Voice via backend
        await client.post(f"{BACKEND_URL}/api/v1/safety/notify-contacts", json={
            "trip_id": ctx.trip_id,
            "user_id": ctx.user_id,
            "lat": lat, "lon": lon,
            "contacts": ctx.contacts
        })

async def trigger_reroute(ctx: TripContext, lat: float, lon: float, spent: float, trigger: str):
    async with httpx.AsyncClient() as client:
        # Fetch current intent from backend
        resp = await client.get(f"{BACKEND_URL}/api/v1/trips/{ctx.trip_id}/intent")
        intent = resp.json()
        # Call AI engine reroute
        ai_url = os.getenv("AI_ENGINE_URL", "http://ai_engine:8001")
        reroute_resp = await client.post(f"{ai_url}/reroute", json={
            "trip_id": ctx.trip_id,
            "intent": intent,
            "current_lat": lat,
            "current_lon": lon,
            "elapsed_mins": 0,
            "spent_budget": spent,
            "trigger": trigger
        })
        new_routes = reroute_resp.json()
        # Push reroute to WS clients
        await client.post(f"{BACKEND_URL}/api/v1/safety/reroute", json={
            "trip_id": ctx.trip_id,
            "new_routes": new_routes.get("new_routes", [])
        })
```

---

## File: `services/safety_watcher/app/watcher.py`

```python
import asyncio, os, json
from app.state_machine import TripContext, TripState
from app.geofence_evaluator import evaluate
from app.sos_pipeline import trigger_sos, trigger_reroute
import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECS", "15"))

class SafetyWatcher:
    def __init__(self):
        self.active_trips: dict[str, TripContext] = {}

    async def start(self):
        r = aioredis.from_url(REDIS_URL)
        # Listen to Redis Stream: raahi:telemetry
        print("[SafetyWatcher] Listening on stream raahi:telemetry")
        last_id = "$"
        while True:
            msgs = await r.xread({"raahi:telemetry": last_id}, block=POLL_INTERVAL * 1000, count=50)
            for _, records in (msgs or []):
                for msg_id, fields in records:
                    last_id = msg_id
                    await self._process(fields)

    async def _process(self, fields: dict):
        trip_id = fields.get(b"trip_id", b"").decode()
        user_id = fields.get(b"user_id", b"").decode()
        lat     = float(fields.get(b"lat", 0))
        lon     = float(fields.get(b"lon", 0))
        spent   = float(fields.get(b"spent", 0))

        if trip_id not in self.active_trips:
            contacts_raw = fields.get(b"contacts", b"[]").decode()
            self.active_trips[trip_id] = TripContext(
                trip_id=trip_id, user_id=user_id,
                contacts=json.loads(contacts_raw)
            )

        ctx = self.active_trips[trip_id]
        result = await evaluate(ctx, lat, lon)

        # SOS trigger conditions
        if result["in_high_risk"]:
            await trigger_sos(ctx, lat, lon, result)

        # Reroute trigger conditions
        if result["off_route"] and not ctx.sos_triggered:
            await trigger_reroute(ctx, lat, lon, spent, "off_route")
        elif result["stationary_secs"] > 600 and not ctx.sos_triggered:
            await trigger_reroute(ctx, lat, lon, spent, "delay")

        if ctx.state == TripState.COMPLETED:
            del self.active_trips[trip_id]
```

---

## File: `services/safety_watcher/app/main.py`

```python
import asyncio
from app.watcher import SafetyWatcher
from fastapi import FastAPI
import threading

app = FastAPI(title="RAAHI Safety Watcher")

@app.get("/health")
async def health(): return {"status": "ok", "service": "raahi-safety-watcher"}

@app.on_event("startup")
async def startup():
    watcher = SafetyWatcher()
    asyncio.create_task(watcher.start())
```

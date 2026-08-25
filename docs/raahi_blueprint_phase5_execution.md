# RAAHI — Phase 5: Docker Compose & OpenCode Execution Sequence

## File: `docker-compose.yml`

```yaml
version: '3.9'

services:
  postgres:
    image: postgis/postgis:16-3.4
    environment:
      POSTGRES_USER:     ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB:       ${POSTGRES_DB}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infrastructure/docker/postgres/init:/docker-entrypoint-initdb.d
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s; timeout: 5s; retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s; timeout: 3s; retries: 3

  backend:
    build:
      context: ./services/backend
      dockerfile: Dockerfile
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres/${POSTGRES_DB}
      REDIS_URL:    redis://redis:6379
    ports:
      - "8000:8000"
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    volumes:
      - ./services/backend:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  ai_engine:
    build:
      context: ./services/ai_engine
      dockerfile: Dockerfile
    env_file: .env
    ports:
      - "8001:8001"
    depends_on:
      - backend
    volumes:
      - ./services/ai_engine:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

  safety_watcher:
    build:
      context: ./services/safety_watcher
      dockerfile: Dockerfile
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres/${POSTGRES_DB}
      REDIS_URL:    redis://redis:6379
      BACKEND_URL:  http://backend:8000
      AI_ENGINE_URL: http://ai_engine:8001
    depends_on:
      - backend
      - redis
    volumes:
      - ./services/safety_watcher:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload

volumes:
  pgdata:
```

---

## File: `.env.example`

```env
# PostgreSQL
POSTGRES_USER=raahi
POSTGRES_PASSWORD=raahi_secure_pass
POSTGRES_DB=raahi_db

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-role-key

# Groq (LLM)
GROQ_API_KEY=gsk_...

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_FROM_NUMBER=+1xxxxxxxxxx

# Redis
REDIS_URL=redis://redis:6379

# Expo Mobile
EXPO_PUBLIC_API_URL=http://localhost:8000
EXPO_PUBLIC_WS_URL=ws://localhost:8000
EXPO_PUBLIC_AI_URL=http://localhost:8001
EXPO_PUBLIC_GOOGLE_MAPS_KEY=your-google-maps-key
```

---

## File: `services/backend/requirements.txt`

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
asyncpg==0.29.0
sqlalchemy[asyncio]==2.0.31
pydantic==2.8.2
pydantic-settings==2.3.4
python-jose[cryptography]==3.3.0
twilio==9.2.3
redis==5.0.7
httpx==0.27.0
python-dotenv==1.0.1
```

---

## File: `services/ai_engine/requirements.txt`

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
langgraph==0.2.14
langchain==0.2.11
langchain-groq==0.1.9
langchain-core==0.2.23
pydantic==2.8.2
httpx==0.27.0
python-dotenv==1.0.1
```

---

## File: `services/safety_watcher/requirements.txt`

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
asyncpg==0.29.0
redis==5.0.7
httpx==0.27.0
python-dotenv==1.0.1
```

---

## File: `apps/mobile/package.json`

```json
{
  "name": "raahi-mobile",
  "version": "1.0.0",
  "main": "expo-router/entry",
  "scripts": {
    "start": "expo start",
    "android": "expo start --android",
    "ios": "expo start --ios"
  },
  "dependencies": {
    "expo": "~51.0.0",
    "expo-location": "~17.0.1",
    "expo-router": "~3.5.0",
    "react": "18.2.0",
    "react-native": "0.74.3",
    "react-native-maps": "1.14.0",
    "zustand": "^4.5.4",
    "@react-native-async-storage/async-storage": "1.23.1",
    "@react-navigation/native": "^6.1.17",
    "@react-navigation/bottom-tabs": "^6.5.20",
    "@react-navigation/native-stack": "^6.9.26",
    "axios": "^1.7.2",
    "@supabase/supabase-js": "^2.44.2",
    "react-native-safe-area-context": "4.10.5",
    "react-native-screens": "3.31.1",
    "react-native-gesture-handler": "~2.16.1"
  },
  "devDependencies": {
    "@babel/core": "^7.24.0",
    "typescript": "~5.3.3"
  }
}
```

---

# OpenCode CLI Execution Sequence

> Run these in order in your terminal with `opencode` powered by Claude Opus.
> Each prompt generates exactly the specified file(s) with full, production-ready code.

---

## STEP 0 — Bootstrap monorepo

```bash
mkdir -p raahi/{apps/mobile/src/{store,hooks,services,screens,components,navigation,constants},\
services/{backend/app/{models,routers,services,middleware},\
ai_engine/app/{schemas,agents,graphs,tools,prompts},\
safety_watcher/app},\
infrastructure/docker/postgres/init}
cd raahi
git init
cp .env.example .env
```

---

## STEP 1 — Database layer

```bash
opencode "Generate the file infrastructure/docker/postgres/init/01_extensions.sql
Enable: postgis, postgis_topology, uuid-ossp, pg_trgm extensions."

opencode "Generate the file infrastructure/docker/postgres/init/02_schema.sql
Tables: users, trips, trip_legs, stay_and_food_recommendations,
safety_zones (Polygon geometry), crowdsourced_reports (Point geometry),
live_gps_telemetry (partitioned by month), expense_logs.
Use PostGIS geometry columns with SRID 4326. Include CHECK constraints and
JSONB for emergency_contacts. Partition live_gps_telemetry by RANGE on recorded_at."

opencode "Generate infrastructure/docker/postgres/init/03_indexes.sql
Create GiST spatial indexes on all geometry columns.
Create B-tree indexes on FK columns, status fields, and time columns."

opencode "Generate infrastructure/docker/postgres/init/04_functions.sql
Write four PostgreSQL PL/pgSQL functions:
1. fn_get_risk_zone(lat, lon, min_risk, night_mode) using ST_Contains
2. fn_find_safe_refuges(lat, lon, radius_m, max_risk) using ST_DWithin
3. fn_is_off_route(trip_id, lat, lon, threshold_m) using ST_Distance on route LineString
4. fn_nearby_alerts(lat, lon, radius_m) for crowdsourced_reports."

opencode "Generate infrastructure/docker/postgres/init/05_seed.sql
Seed safety_zones for Delhi NCR (Connaught Place risk=1, Paharganj risk=3,
Nizamuddin risk=2 day/4 night, Saket risk=1) and Mumbai (Dharavi risk=4,
Bandra risk=1, Kurla risk=3). Seed stay_and_food for Zostel Delhi, Moustache
Hostel Jaipur, Sagar Ratna CP, Indian Coffee House CP.
Use real approximate lat/lon coordinates."
```

---

## STEP 2 — Backend Gateway

```bash
opencode "Generate services/backend/app/config.py
Pydantic BaseSettings class loading: DATABASE_URL, REDIS_URL, SUPABASE_URL,
SUPABASE_SERVICE_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER.
Include a cached settings() function."

opencode "Generate services/backend/app/services/db.py
Async SQLAlchemy engine using asyncpg dialect, sessionmaker, and init_db()
that runs a test query. Expose get_db dependency."

opencode "Generate services/backend/app/services/ws_manager.py
ConnectionManager class:
- active dict: trip_id → Set[WebSocket]
- connect(), disconnect(), broadcast(), send_sos_alert(), send_reroute() methods
- All async, handle dead connections gracefully."

opencode "Generate services/backend/app/routers/websocket.py
WebSocket endpoint /ws/trip/{trip_id}:
- Accept connection via manager
- Receive TELEMETRY messages, persist to live_gps_telemetry via asyncpg
- Call check_risk_and_deviation() from postgis service
- Broadcast SOS_ALERT if in_high_risk=True
- Handle WebSocketDisconnect gracefully."

opencode "Generate services/backend/app/services/postgis.py
Async function check_risk_and_deviation(db, trip_id, lat, lon, night_mode):
- Calls fn_get_risk_zone, fn_is_off_route, fn_nearby_alerts, fn_find_safe_refuges
- Returns dict with in_high_risk, risk_zones, off_route, nearby_alerts, safe_refuges."

opencode "Generate services/backend/app/services/twilio_notifier.py
Two async functions:
- send_sos_sms(contacts, user_name, lat, lon): sends Twilio SMS with Google Maps link
- make_sos_voice_call(contacts, user_name, lat, lon): TwiML voice call to top 2 contacts."

opencode "Generate services/backend/app/routers/trips.py
FastAPI router with:
- POST /trips: create trip, store in DB
- GET /trips/{trip_id}: get trip with legs
- PATCH /trips/{trip_id}/status: update status
- GET /trips/{trip_id}/intent: return serialized ParsedIntent from trip metadata
- POST /trips/{trip_id}/start: mark trip active, set started_at"

opencode "Generate services/backend/app/routers/safety.py
FastAPI router with:
- POST /safety/sos: update trip status to 'sos', broadcast via WS, call Twilio
- POST /safety/notify-contacts: call twilio_notifier SMS + voice
- POST /safety/reroute: broadcast REROUTE event to WS clients
- POST /safety/report: insert crowdsourced_report"

opencode "Generate services/backend/app/routers/budget.py
FastAPI router with:
- POST /budget/log: insert expense_log, update trips.total_actual_cost
- GET /budget/{trip_id}: return ceiling, spent, remaining, logs list
- GET /budget/{trip_id}/alert: return {over_budget: bool, percent_used: float}"

opencode "Generate services/backend/app/routers/auth.py
FastAPI router using Supabase JWT verification:
- POST /auth/verify: validate Supabase JWT, create user in DB if first login
- GET /auth/me: return current user profile
- PATCH /auth/emergency-contacts: update user.emergency_contacts JSONB"

opencode "Generate services/backend/app/main.py
FastAPI app with lifespan, CORS middleware, request logging middleware,
include all routers, health endpoint."

opencode "Generate services/backend/Dockerfile
Python 3.12-slim base, install requirements.txt, WORKDIR /app,
COPY app/ app/, CMD uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

---

## STEP 3 — AI Engine

```bash
opencode "Generate services/ai_engine/app/schemas/intent.py
Pydantic models: ParsedIntent (source, dest, budget_ceiling, time_deadline,
preferred_modes, safety_priority, confidence), RouteLeg, PlannedRoute.
TransitMode Enum: walk,metro,bus,train,auto,cab,rapido."

opencode "Generate services/ai_engine/app/agents/intent_parser.py
LangChain chain using ChatGroq llama-3.3-70b-versatile.
System prompt instructs extraction of ParsedIntent from Indian travel queries.
Returns ParsedIntent Pydantic object. Handle rupee amounts, time deadlines, safety keywords."

opencode "Generate services/ai_engine/app/tools/transit_api.py
Async function fetch_transit_options(src_lat, src_lon, dst_lat, dst_lon, modes).
For now implement a realistic mock that:
- Generates 3-4 route options per mode combination
- Uses real Indian transit cost ranges (metro: 10-60 INR/km, bus: 5-15, auto: 15-25/km)
- Adds realistic duration based on haversine distance
- Marks the function for future GTFS/OLA/Rapido API integration."

opencode "Generate services/ai_engine/app/tools/safety_scorer.py
Async function score_route_safety(legs: list[dict]) -> float.
Calls backend PostGIS API to sample risk scores along each leg's midpoint.
Aggregates weighted average. Returns score 0-5 (5=safest)."

opencode "Generate services/ai_engine/app/tools/budget_filter.py
Function hard_filter_budget(routes, ceiling) that removes any route where
sum(leg.planned_cost) > ceiling. Returns filtered list."

opencode "Generate services/ai_engine/app/agents/planner_agent.py
Async generate_routes(intent: ParsedIntent) -> List[PlannedRoute].
1. fetch_transit_options() 2. hard_filter_budget() 3. score_route_safety()
4. compute_utility: U = 0.35*norm_cost + 0.30*norm_time + 0.35*norm_safety
5. Sort by utility, return top 3 PlannedRoute objects."

opencode "Generate services/ai_engine/app/graphs/planning_graph.py
LangGraph StateGraph with nodes: parse_intent, plan_route.
PlanningState TypedDict. Retry logic (max 2) if error occurs.
Compile and export run_planning(user_input) async function."

opencode "Generate services/ai_engine/app/graphs/reroute_graph.py
LangGraph StateGraph with nodes: adjust_intent, replan.
RerouteState includes trip_id, intent, current_lat/lon, spent_budget, trigger.
adjust_intent updates intent source to current position and subtracts spent_budget.
Export run_reroute() async function."

opencode "Generate services/ai_engine/app/main.py
FastAPI app exposing POST /plan and POST /reroute endpoints.
/plan calls run_planning, /reroute calls run_reroute.
Both return routes list and error."

opencode "Generate services/ai_engine/Dockerfile same pattern as backend."
```

---

## STEP 4 — Safety Watcher

```bash
opencode "Generate services/safety_watcher/app/state_machine.py
TripState Enum: IDLE, NAVIGATING, STATIONARY, OFF_ROUTE, HIGH_RISK_ZONE, SOS_TRIGGERED, COMPLETED.
TripContext dataclass with trip_id, user_id, state, last_lat/lon, last_moved_at,
sos_triggered, off_route_count, stationary_secs, contacts.
transition(new_state) method with logging."

opencode "Generate services/safety_watcher/app/geofence_evaluator.py
Async evaluate(ctx, lat, lon) function:
- asyncpg connect to DB
- Call fn_get_risk_zone, fn_is_off_route
- Detect stationary using haversine vs last position
- Execute TripContext state transitions
- Return evaluation dict."

opencode "Generate services/safety_watcher/app/sos_pipeline.py
Async trigger_sos(ctx, lat, lon, risk_info): idempotent, posts to backend /safety/sos and /safety/notify-contacts via httpx.
Async trigger_reroute(ctx, lat, lon, spent, trigger): fetches intent from backend,
calls ai_engine /reroute, posts result to /safety/reroute."

opencode "Generate services/safety_watcher/app/watcher.py
SafetyWatcher class with active_trips dict.
start() listens to Redis Stream 'raahi:telemetry' using xread with block.
_process(fields) deserialises telemetry, gets/creates TripContext,
calls evaluate(), triggers SOS if in_high_risk, triggers reroute if off_route or 10min stationary."

opencode "Generate services/safety_watcher/app/main.py
FastAPI app. On startup, create asyncio task for SafetyWatcher().start().
Health endpoint."

opencode "Generate services/safety_watcher/Dockerfile."
```

---

## STEP 5 — Mobile App

```bash
opencode "Generate apps/mobile/src/constants/colors.ts
Dark theme color tokens: bg=#0D0D1A, surface=#1A1A2E, border=#2A2A3E,
text=#EAEAFF, muted=#6B6B8A, accent=#6C63FF, success, warning, danger."

opencode "Generate apps/mobile/src/store/index.ts
Four Zustand stores:
- useAuthStore (user, token, setUser, logout) with AsyncStorage persistence
- useTripStore (routes, selectedRoute, activeTrip, activeLegIdx)
- useSafetyStore (riskLevel, inRiskZone, offRoute, sosActive, alerts)
- useBudgetStore (ceiling, spent, logs) with persistence."

opencode "Generate apps/mobile/src/services/api.ts
Axios instances for API gateway (port 8000) and AI engine (port 8001).
JWT interceptor from useAuthStore. Exported helper functions: planTrip(), logExpense()."

opencode "Generate apps/mobile/src/hooks/useWebSocket.ts
useTripWebSocket(tripId) hook:
- Opens WebSocket to /ws/trip/{tripId}
- Handles SOS_ALERT, REROUTE, RISK_UPDATE, OFF_ROUTE events
- Updates Zustand stores accordingly
- Exposes sendTelemetry(payload) function."

opencode "Generate apps/mobile/src/hooks/useLocation.ts
useBackgroundLocation(tripId, onLocation) hook.
expo-location watchPositionAsync every 15s or 20m.
Requests foreground + background permissions."

opencode "Generate apps/mobile/src/screens/IntentInputScreen.tsx
Full-screen dark UI. Large multiline TextInput, example chips, Plan button.
Calls aiApi.post('/plan'), stores routes in useTripStore, navigates to RouteSelection."

opencode "Generate apps/mobile/src/screens/RouteSelectionScreen.tsx
Display 3 route cards sorted by utility_score.
Each card shows: mode badges, total cost, duration, safety rating (stars),
utility score, per-leg breakdown in expandable section.
Select button stores selectedRoute, navigates to MapView."

opencode "Generate apps/mobile/src/screens/MapViewScreen.tsx
react-native-maps MapView with dark custom style.
Polyline for selected route (red if in risk zone, purple otherwise).
Heatmap overlay from crowdsourced alerts.
Leg markers. Calls useBackgroundLocation, useTripWebSocket.
Renders SOSButton, ExpenseWidget, RerouteModal."

opencode "Generate apps/mobile/src/components/SOSButton.tsx
Floating bottom-right button. On press: Alert confirmation, calls /api/v1/safety/sos,
triggers triggerSOS() in store. Pulses red when sosActive=true."

opencode "Generate apps/mobile/src/components/RouteCard.tsx
Card component for route display. Shows mode icons, cost, duration, safety stars,
utility badge. Expandable leg list. Select button."

opencode "Generate apps/mobile/src/components/ExpenseWidget.tsx
Bottom overlay widget. Shows ceiling, spent (progress bar), remaining.
Tap to expand to full expense log. Quick-add expense button."

opencode "Generate apps/mobile/src/components/RerouteModal.tsx
Bottom sheet modal triggered when offRoute=true.
Shows 'Off Route Detected' header, new route options list,
Accept/Dismiss buttons. Calls selectRoute() on accept."

opencode "Generate apps/mobile/src/navigation/AppNavigator.tsx
Stack navigator wrapping AuthNavigator and TabNavigator.
Conditionally renders based on useAuthStore token."

opencode "Generate apps/mobile/src/navigation/TabNavigator.tsx
Bottom tabs: Journey (IntentInput), Map (MapView), Budget (ExpenseLog), Profile."

opencode "Generate apps/mobile/app.json with expo config, bundle identifiers,
maps API key from env, permissions for location (background + foreground)."

opencode "Generate apps/mobile/package.json with all dependencies:
expo ~51, expo-location, react-native-maps, zustand, axios,
@react-navigation/* packages, @supabase/supabase-js, AsyncStorage."
```

---

## STEP 6 — Infrastructure

```bash
opencode "Generate docker-compose.yml with services: postgres (postgis/postgis:16-3.4),
redis (redis:7-alpine), backend, ai_engine, safety_watcher.
All services use .env file. postgres and redis have healthchecks.
backend and safety_watcher depend on postgres+redis being healthy."

opencode "Generate .env.example with all required variables:
POSTGRES_*, SUPABASE_*, GROQ_API_KEY, TWILIO_*, REDIS_URL, EXPO_PUBLIC_* vars."

opencode "Generate infrastructure/docker/nginx/nginx.conf
Reverse proxy: / → backend:8000, /ai/ → ai_engine:8001.
WebSocket upgrade headers for /ws/ paths."
```

---

## STEP 7 — Final Wiring & Run

```bash
# Copy .env.example to .env and fill in your API keys
cp .env.example .env

# Start all services
docker compose up --build -d

# Verify DB is seeded
docker compose exec postgres psql -U raahi -d raahi_db \
  -c "SELECT name, risk_score FROM safety_zones;"

# Install mobile dependencies
cd apps/mobile && npm install

# Start mobile dev server
npx expo start
```

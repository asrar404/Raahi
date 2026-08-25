# RAAHI

Safety-first, budget-aware travel companion for Indian urban transit.

You describe a journey in plain language — *"Paharganj to Saket under ₹150 by
metro, travelling alone at night"* — and RAAHI plans routes ranked on **cost,
time and safety together**, then watches the journey as it happens. If you walk
into an area flagged as high risk, drift off your route, or stop moving, it
reroutes you and can alert your emergency contacts with your live location.

The safety model is not a wrapper around a maps API. Risk zones are PostGIS
polygons with separate day and night scores, blended with time-decaying
crowdsourced reports, and sampled along every candidate route before it is
ever shown to you.

---

## Contents

- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Running the mobile app](#running-the-mobile-app)
- [How the safety engine works](#how-the-safety-engine-works)
- [Service reference](#service-reference)
- [Configuration](#configuration)
- [Development without API keys](#development-without-api-keys)
- [Repository layout](#repository-layout)
- [Verification](#verification)
- [Limitations](#limitations)

---

## Architecture

```
┌──────────────────┐
│  Mobile (Expo)   │  React Native · Zustand · react-native-maps
└────┬─────────┬───┘
     │ REST    │ WebSocket (telemetry up, safety events down)
     ▼         ▼
┌─────────────────────────┐        ┌──────────────────────┐
│   API Gateway  :8000    │───────▶│   AI Engine  :8001   │
│   FastAPI + asyncpg     │◀───────│   LangGraph + Groq   │
│                         │ safety │                      │
│  · auth (Supabase JWT)  │ scores │  · intent parsing    │
│  · trips / legs         │        │  · geocoding         │
│  · budget               │        │  · route generation  │
│  · SOS + Twilio         │        │  · utility ranking   │
│  · WebSocket fan-out    │        └──────────────────────┘
└──┬───────────────┬──────┘
   │               │ XADD telemetry
   ▼               ▼
┌────────────┐  ┌─────────┐     ┌──────────────────────────┐
│ PostgreSQL │  │  Redis  │────▶│  Safety Watcher  :8002   │
│  + PostGIS │  │ Streams │     │  per-trip state machine  │
└────────────┘  └─────────┘     └──────────────────────────┘
      ▲                                      │
      └──────────────────────────────────────┘
              identical risk SQL
```

**Why the split.** The gateway owns the WebSocket, so it is the only service
that sees live telemetry. But the safety machine is *stateful* — stationary
timers, off-route strike counts, SOS de-duplication — and that state does not
belong in a request handler. Redis Streams carries each fix to the watcher,
which is free to be slow, restart, and resume exactly where it left off.

Both services call the **same PL/pgSQL functions** for risk evaluation. Risk
logic is deliberately not duplicated in Python: two services disagreeing about
whether someone is in danger is not an acceptable failure mode.

---

## Quick start

Requires Docker with Compose v2.

```bash
git clone <your-remote> raahi && cd raahi

cp .env.example .env
# Optional: add GROQ_API_KEY, Supabase and Twilio credentials.
# It runs fine without them — see "Development without API keys".

docker compose up --build -d
```

Wait for Postgres to become healthy (the seed scripts run on first boot only),
then check everything is up:

```bash
curl -s localhost:8000/health | python3 -m json.tool
curl -s localhost:8001/health | python3 -m json.tool
curl -s localhost:8002/health | python3 -m json.tool
```

Confirm the database seeded:

```bash
docker compose exec postgres psql -U raahi -d raahi_db \
  -c "SELECT name, city, risk_score, night_risk_score FROM safety_zones ORDER BY city, risk_score DESC;"
```

Plan a journey straight from the API:

```bash
curl -s -X POST localhost:8001/plan \
  -H 'Content-Type: application/json' \
  -d '{"user_input":"Paharganj to Saket under 150 rupees by metro, alone at night"}' \
  | python3 -m json.tool
```

Interactive API docs: <http://localhost:8000/docs> and <http://localhost:8001/docs>.

---

## Running the mobile app

```bash
cd apps/mobile
npm install
npx expo start
```

**On a physical device, `localhost` will not work.** It resolves to the phone,
not your machine. Set your LAN IP in `.env` before starting Expo:

```env
EXPO_PUBLIC_API_URL=http://192.168.1.5:8000
EXPO_PUBLIC_WS_URL=ws://192.168.1.5:8000
EXPO_PUBLIC_AI_URL=http://192.168.1.5:8001
```

Maps need a Google Maps API key in `EXPO_PUBLIC_GOOGLE_MAPS_KEY` (Maps SDK for
Android / iOS enabled). Without it the map renders blank but everything else
works.

Typecheck:

```bash
npm run typecheck
```

---

## How the safety engine works

### Risk scoring

`safety_zones` holds polygons with `risk_score` 1–5 and an optional
`night_risk_score` that takes over between 22:00 and 06:00 IST. A market that
is crowded and fine at noon can be a 4 at midnight, and collapsing that into
one number loses the thing that matters most.

`fn_point_safety_score(lat, lon, night)` returns a blended 0–5 score
(5 = safest):

- Containing zone risk 1–5 → base safety 5–1
- No zone data → 3.5, a mildly optimistic neutral
- Nearby hazard reports → penalty, attenuated by distance and weighted by severity
- `safe_spot` / `police_present` reports → small bonus
- Night → flat −0.3, because unlit streets are riskier everywhere

### Route scoring

`fn_route_safety_score` samples a polyline at 8 points and returns
**70% mean + 30% worst sample**. A pure average lets a long safe metro ride
mask a 15-minute walk through somewhere genuinely dangerous — which is exactly
the part of the journey you need warning about.

Per-leg scores are also blended with the mode's inherent safety, weighted
65/35 location-to-mode (85/15 for walking, since a pedestrian is far more
exposed to their surroundings than someone in a cab).

### Ranking

```
U = 0.35 · norm_cost + 0.30 · norm_time + 0.35 · norm_safety
```

Budget is a **hard filter**, not a penalty. Someone travelling on ₹150 cannot
take a ₹400 cab however well it scores, so over-budget routes are removed
before ranking. If nothing fits, the cheapest option is returned anyway,
explicitly flagged — *"the cheapest way is ₹210, which is ₹60 over"* beats an
empty screen.

### Live monitoring

The watcher's state machine:

```
IDLE ─▶ NAVIGATING ⇄ STATIONARY
            │ ⇄ OFF_ROUTE
            ├─▶ HIGH_RISK_ZONE ─▶ SOS_TRIGGERED
            └─▶ COMPLETED
```

Escalation fires on **transitions, not state**. Standing in a high-risk zone
for ten minutes is one incident, not forty — keying off steady state would text
your emergency contacts on every fix.

Debounce thresholds that matter:

| Trigger | Threshold | Why |
|---|---|---|
| Off route | 300 m, **3 consecutive fixes** | Urban GPS drifts badly; one bad fix must not trigger anything |
| Stationary | 25 m of movement, 5 min | Consumer GPS noise is ~10–20 m, so anything tighter reads noise as motion |
| Reroute offer | 180 s cooldown | Otherwise a genuinely lost traveller gets a new suggestion every 15 s |
| SOS | idempotent per trip, 600 s retry | Prevents SMS storms; the retry window stops one failed request permanently suppressing a real emergency |

### SOS

Manual (button) or automatic (zone entry). The pipeline:

1. Resolve location — falls back to the last stored fix, because the button is
   often pressed indoors with no signal
2. Dedupe against any unresolved SOS in the last 10 minutes
3. Snapshot the risk picture and find the nearest safe refuges
4. Flag the trip, write an immutable `sos_events` audit row
5. Broadcast to every WebSocket subscriber
6. SMS all contacts, then voice-call the primary two

Notification is awaited under a 20-second timeout so the client gets a real
delivery count, but a hanging provider cannot wedge the request. With
`TWILIO_ENABLED=false` it logs exactly what it would have sent — and the app
says so, rather than implying help is on the way.

---

## Service reference

| Service | Port | Health |
|---|---|---|
| API Gateway | 8000 | `/health`, `/health/live`, `/health/ready` |
| AI Engine | 8001 | `/health`, `/health/deps` |
| Safety Watcher | 8002 | `/health`, `/trips` |
| PostgreSQL + PostGIS | 5432 | `pg_isready` |
| Redis | 6379 | `redis-cli ping` |

### Key endpoints

**Gateway** — 34 routes total, see `/docs`.

```
POST   /api/v1/auth/verify              Exchange Supabase JWT for a profile
GET    /api/v1/auth/me
PATCH  /api/v1/auth/emergency-contacts

POST   /api/v1/trips                    Persist a chosen route
GET    /api/v1/trips/active             Restore an in-progress trip
POST   /api/v1/trips/{id}/start
POST   /api/v1/trips/{id}/advance-leg
GET    /api/v1/trips/{id}/intent        Replayed by the watcher on reroute

POST   /api/v1/safety/sos               Escalate (idempotent)
POST   /api/v1/safety/sos/resolve
GET    /api/v1/safety/risk              Full assessment for a coordinate
GET    /api/v1/safety/zones             GeoJSON polygons for a viewport
POST   /api/v1/safety/report            Crowdsourced hazard (auth optional)

POST   /api/v1/budget/log
GET    /api/v1/budget/{id}

WS     /ws/trip/{id}?token=...          Telemetry up, safety events down
WS     /ws/watch/{id}?token=...          Read-only, for a guardian
```

**AI Engine**

```
POST   /plan       Natural language -> ranked routes
POST   /reroute    Replan from a current position
GET    /geocode    Resolve a place name
```

### PostGIS functions

| Function | Purpose |
|---|---|
| `fn_get_risk_zone(lat, lon, min_risk, night)` | Containing zones at/above a threshold, worst first |
| `fn_find_safe_refuges(lat, lon, radius_m, max_risk)` | Nearest low-risk zones |
| `fn_is_off_route(trip_id, lat, lon, threshold_m)` | Deviation from in-progress legs |
| `fn_nearby_alerts(lat, lon, radius_m)` | Live crowdsourced reports |
| `fn_point_safety_score(lat, lon, night, radius_m)` | Blended 0–5 safety |
| `fn_route_safety_score(line, night, samples)` | Polyline safety |
| `fn_trip_budget_status(trip_id)` | Ceiling, spend, remaining, over-budget |
| `fn_is_night(at)` | Asia/Kolkata clock, single source of truth |
| `fn_ensure_telemetry_partition(date)` | Idempotent monthly partition creation |

---

## Configuration

Everything is environment-driven; see `.env.example` for the full list.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | asyncpg DSN. `+asyncpg` suffix is stripped if present |
| `REDIS_URL` | `redis://redis:6379/0` | Telemetry stream transport |
| `GROQ_API_KEY` | — | Optional. Falls back to a deterministic parser |
| `SUPABASE_JWT_SECRET` | — | Optional. Setting it closes the dev auth bypass |
| `TWILIO_ENABLED` | `false` | `false` logs SOS notifications instead of sending |
| `INTERNAL_API_KEY` | — | Shared secret for service-to-service calls |
| `RISK_THRESHOLD` | `3` | Zone risk at which SOS escalation begins |
| `OFF_ROUTE_THRESHOLD_M` | `300` | Metres before deviation is flagged |
| `OFF_ROUTE_STRIKES` | `3` | Consecutive fixes before rerouting |
| `W_COST` / `W_TIME` / `W_SAFETY` | `0.35` / `0.30` / `0.35` | Utility weights, normalised if they do not sum to 1 |

### Security note

Two safeguards are worth understanding before deploying:

1. **Dev auth bypass.** When `SUPABASE_JWT_SECRET` is empty *and*
   `ENVIRONMENT` is not `production`, unauthenticated requests resolve to the
   seeded demo user, and every use logs a warning. Setting either variable
   closes it.

2. **`INTERNAL_API_KEY`.** Guards `/safety/sos`, `/safety/notify-contacts`,
   `/safety/score-points` and `/trips/{id}/intent`, which the watcher and AI
   engine call without a user token. Compared in constant time. **Set this
   before exposing the gateway** — it protects the path that can text arbitrary
   phone numbers. The bundled `nginx.conf` also denies the internal-only
   endpoints from outside the Docker network.

---

## Development without API keys

The stack is fully functional with no third-party credentials, which is how it
should be evaluated locally.

| Missing | Behaviour |
|---|---|
| `GROQ_API_KEY` | A regex parser handles `<A> to <B> under ₹<N> by <mode>`, night/solo keywords, `by 9pm` deadlines and mode exclusions. Responses set `used_fallback_parser: true` and the app tells the user |
| Supabase | The app offers "continue in demo mode" against the seeded demo traveller |
| Twilio | SOS runs end to end and logs the exact SMS and TwiML it would have sent. The confirmation dialog states that nothing was actually sent |
| Google Maps key | The map is blank; navigation, risk banners and SOS all still work |

Geocoding never needs a key: a built-in gazetteer of **155 Indian transit
landmarks** across Delhi NCR, Mumbai, Jaipur and Bengaluru resolves offline
with fuzzy matching, so `pahar ganj`, `PAHARGUNJ` and `Saket Metro Station` all
land correctly. Nominatim is available as an opt-in fallback
(`ENABLE_NOMINATIM=true`), off by default because the public endpoint is
rate-limited to roughly 1 req/s.

---

## Repository layout

```
raahi/
├── apps/mobile/                  React Native (Expo SDK 51)
│   └── src/
│       ├── constants/            colours, config, route names
│       ├── store/                4 Zustand stores + shared types
│       ├── services/             api, supabase, websocket, location
│       ├── hooks/                useWebSocket, useLocation, useSOS, useTrip
│       ├── components/           SOSButton, RouteCard, SafetyHeatmap, ...
│       ├── screens/              Auth, IntentInput, RouteSelection, MapView, ...
│       └── navigation/           App / Auth / Tab navigators
│
├── services/
│   ├── backend/                  FastAPI gateway (asyncpg, no ORM)
│   │   └── app/{models,routers,services,middleware}
│   ├── ai_engine/                LangGraph planner
│   │   └── app/{schemas,agents,graphs,tools,prompts}
│   └── safety_watcher/           Redis Stream consumer + state machine
│
├── infrastructure/docker/
│   ├── postgres/init/            01_extensions → 05_seed
│   └── nginx/nginx.conf
│
├── docs/                         Original design blueprints
└── docker-compose.yml
```

---

## Verification

```bash
# Python — all three services compile and import
python3 -m compileall -q services/

# Gateway route table (34 HTTP paths + 3 WebSocket)
cd services/backend && python -c "import app.main as m; print(len(m.app.openapi()['paths']))"

# Planner, end to end, no LLM or database required
cd services/ai_engine && python -c "
import asyncio
from app.graphs.planning_graph import run_planning
r = asyncio.run(run_planning('Paharganj to Saket under 150 by metro at night'))
for rt in r['routes']:
    print(rt.total_cost, rt.total_duration, rt.safety_rating, rt.mode_sequence)
"

# Mobile — strict typecheck, passes clean
cd apps/mobile && npm run typecheck
```

The Compose file, nginx config and SQL init scripts are also structurally
validated; see the commit history for the checks that were run.

---

## Limitations

Stated plainly, because a safety tool that overstates its coverage is worse
than one that does not exist.

- **Transit data is synthetic.** `services/ai_engine/app/tools/transit_api.py`
  generates realistically-shaped routes from a fare and speed model calibrated
  against published operator tariffs (Delhi Metro slabs, DTC/BEST slabs, auto
  and cab per-km rates). It does **not** know timetables, live delays, service
  outages, or where stations actually are. Boarding points are interpolated
  along the corridor. This module is the single integration point for GTFS
  static + GTFS-RT, an aggregator estimate API, and a routing engine such as
  OSRM or Valhalla — nothing else in the planner needs to change.

- **Safety zone data is illustrative.** The seeded polygons are development
  fixtures for demonstrating the engine, not an authoritative or endorsed
  assessment of any locality. Production data should come from verified
  partners and moderated community reports.

- **Route lines are straight segments.** Off-route detection measures against
  leg endpoints rather than real road geometry, so the 300 m threshold is
  coarser than it looks on a winding route.

- **Single-replica WebSocket fan-out.** `ws_manager` holds connections in
  process memory. Running more than one gateway replica needs a Redis pub/sub
  bridge so an event raised on instance A reaches a socket on instance B; the
  transport (`redis_bus.publish_event`) exists, the subscriber loop does not.

- **No push notifications.** Safety events reach the app over the WebSocket
  only, so a fully backgrounded or killed app will not be alerted. Production
  needs FCM/APNs alongside this.

- **Background location is best-effort.** Requested but never required. A user
  who declines it keeps full navigation and monitoring while the app is open,
  and loses only screen-off SOS detection.

- **Not a substitute for emergency services.** RAAHI alerts contacts you
  choose. It does not contact police or ambulance services.

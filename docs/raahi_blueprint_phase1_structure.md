# RAAHI — Phase 1: Project Repository Structure

## Monorepo Layout

```
raahi/
├── .env.example
├── .gitignore
├── README.md
├── docker-compose.yml
│
├── apps/
│   └── mobile/                          # React Native / Expo app
│       ├── app.json
│       ├── package.json
│       ├── tsconfig.json
│       ├── babel.config.js
│       ├── assets/
│       │   ├── icon.png
│       │   └── splash.png
│       └── src/
│           ├── index.ts
│           ├── App.tsx
│           ├── constants/
│           │   ├── colors.ts
│           │   ├── config.ts
│           │   └── routes.ts
│           ├── store/
│           │   ├── index.ts
│           │   ├── authSlice.ts
│           │   ├── tripSlice.ts
│           │   ├── safetySlice.ts
│           │   └── budgetSlice.ts
│           ├── hooks/
│           │   ├── useWebSocket.ts
│           │   ├── useLocation.ts
│           │   ├── useSOS.ts
│           │   └── useTrip.ts
│           ├── services/
│           │   ├── api.ts
│           │   ├── supabase.ts
│           │   ├── websocket.ts
│           │   └── location.ts
│           ├── screens/
│           │   ├── AuthScreen.tsx
│           │   ├── IntentInputScreen.tsx
│           │   ├── RouteSelectionScreen.tsx
│           │   ├── MapViewScreen.tsx
│           │   ├── ExpenseLogScreen.tsx
│           │   └── ProfileScreen.tsx
│           ├── components/
│           │   ├── SOSButton.tsx
│           │   ├── RouteCard.tsx
│           │   ├── SafetyHeatmap.tsx
│           │   ├── ExpenseWidget.tsx
│           │   ├── RerouteModal.tsx
│           │   └── MapOverlay.tsx
│           └── navigation/
│               ├── AppNavigator.tsx
│               ├── AuthNavigator.tsx
│               └── TabNavigator.tsx
│
├── services/
│   ├── backend/                         # FastAPI Gateway
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── pyproject.toml
│   │   └── app/
│   │       ├── main.py
│   │       ├── config.py
│   │       ├── dependencies.py
│   │       ├── models/
│   │       │   ├── __init__.py
│   │       │   ├── user.py
│   │       │   ├── trip.py
│   │       │   ├── safety.py
│   │       │   └── budget.py
│   │       ├── routers/
│   │       │   ├── __init__.py
│   │       │   ├── auth.py
│   │       │   ├── trips.py
│   │       │   ├── safety.py
│   │       │   ├── budget.py
│   │       │   └── websocket.py
│   │       ├── services/
│   │       │   ├── __init__.py
│   │       │   ├── db.py
│   │       │   ├── postgis.py
│   │       │   ├── twilio_notifier.py
│   │       │   └── ws_manager.py
│   │       └── middleware/
│   │           ├── __init__.py
│   │           ├── auth.py
│   │           └── logging.py
│   │
│   ├── ai_engine/                       # LangGraph AI microservice
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py
│   │       ├── config.py
│   │       ├── schemas/
│   │       │   ├── __init__.py
│   │       │   ├── intent.py
│   │       │   ├── route.py
│   │       │   └── replan.py
│   │       ├── agents/
│   │       │   ├── __init__.py
│   │       │   ├── intent_parser.py
│   │       │   ├── planner_agent.py
│   │       │   └── reroute_agent.py
│   │       ├── graphs/
│   │       │   ├── __init__.py
│   │       │   ├── planning_graph.py
│   │       │   └── reroute_graph.py
│   │       ├── tools/
│   │       │   ├── __init__.py
│   │       │   ├── transit_api.py
│   │       │   ├── safety_scorer.py
│   │       │   └── budget_filter.py
│   │       └── prompts/
│   │           ├── intent_parser.txt
│   │           ├── planner.txt
│   │           └── rerouter.txt
│   │
│   └── safety_watcher/                  # Background safety microservice
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app/
│           ├── main.py
│           ├── config.py
│           ├── watcher.py
│           ├── state_machine.py
│           ├── geofence_evaluator.py
│           ├── sos_pipeline.py
│           └── telemetry_consumer.py
│
└── infrastructure/
    └── docker/
        ├── postgres/
        │   ├── Dockerfile
        │   └── init/
        │       ├── 01_extensions.sql
        │       ├── 02_schema.sql
        │       ├── 03_indexes.sql
        │       ├── 04_functions.sql
        │       └── 05_seed.sql
        └── nginx/
            └── nginx.conf
```

## Technology Stack Summary

| Layer | Technology |
|---|---|
| Mobile | React Native (Expo SDK 51), Zustand, react-native-maps / Mapbox |
| API Gateway | FastAPI 0.111, Python 3.12, SQLAlchemy 2.0 async |
| AI Engine | LangGraph 0.2, LangChain, Groq (llama-3.3-70b) / Claude |
| Safety Watcher | FastAPI + asyncio background tasks, Redis Streams |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Auth | Supabase Auth (JWT) |
| Realtime | WebSockets (FastAPI native) |
| Notifications | Twilio SMS + Voice |
| Caching | Redis 7 |
| Containerisation | Docker Compose (dev), Kubernetes-ready |

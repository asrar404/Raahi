/**
 * Shared domain types, mirroring the backend's Pydantic models.
 *
 * Kept in the store layer so screens, hooks and services all agree on shape.
 * These correspond 1:1 with services/ai_engine/app/schemas/intent.py and
 * services/backend/app/models/.
 */

export type TransitMode =
  | 'walk'
  | 'metro'
  | 'bus'
  | 'train'
  | 'auto'
  | 'cab'
  | 'rapido'
  | 'ferry';

export type TripStatus = 'planned' | 'active' | 'completed' | 'cancelled' | 'sos';

export type LegStatus = 'pending' | 'in_progress' | 'completed' | 'skipped';

export type ExpenseCategory = 'transit' | 'food' | 'stay' | 'misc';

/** One segment of a route, as returned by the AI engine. */
export interface RouteLeg {
  leg_order: number;
  mode: TransitMode;
  from_name: string;
  from_lat: number;
  from_lon: number;
  to_name: string;
  to_lat: number;
  to_lon: number;
  distance_km: number;
  planned_cost: number;
  duration_mins: number;
  provider?: string | null;
  safety_score: number;
}

/** A ranked itinerary from POST /plan. */
export interface PlannedRoute {
  route_id: string;
  legs: RouteLeg[];
  total_cost: number;
  total_duration: number;
  utility_score: number;
  safety_rating: number;
  summary: string;
  warnings?: string[];
}

/** A leg as persisted by the gateway (has an id and actuals). */
export interface TripLeg extends Omit<RouteLeg, 'duration_mins'> {
  id: string;
  trip_id: string;
  route_coords?: number[][] | null;
  planned_duration_mins?: number | null;
  actual_duration_mins?: number | null;
  actual_cost?: number | null;
  status: LegStatus;
  booking_ref?: string | null;
  departed_at?: string | null;
  arrived_at?: string | null;
}

export interface Trip {
  id: string;
  user_id: string;
  status: TripStatus;
  origin_name: string;
  origin_lat: number;
  origin_lon: number;
  dest_name: string;
  dest_lat: number;
  dest_lon: number;
  budget_ceiling: number;
  time_deadline?: string | null;
  transit_prefs: string[];
  total_planned_cost?: number | null;
  total_actual_cost: number;
  planned_eta?: string | null;
  actual_eta?: string | null;
  utility_score?: number | null;
  safety_priority: boolean;
  raw_intent?: string | null;
  intent_json: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  legs: TripLeg[];
}

export interface EmergencyContact {
  name: string;
  phone: string;
  relation?: string | null;
}

export interface UserProfile {
  id: string;
  supabase_uid: string;
  full_name: string;
  phone: string;
  email?: string | null;
  gender?: string | null;
  preferred_modes: string[];
  budget_default: number;
  emergency_contacts: EmergencyContact[];
  sos_enabled: boolean;
  home_city?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ParsedIntent {
  source_raw: string;
  source_lat?: number | null;
  source_lon?: number | null;
  destination_raw: string;
  dest_lat?: number | null;
  dest_lon?: number | null;
  budget_ceiling: number;
  time_deadline?: string | null;
  preferred_modes: TransitMode[];
  safety_priority: boolean;
  night_travel: boolean;
  city?: string | null;
  confidence: number;
}

export interface ResolvedPlace {
  query: string;
  name: string;
  lat: number;
  lon: number;
  city?: string | null;
  source: string;
  confidence: number;
}

export interface PlanResponse {
  routes: PlannedRoute[];
  intent?: ParsedIntent | null;
  origin?: ResolvedPlace | null;
  destination?: ResolvedPlace | null;
  error?: string | null;
  warnings: string[];
  used_fallback_parser: boolean;
  duration_ms?: number | null;
}

export interface RiskZone {
  zone_id: string;
  zone_name: string;
  risk_score: number;
  risk_factors: string[];
}

export interface SafeRefuge {
  zone_id: string;
  zone_name: string;
  risk_score: number;
  distance_m: number;
}

/** A crowdsourced report or safety event surfaced to the user. */
export interface SafetyAlert {
  id: string;
  type: 'sos' | 'reroute' | 'off_route' | 'risk' | 'report' | 'budget' | 'info';
  message: string;
  severity?: number;
  lat?: number;
  lon?: number;
  at: string;
}

export interface ExpenseLog {
  id: string;
  trip_id: string;
  leg_id?: string | null;
  amount: number;
  category: ExpenseCategory;
  description?: string | null;
  recorded_at: string;
}

export interface BudgetSummary {
  trip_id: string;
  ceiling: number;
  planned: number;
  spent: number;
  remaining: number;
  percent_used: number;
  over_budget: boolean;
  logs: ExpenseLog[];
}

/** Server -> client WebSocket event names. Mirrors ws_manager.Event. */
export type WsEvent =
  | 'SOS_ALERT'
  | 'SOS_RESOLVED'
  | 'REROUTE'
  | 'RISK_UPDATE'
  | 'OFF_ROUTE'
  | 'BACK_ON_ROUTE'
  | 'BUDGET_ALERT'
  | 'LEG_ADVANCED'
  | 'TRIP_COMPLETED'
  | 'TELEMETRY_ACK'
  | 'PONG'
  | 'ERROR';

export interface WsMessage<T = Record<string, unknown>> {
  event: WsEvent;
  data: T;
  ts: string;
}

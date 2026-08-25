/**
 * HTTP clients for the gateway and the AI engine.
 *
 * Two instances because they have very different timeout profiles: REST calls
 * should fail fast, but planning involves an LLM round trip and legitimately
 * takes tens of seconds.
 *
 * Errors are normalised into `ApiError` so screens can render one readable
 * message instead of digging through axios internals.
 */

import axios, { AxiosError, type AxiosInstance } from 'axios';

import { AI_ENGINE_URL, API_BASE_URL, timeouts } from '../constants/config';
import { getAuthToken, useAuthStore } from '../store/authSlice';
import type {
  BudgetSummary,
  EmergencyContact,
  ExpenseCategory,
  PlanResponse,
  PlannedRoute,
  Trip,
  UserProfile,
} from '../store/types';

export class ApiError extends Error {
  readonly status?: number;
  readonly requestId?: string;
  readonly fieldErrors?: { field: string; message: string }[];

  constructor(
    message: string,
    status?: number,
    requestId?: string,
    fieldErrors?: { field: string; message: string }[],
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.requestId = requestId;
    this.fieldErrors = fieldErrors;
  }
}

/** Turn any axios failure into something worth showing a user. */
function normaliseError(error: unknown): ApiError {
  if (!axios.isAxiosError(error)) {
    return new ApiError(error instanceof Error ? error.message : 'Unexpected error');
  }

  const axiosError = error as AxiosError<{
    detail?: string | { msg?: string }[];
    errors?: { field: string; message: string }[];
    request_id?: string;
  }>;

  if (axiosError.code === 'ECONNABORTED') {
    return new ApiError('The request timed out. Check your connection and try again.');
  }
  if (!axiosError.response) {
    return new ApiError(
      'Cannot reach the RAAHI server. Check your connection, or that the backend is running.',
    );
  }

  const { status, data } = axiosError.response;
  const requestId = data?.request_id;

  let message: string;
  if (typeof data?.detail === 'string') {
    message = data.detail;
  } else if (Array.isArray(data?.detail)) {
    message = data.detail.map((d) => d?.msg ?? String(d)).join(', ');
  } else if (status === 401) {
    message = 'Your session has expired. Please sign in again.';
  } else if (status === 404) {
    message = 'Not found.';
  } else if (status >= 500) {
    message = 'The server had a problem. Please try again shortly.';
  } else {
    message = axiosError.message;
  }

  return new ApiError(message, status, requestId, data?.errors);
}

function attachInterceptors(client: AxiosInstance, withAuth: boolean): AxiosInstance {
  if (withAuth) {
    client.interceptors.request.use((config) => {
      const token = getAuthToken();
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
      return config;
    });
  }

  client.interceptors.response.use(
    (response) => response,
    (error) => {
      // A 401 means the token is dead. Clearing it here sends the navigator
      // back to the auth flow rather than leaving every screen erroring.
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        useAuthStore.getState().logout();
      }
      return Promise.reject(normaliseError(error));
    },
  );

  return client;
}

export const api = attachInterceptors(
  axios.create({ baseURL: API_BASE_URL, timeout: timeouts.api }),
  true,
);

export const aiApi = attachInterceptors(
  axios.create({ baseURL: AI_ENGINE_URL, timeout: timeouts.ai }),
  false,
);

// ============================================================
// Auth
// ============================================================
export async function verifyAuth(payload: {
  full_name?: string;
  phone?: string;
  email?: string;
  gender?: string;
  home_city?: string;
}): Promise<{ user: UserProfile; created: boolean }> {
  const { data } = await api.post('/api/v1/auth/verify', payload);
  return data;
}

export async function fetchMe(): Promise<UserProfile> {
  const { data } = await api.get('/api/v1/auth/me');
  return data;
}

export async function updateProfile(patch: Partial<UserProfile>): Promise<UserProfile> {
  const { data } = await api.patch('/api/v1/auth/me', patch);
  return data;
}

export async function updateEmergencyContacts(
  contacts: EmergencyContact[],
): Promise<UserProfile> {
  const { data } = await api.patch('/api/v1/auth/emergency-contacts', { contacts });
  return data;
}

export async function fetchAuthConfig(): Promise<{
  auth_configured: boolean;
  dev_bypass_active: boolean;
  environment: string;
}> {
  const { data } = await api.get('/api/v1/auth/config');
  return data;
}

// ============================================================
// Planning (AI engine)
// ============================================================
export async function planTrip(
  userInput: string,
  opts: { lat?: number; lon?: number; city?: string } = {},
): Promise<PlanResponse> {
  const { data } = await aiApi.post('/plan', {
    user_input: userInput,
    origin_lat: opts.lat,
    origin_lon: opts.lon,
    city: opts.city,
  });
  return data;
}

// ============================================================
// Trips
// ============================================================
/**
 * Persist a selected route as a trip.
 *
 * The AI engine's leg shape differs slightly from the gateway's: `route_coords`
 * is derived here from the leg endpoints so off-route detection has a reference
 * path to measure against.
 */
export async function createTrip(
  route: PlannedRoute,
  meta: {
    originName: string;
    originLat: number;
    originLon: number;
    destName: string;
    destLat: number;
    destLon: number;
    budgetCeiling: number;
    rawIntent?: string;
    intentJson?: Record<string, unknown>;
    safetyPriority?: boolean;
    timeDeadline?: string | null;
  },
): Promise<Trip> {
  const { data } = await api.post('/api/v1/trips', {
    origin_name: meta.originName,
    origin_lat: meta.originLat,
    origin_lon: meta.originLon,
    dest_name: meta.destName,
    dest_lat: meta.destLat,
    dest_lon: meta.destLon,
    budget_ceiling: meta.budgetCeiling,
    time_deadline: meta.timeDeadline ?? null,
    total_planned_cost: route.total_cost,
    utility_score: route.utility_score,
    safety_priority: meta.safetyPriority ?? true,
    raw_intent: meta.rawIntent ?? null,
    intent_json: meta.intentJson ?? {},
    transit_prefs: Array.from(new Set(route.legs.map((leg) => leg.mode))),
    legs: route.legs.map((leg) => ({
      leg_order: leg.leg_order,
      mode: leg.mode,
      from_name: leg.from_name,
      from_lat: leg.from_lat,
      from_lon: leg.from_lon,
      to_name: leg.to_name,
      to_lat: leg.to_lat,
      to_lon: leg.to_lon,
      route_coords: [
        [leg.from_lat, leg.from_lon],
        [leg.to_lat, leg.to_lon],
      ],
      distance_km: leg.distance_km,
      planned_cost: leg.planned_cost,
      planned_duration_mins: leg.duration_mins,
      provider: leg.provider ?? null,
      safety_score: leg.safety_score,
    })),
  });
  return data;
}

export async function fetchTrip(tripId: string): Promise<Trip> {
  const { data } = await api.get(`/api/v1/trips/${tripId}`);
  return data;
}

export async function fetchActiveTrip(): Promise<Trip | null> {
  const { data } = await api.get('/api/v1/trips/active');
  return data ?? null;
}

export async function startTrip(tripId: string): Promise<Trip> {
  const { data } = await api.post(`/api/v1/trips/${tripId}/start`);
  return data;
}

export async function advanceLeg(tripId: string): Promise<Trip> {
  const { data } = await api.post(`/api/v1/trips/${tripId}/advance-leg`);
  return data;
}

export async function completeTrip(tripId: string): Promise<Trip> {
  const { data } = await api.post(`/api/v1/trips/${tripId}/complete`);
  return data;
}

export async function cancelTrip(tripId: string): Promise<{ status: string }> {
  const { data } = await api.delete(`/api/v1/trips/${tripId}`);
  return data;
}

export async function fetchTripHistory(limit = 20): Promise<Trip[]> {
  const { data } = await api.get('/api/v1/trips', { params: { limit } });
  return data;
}

// ============================================================
// Safety
// ============================================================
export async function sendSOS(payload: {
  trip_id?: string | null;
  lat?: number;
  lon?: number;
  trigger_source?: 'manual' | 'auto';
}): Promise<{
  sos_event_id?: string;
  contacts_alerted: number;
  sms_sent: number;
  calls_placed: number;
  twilio_enabled: boolean;
  already_active: boolean;
  safe_refuges: { zone_name: string; distance_m: number }[];
}> {
  const { data } = await api.post('/api/v1/safety/sos', {
    ...payload,
    trigger_source: payload.trigger_source ?? 'manual',
  });
  return data;
}

export async function resolveSOS(tripId: string): Promise<{ resolved: boolean }> {
  const { data } = await api.post('/api/v1/safety/sos/resolve', { trip_id: tripId });
  return data;
}

export async function fetchRisk(
  lat: number,
  lon: number,
  tripId?: string | null,
): Promise<{
  in_high_risk: boolean;
  max_risk: number;
  risk_zones: { zone_id: string; zone_name: string; risk_score: number; risk_factors: string[] }[];
  off_route: boolean;
  safety_score: number | null;
  safe_refuges: { zone_id: string; zone_name: string; risk_score: number; distance_m: number }[];
  night_mode: boolean;
}> {
  const { data } = await api.get('/api/v1/safety/risk', {
    params: { lat, lon, trip_id: tripId ?? undefined },
  });
  return data;
}

export async function fetchZones(bbox: {
  minLat: number;
  minLon: number;
  maxLat: number;
  maxLon: number;
}): Promise<
  {
    id: string;
    name: string;
    city: string;
    risk_score: number;
    night_risk_score: number | null;
    time_sensitive: boolean;
    risk_factors: string[];
    geojson: string;
    center_lat: number;
    center_lon: number;
  }[]
> {
  const { data } = await api.get('/api/v1/safety/zones', {
    params: {
      min_lat: bbox.minLat,
      min_lon: bbox.minLon,
      max_lat: bbox.maxLat,
      max_lon: bbox.maxLon,
    },
  });
  return data;
}

export async function fetchNearbyAlerts(
  lat: number,
  lon: number,
  radiusM = 500,
): Promise<{
  count: number;
  alerts: {
    report_id: string;
    category: string;
    severity: number;
    distance_m: number;
    description: string | null;
    lat: number;
    lon: number;
    created_at: string;
  }[];
}> {
  const { data } = await api.get('/api/v1/safety/alerts', {
    params: { lat, lon, radius_m: radiusM },
  });
  return data;
}

export async function submitReport(payload: {
  lat: number;
  lon: number;
  category: string;
  description?: string;
  severity?: number;
}): Promise<{ id: string }> {
  const { data } = await api.post('/api/v1/safety/report', payload);
  return data;
}

// ============================================================
// Budget
// ============================================================
export async function logExpense(
  tripId: string,
  amount: number,
  category: ExpenseCategory,
  description?: string,
  legId?: string | null,
): Promise<BudgetSummary> {
  const { data } = await api.post('/api/v1/budget/log', {
    trip_id: tripId,
    amount,
    category,
    description: description ?? null,
    leg_id: legId ?? null,
  });
  return data;
}

export async function fetchBudget(tripId: string): Promise<BudgetSummary> {
  const { data } = await api.get(`/api/v1/budget/${tripId}`);
  return data;
}

export async function fetchBudgetAlert(tripId: string): Promise<{
  over_budget: boolean;
  percent_used: number;
  remaining: number;
  approaching_limit: boolean;
  severity: string;
}> {
  const { data } = await api.get(`/api/v1/budget/${tripId}/alert`);
  return data;
}

export async function deleteExpense(expenseId: string): Promise<BudgetSummary> {
  const { data } = await api.delete(`/api/v1/budget/log/${expenseId}`);
  return data;
}

// ============================================================
// Health
// ============================================================
export async function checkHealth(): Promise<{ status: string; service: string }> {
  const { data } = await api.get('/health');
  return data;
}

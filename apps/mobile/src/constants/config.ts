/**
 * Runtime configuration.
 *
 * Every value comes from an EXPO_PUBLIC_* environment variable, inlined into
 * the bundle at build time.
 *
 * Note on localhost: `localhost` from a physical device or the Android
 * emulator resolves to the device itself, not your development machine. Set
 * EXPO_PUBLIC_API_URL to your machine's LAN IP (e.g. http://192.168.1.5:8000)
 * when testing on hardware.
 */

const fallback = {
  api: 'http://localhost:8000',
  ws: 'ws://localhost:8000',
  ai: 'http://localhost:8001',
};

function clean(url: string | undefined, dflt: string): string {
  const value = (url ?? '').trim();
  if (!value) return dflt;
  // Trailing slashes produce doubled separators once paths are appended
  return value.replace(/\/+$/, '');
}

export const API_BASE_URL = clean(process.env.EXPO_PUBLIC_API_URL, fallback.api);
export const API_WS_URL = clean(process.env.EXPO_PUBLIC_WS_URL, fallback.ws);
export const AI_ENGINE_URL = clean(process.env.EXPO_PUBLIC_AI_URL, fallback.ai);

export const SUPABASE_URL = (process.env.EXPO_PUBLIC_SUPABASE_URL ?? '').trim();
export const SUPABASE_ANON_KEY = (process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? '').trim();
export const GOOGLE_MAPS_KEY = (process.env.EXPO_PUBLIC_GOOGLE_MAPS_KEY ?? '').trim();

/** True when Supabase credentials are present. When false the app runs in
 *  local/dev mode against the gateway's demo-user fallback. */
export const AUTH_ENABLED = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

export const timeouts = {
  /** Standard REST calls. */
  api: 20_000,
  /** Planning involves an LLM round trip, so it needs longer. */
  ai: 60_000,
} as const;

export const telemetry = {
  /** Minimum interval between location reports, ms. Matches the server's
   *  expectation and keeps battery drain reasonable. */
  intervalMs: 15_000,
  /** Report sooner if the device moves this far, metres. */
  distanceM: 20,
  /** WebSocket reconnect backoff, ms. */
  reconnectBaseMs: 1_000,
  reconnectMaxMs: 30_000,
  /** Give up reconnecting after this many consecutive failures. */
  maxReconnectAttempts: 10,
} as const;

export const map = {
  /** Default viewport: central Delhi. */
  initialRegion: {
    latitude: 28.6139,
    longitude: 77.209,
    latitudeDelta: 0.08,
    longitudeDelta: 0.08,
  },
  /** Zoom applied when following the user. */
  followDelta: 0.012,
} as const;

export const budget = {
  /** Percentage of the ceiling at which the widget turns amber. */
  warnAtPercent: 80,
} as const;

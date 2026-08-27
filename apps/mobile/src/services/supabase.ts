/**
 * Supabase client.
 *
 * Optional by design. When EXPO_PUBLIC_SUPABASE_URL / ANON_KEY are unset the
 * client is null and the app runs against the gateway's demo-user fallback,
 * so the whole product can be exercised locally without provisioning a
 * Supabase project.
 *
 * `react-native-url-polyfill` must be imported before the SDK: supabase-js
 * relies on URL/URLSearchParams, which React Native does not fully implement.
 */

import 'react-native-url-polyfill/auto';

import AsyncStorage from '@react-native-async-storage/async-storage';
import { type SupabaseClient, createClient } from '@supabase/supabase-js';

import { AUTH_ENABLED, SUPABASE_ANON_KEY, SUPABASE_URL } from '../constants/config';
import { useAuthStore } from '../store/authSlice';

// Debug: log Supabase client init config (only in development)
if (__DEV__) {
  console.log('[Supabase Client] Initializing with:', {
    url: SUPABASE_URL ? 'SET' : 'NOT SET',
    anonKey: SUPABASE_ANON_KEY ? 'SET (starts with ' + SUPABASE_ANON_KEY.substring(0, 10) + '...)' : 'NOT SET',
    authEnabled: AUTH_ENABLED,
  });
}

export const supabase: SupabaseClient | null = AUTH_ENABLED
  ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      auth: {
        storage: AsyncStorage,
        autoRefreshToken: true,
        persistSession: true,
        // No URL-based session detection: this is a native app, not a browser
        // handling an OAuth redirect fragment.
        detectSessionInUrl: false,
      },
    })
  : null;

export const isAuthEnabled = AUTH_ENABLED;

/**
 * Mirror Supabase's token into the auth store, and keep it current.
 *
 * Access tokens are short-lived. Without subscribing to refreshes, requests
 * begin failing with 401 roughly an hour into a session — which for a trip in
 * progress would break telemetry at the worst possible moment.
 */
export function initAuthListener(): () => void {
  if (!supabase) return () => undefined;

  void supabase.auth.getSession().then(({ data }) => {
    if (data.session?.access_token) {
      useAuthStore.getState().setToken(data.session.access_token);
    }
  });

  const { data: subscription } = supabase.auth.onAuthStateChange((event, session) => {
    if (session?.access_token) {
      useAuthStore.getState().setToken(session.access_token);
    } else if (event === 'SIGNED_OUT') {
      useAuthStore.getState().logout();
    }
  });

  return () => subscription.subscription.unsubscribe();
}

// ── Phone OTP ───────────────────────────────────────────────
export async function sendOtp(phone: string): Promise<void> {
  if (!supabase) throw new Error('Supabase is not configured on this build');
  const { error } = await supabase.auth.signInWithOtp({ phone });
  if (error) {
    console.error('[Supabase] sendOtp error:', { message: error.message, code: error.status, details: error });
    throw new Error(error.message);
  }
}

export async function verifyOtp(phone: string, token: string): Promise<string> {
  if (!supabase) throw new Error('Supabase is not configured on this build');
  const { data, error } = await supabase.auth.verifyOtp({ phone, token, type: 'sms' });
  if (error) {
    console.error('[Supabase] verifyOtp error:', { message: error.message, code: error.status, details: error });
    throw new Error(error.message);
  }
  const accessToken = data.session?.access_token;
  if (!accessToken) throw new Error('Verification succeeded but no session was returned');
  useAuthStore.getState().setToken(accessToken);
  return accessToken;
}

// ── Email + password ────────────────────────────────────────
export async function signInWithEmail(email: string, password: string): Promise<string> {
  if (!supabase) throw new Error('Supabase is not configured on this build');
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    console.error('[Supabase] signInWithEmail error:', {
      message: error.message,
      code: error.status,
      details: error,
      name: error.name,
      cause: error.cause,
    });
    throw new Error(error.message);
  }
  const accessToken = data.session?.access_token;
  if (!accessToken) throw new Error('Sign-in succeeded but no session was returned');
  useAuthStore.getState().setToken(accessToken);
  return accessToken;
}

export async function signUpWithEmail(
  email: string,
  password: string,
): Promise<string | null> {
  if (!supabase) throw new Error('Supabase is not configured on this build');
  const { data, error } = await supabase.auth.signUp({ email, password });
  if (error) {
    console.error('[Supabase] signUpWithEmail error:', {
      message: error.message,
      code: error.status,
      details: error,
      name: error.name,
      cause: error.cause,
    });
    throw new Error(error.message);
  }
  const accessToken = data.session?.access_token ?? null;
  if (accessToken) useAuthStore.getState().setToken(accessToken);
  return accessToken;
}

export async function signOut(): Promise<void> {
  if (supabase) {
    await supabase.auth.signOut();
  }
  useAuthStore.getState().logout();
}

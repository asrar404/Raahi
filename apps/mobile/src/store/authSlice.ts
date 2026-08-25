/**
 * Auth store.
 *
 * Persisted to AsyncStorage so a returning user is not forced back through
 * sign-in. The Supabase access token is short-lived; `services/supabase.ts`
 * refreshes it and pushes updates in here.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import type { UserProfile } from './types';

interface AuthState {
  user: UserProfile | null;
  token: string | null;
  /** True once the persisted state has been read from disk. Screens wait on
   *  this before deciding whether to show the auth flow, otherwise a logged-in
   *  user briefly sees the sign-in screen on every cold start. */
  hydrated: boolean;

  setUser: (user: UserProfile, token: string | null) => void;
  setToken: (token: string | null) => void;
  patchUser: (patch: Partial<UserProfile>) => void;
  logout: () => void;
  setHydrated: (value: boolean) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      hydrated: false,

      setUser: (user, token) => set({ user, token }),
      setToken: (token) => set({ token }),
      patchUser: (patch) =>
        set((state) => (state.user ? { user: { ...state.user, ...patch } } : {})),
      logout: () => set({ user: null, token: null }),
      setHydrated: (hydrated) => set({ hydrated }),
    }),
    {
      name: 'raahi-auth',
      storage: createJSONStorage(() => AsyncStorage),
      // `hydrated` is runtime-only; persisting it would restore a stale true.
      partialize: (state) => ({ user: state.user, token: state.token }),
      onRehydrateStorage: () => (state) => {
        state?.setHydrated(true);
      },
    },
  ),
);

/** Non-reactive token read, for use inside axios interceptors. */
export const getAuthToken = (): string | null => useAuthStore.getState().token;
export const getCurrentUser = (): UserProfile | null => useAuthStore.getState().user;

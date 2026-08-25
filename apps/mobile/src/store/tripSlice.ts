/**
 * Trip store: planned routes, the selected route, and the live trip.
 *
 * Not persisted. A stale "active trip" restored from disk would be worse than
 * none — the app would show navigation for a journey that finished yesterday.
 * `GET /api/v1/trips/active` is the source of truth on launch.
 */

import { create } from 'zustand';

import type { ParsedIntent, PlannedRoute, ResolvedPlace, Trip } from './types';

interface TripState {
  // Planning
  routes: PlannedRoute[];
  selectedRoute: PlannedRoute | null;
  intent: ParsedIntent | null;
  origin: ResolvedPlace | null;
  destination: ResolvedPlace | null;
  planWarnings: string[];
  planning: boolean;
  planError: string | null;

  // Live trip
  activeTrip: Trip | null;
  activeLegIdx: number;
  starting: boolean;

  // Planning actions
  setPlanning: (value: boolean) => void;
  setPlanResult: (payload: {
    routes: PlannedRoute[];
    intent?: ParsedIntent | null;
    origin?: ResolvedPlace | null;
    destination?: ResolvedPlace | null;
    warnings?: string[];
  }) => void;
  setPlanError: (error: string | null) => void;
  setRoutes: (routes: PlannedRoute[]) => void;
  selectRoute: (route: PlannedRoute | null) => void;
  clearPlan: () => void;

  // Trip actions
  setActiveTrip: (trip: Trip | null) => void;
  startTrip: (trip: Trip) => void;
  advanceLeg: () => void;
  setActiveLegIdx: (index: number) => void;
  endTrip: () => void;
}

export const useTripStore = create<TripState>()((set, get) => ({
  routes: [],
  selectedRoute: null,
  intent: null,
  origin: null,
  destination: null,
  planWarnings: [],
  planning: false,
  planError: null,

  activeTrip: null,
  activeLegIdx: 0,
  starting: false,

  setPlanning: (planning) => set({ planning, ...(planning ? { planError: null } : {}) }),

  setPlanResult: ({ routes, intent, origin, destination, warnings }) =>
    set({
      routes,
      intent: intent ?? null,
      origin: origin ?? null,
      destination: destination ?? null,
      planWarnings: warnings ?? [],
      planning: false,
      planError: null,
      // A fresh plan invalidates any earlier selection
      selectedRoute: null,
    }),

  setPlanError: (planError) => set({ planError, planning: false }),

  setRoutes: (routes) => set({ routes }),

  selectRoute: (selectedRoute) => set({ selectedRoute }),

  clearPlan: () =>
    set({
      routes: [],
      selectedRoute: null,
      intent: null,
      origin: null,
      destination: null,
      planWarnings: [],
      planError: null,
    }),

  setActiveTrip: (activeTrip) => {
    if (!activeTrip) {
      set({ activeTrip: null, activeLegIdx: 0 });
      return;
    }
    // Derive the current leg from the server's view rather than trusting a
    // local counter — the two diverge after a reroute or an app restart.
    const idx = activeTrip.legs.findIndex((leg) => leg.status === 'in_progress');
    set({ activeTrip, activeLegIdx: idx >= 0 ? idx : 0 });
  },

  startTrip: (trip) => {
    const idx = trip.legs.findIndex((leg) => leg.status === 'in_progress');
    set({ activeTrip: trip, activeLegIdx: idx >= 0 ? idx : 0, starting: false });
  },

  advanceLeg: () => {
    const { activeTrip, activeLegIdx } = get();
    const max = activeTrip ? Math.max(0, activeTrip.legs.length - 1) : 0;
    set({ activeLegIdx: Math.min(activeLegIdx + 1, max) });
  },

  setActiveLegIdx: (activeLegIdx) => set({ activeLegIdx }),

  endTrip: () =>
    set({
      activeTrip: null,
      activeLegIdx: 0,
      selectedRoute: null,
      routes: [],
    }),
}));

/** The leg currently in progress, if any. */
export const selectCurrentLeg = (state: TripState) =>
  state.activeTrip?.legs[state.activeLegIdx] ?? null;

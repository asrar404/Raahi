/**
 * Safety store: live risk state, alerts and SOS status.
 *
 * Not persisted. Risk state is only meaningful relative to where the user is
 * right now, and restoring a stale `sosActive: true` on launch would show a
 * false emergency.
 */

import { create } from 'zustand';

import type { PlannedRoute, RiskZone, SafeRefuge, SafetyAlert } from './types';

/** Alerts kept in memory. Enough for a scrollable feed, bounded so a long
 *  trip cannot grow the list without limit. */
const MAX_ALERTS = 40;

let alertSeq = 0;
function nextAlertId(): string {
  alertSeq += 1;
  return `alert-${Date.now()}-${alertSeq}`;
}

interface SafetyState {
  /** Worst zone risk at the current position, 1-5. 0 means no flagged zone. */
  riskLevel: number;
  /** Blended safety score for the current position, 0-5 (5 = safest). */
  safetyScore: number | null;
  inRiskZone: boolean;
  offRoute: boolean;
  sosActive: boolean;
  sosSending: boolean;
  sosEventId: string | null;

  zones: RiskZone[];
  refuges: SafeRefuge[];
  alerts: SafetyAlert[];

  /** Routes pushed by the server after a reroute, pending user acceptance. */
  pendingReroute: PlannedRoute[] | null;
  rerouteTrigger: string | null;

  setRisk: (level: number, inZone: boolean, safetyScore?: number | null) => void;
  setZones: (zones: RiskZone[]) => void;
  setRefuges: (refuges: SafeRefuge[]) => void;
  setOffRoute: (value: boolean) => void;

  triggerSOS: (eventId?: string | null) => void;
  setSosSending: (value: boolean) => void;
  resetSOS: () => void;

  addAlert: (alert: Omit<SafetyAlert, 'id' | 'at'> & { at?: string }) => void;
  clearAlerts: () => void;

  setPendingReroute: (routes: PlannedRoute[] | null, trigger?: string | null) => void;
  dismissReroute: () => void;

  reset: () => void;
}

export const useSafetyStore = create<SafetyState>()((set) => ({
  riskLevel: 0,
  safetyScore: null,
  inRiskZone: false,
  offRoute: false,
  sosActive: false,
  sosSending: false,
  sosEventId: null,

  zones: [],
  refuges: [],
  alerts: [],

  pendingReroute: null,
  rerouteTrigger: null,

  setRisk: (riskLevel, inRiskZone, safetyScore) =>
    set((state) => ({
      riskLevel,
      inRiskZone,
      safetyScore: safetyScore === undefined ? state.safetyScore : safetyScore,
    })),

  setZones: (zones) => set({ zones }),
  setRefuges: (refuges) => set({ refuges }),
  setOffRoute: (offRoute) => set({ offRoute }),

  triggerSOS: (sosEventId) =>
    set({ sosActive: true, sosSending: false, sosEventId: sosEventId ?? null }),

  setSosSending: (sosSending) => set({ sosSending }),

  resetSOS: () => set({ sosActive: false, sosSending: false, sosEventId: null }),

  addAlert: (alert) =>
    set((state) => ({
      alerts: [
        { ...alert, id: nextAlertId(), at: alert.at ?? new Date().toISOString() },
        ...state.alerts,
      ].slice(0, MAX_ALERTS),
    })),

  clearAlerts: () => set({ alerts: [] }),

  setPendingReroute: (pendingReroute, rerouteTrigger) =>
    set({ pendingReroute, rerouteTrigger: rerouteTrigger ?? null }),

  dismissReroute: () => set({ pendingReroute: null, rerouteTrigger: null }),

  reset: () =>
    set({
      riskLevel: 0,
      safetyScore: null,
      inRiskZone: false,
      offRoute: false,
      sosActive: false,
      sosSending: false,
      sosEventId: null,
      zones: [],
      refuges: [],
      alerts: [],
      pendingReroute: null,
      rerouteTrigger: null,
    }),
}));

/** Alerts that carry coordinates, for the map heatmap. */
export const selectGeoAlerts = (state: SafetyState) =>
  state.alerts.filter(
    (a): a is SafetyAlert & { lat: number; lon: number } =>
      typeof a.lat === 'number' && typeof a.lon === 'number',
  );

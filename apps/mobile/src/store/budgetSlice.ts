/**
 * Budget store.
 *
 * The ceiling and locally-entered logs are persisted so a mid-trip app restart
 * does not lose expenses the user typed in. `syncFromServer` reconciles with
 * `GET /api/v1/budget/{trip_id}`, which is authoritative.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import { budget as budgetConfig } from '../constants/config';
import type { BudgetSummary, ExpenseCategory, ExpenseLog } from './types';

export interface LocalExpense {
  amount: number;
  category: ExpenseCategory;
  desc: string;
  at: string;
  /** False until the gateway has accepted it, so a failed POST can be retried
   *  rather than silently dropping the user's entry. */
  synced: boolean;
}

interface BudgetState {
  tripId: string | null;
  ceiling: number;
  planned: number;
  spent: number;
  logs: LocalExpense[];
  serverLogs: ExpenseLog[];
  syncing: boolean;

  setTrip: (tripId: string | null, ceiling?: number) => void;
  setCeiling: (ceiling: number) => void;
  addExpense: (
    amount: number,
    category: ExpenseCategory,
    desc: string,
    synced?: boolean,
  ) => void;
  markSynced: (at: string) => void;
  syncFromServer: (summary: BudgetSummary) => void;
  setSyncing: (value: boolean) => void;
  reset: () => void;
}

export const useBudgetStore = create<BudgetState>()(
  persist(
    (set) => ({
      tripId: null,
      ceiling: 500,
      planned: 0,
      spent: 0,
      logs: [],
      serverLogs: [],
      syncing: false,

      setTrip: (tripId, ceiling) =>
        set((state) => ({
          tripId,
          ceiling: ceiling ?? state.ceiling,
          // Switching trips must not carry the previous trip's spend over
          ...(tripId !== state.tripId ? { spent: 0, logs: [], serverLogs: [] } : {}),
        })),

      setCeiling: (ceiling) => set({ ceiling }),

      addExpense: (amount, category, desc, synced = false) =>
        set((state) => ({
          spent: Math.round((state.spent + amount) * 100) / 100,
          logs: [
            { amount, category, desc, at: new Date().toISOString(), synced },
            ...state.logs,
          ],
        })),

      markSynced: (at) =>
        set((state) => ({
          logs: state.logs.map((log) => (log.at === at ? { ...log, synced: true } : log)),
        })),

      // The server total supersedes the local one. Local logs are kept only
      // where they have not yet synced, so nothing the user typed is lost.
      syncFromServer: (summary) =>
        set((state) => ({
          tripId: summary.trip_id,
          ceiling: summary.ceiling,
          planned: summary.planned,
          spent: summary.spent,
          serverLogs: summary.logs,
          logs: state.logs.filter((log) => !log.synced),
          syncing: false,
        })),

      setSyncing: (syncing) => set({ syncing }),

      reset: () =>
        set({
          tripId: null,
          planned: 0,
          spent: 0,
          logs: [],
          serverLogs: [],
          syncing: false,
        }),
    }),
    {
      name: 'raahi-budget',
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (state) => ({
        tripId: state.tripId,
        ceiling: state.ceiling,
        spent: state.spent,
        logs: state.logs,
      }),
    },
  ),
);

// ── Selectors ───────────────────────────────────────────────
export const selectRemaining = (state: BudgetState) =>
  Math.round((state.ceiling - state.spent) * 100) / 100;

export const selectPercentUsed = (state: BudgetState) =>
  state.ceiling > 0 ? Math.min(100, (state.spent / state.ceiling) * 100) : 0;

export const selectSeverity = (state: BudgetState): 'ok' | 'warning' | 'critical' => {
  if (state.ceiling <= 0) return 'ok';
  const pct = (state.spent / state.ceiling) * 100;
  if (pct > 100) return 'critical';
  if (pct >= budgetConfig.warnAtPercent) return 'warning';
  return 'ok';
};

/** Expenses entered offline that still need pushing to the gateway. */
export const selectUnsynced = (state: BudgetState) =>
  state.logs.filter((log) => !log.synced);

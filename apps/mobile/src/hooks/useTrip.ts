/**
 * Trip lifecycle hook.
 *
 * Wraps planning, starting, advancing and finishing a trip so screens deal in
 * intent ("start this route") rather than in API calls and store updates.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  ApiError,
  advanceLeg as advanceLegApi,
  cancelTrip as cancelTripApi,
  completeTrip as completeTripApi,
  createTrip,
  fetchActiveTrip,
  fetchBudget,
  planTrip,
  startTrip as startTripApi,
} from '../services/api';
import { getCurrentFix } from '../services/location';
import { useBudgetStore } from '../store/budgetSlice';
import { useSafetyStore } from '../store/safetySlice';
import { useTripStore } from '../store/tripSlice';
import type { PlannedRoute } from '../store/types';

export function useTrip() {
  const activeTrip = useTripStore((s) => s.activeTrip);
  const routes = useTripStore((s) => s.routes);
  const selectedRoute = useTripStore((s) => s.selectedRoute);
  const planning = useTripStore((s) => s.planning);
  const planError = useTripStore((s) => s.planError);
  const [busy, setBusy] = useState(false);

  /**
   * Plan a journey from free text.
   *
   * The device position is attached when available so "from here" resolves;
   * the AI engine's geocoder needs it and cannot infer it.
   */
  const plan = useCallback(async (userInput: string): Promise<boolean> => {
    const trip = useTripStore.getState();
    trip.setPlanning(true);

    try {
      const fix = await getCurrentFix();
      const result = await planTrip(userInput, { lat: fix?.lat, lon: fix?.lon });

      if (result.error) {
        trip.setPlanError(result.error);
        return false;
      }
      if (!result.routes.length) {
        trip.setPlanError(
          result.warnings[0] ?? 'No routes found for that request. Try widening your budget.',
        );
        return false;
      }

      trip.setPlanResult({
        routes: result.routes,
        intent: result.intent,
        origin: result.origin,
        destination: result.destination,
        warnings: result.warnings,
      });

      if (result.intent?.budget_ceiling) {
        useBudgetStore.getState().setCeiling(result.intent.budget_ceiling);
      }
      return true;
    } catch (err) {
      trip.setPlanError(
        err instanceof ApiError ? err.message : 'Could not plan that journey. Try again.',
      );
      return false;
    }
  }, []);

  /** Persist a route as a trip and immediately start it. */
  const begin = useCallback(async (route: PlannedRoute): Promise<string | null> => {
    const trip = useTripStore.getState();
    const { intent, origin, destination } = trip;

    const firstLeg = route.legs[0];
    const lastLeg = route.legs[route.legs.length - 1];
    if (!firstLeg || !lastLeg) return null;

    setBusy(true);
    try {
      const created = await createTrip(route, {
        originName: origin?.name ?? firstLeg.from_name,
        originLat: origin?.lat ?? firstLeg.from_lat,
        originLon: origin?.lon ?? firstLeg.from_lon,
        destName: destination?.name ?? lastLeg.to_name,
        destLat: destination?.lat ?? lastLeg.to_lat,
        destLon: destination?.lon ?? lastLeg.to_lon,
        budgetCeiling: intent?.budget_ceiling ?? useBudgetStore.getState().ceiling,
        rawIntent: intent ? `${intent.source_raw} to ${intent.destination_raw}` : undefined,
        // Stored so safety_watcher can replay it on reroute
        intentJson: (intent ?? {}) as unknown as Record<string, unknown>,
        safetyPriority: intent?.safety_priority ?? true,
        timeDeadline: intent?.time_deadline ?? null,
      });

      const started = await startTripApi(created.id);

      trip.selectRoute(route);
      trip.startTrip(started);
      useBudgetStore.getState().setTrip(started.id, started.budget_ceiling);
      useSafetyStore.getState().reset();

      return started.id;
    } catch (err) {
      trip.setPlanError(
        err instanceof ApiError ? err.message : 'Could not start the trip. Try again.',
      );
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  /** Mark the current leg done and move to the next. */
  const nextLeg = useCallback(async (): Promise<boolean> => {
    const trip = useTripStore.getState();
    if (!trip.activeTrip) return false;

    setBusy(true);
    try {
      const updated = await advanceLegApi(trip.activeTrip.id);
      trip.setActiveTrip(updated);
      if (updated.status === 'completed') {
        useSafetyStore.getState().reset();
      }
      return true;
    } catch {
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const finish = useCallback(async (): Promise<boolean> => {
    const trip = useTripStore.getState();
    if (!trip.activeTrip) return false;

    setBusy(true);
    try {
      await completeTripApi(trip.activeTrip.id);
      trip.endTrip();
      useSafetyStore.getState().reset();
      useBudgetStore.getState().reset();
      return true;
    } catch {
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const abandon = useCallback(async (): Promise<boolean> => {
    const trip = useTripStore.getState();
    if (!trip.activeTrip) return false;

    setBusy(true);
    try {
      await cancelTripApi(trip.activeTrip.id);
      trip.endTrip();
      useSafetyStore.getState().reset();
      useBudgetStore.getState().reset();
      return true;
    } catch {
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  return {
    activeTrip,
    routes,
    selectedRoute,
    planning,
    planError,
    busy,
    plan,
    begin,
    nextLeg,
    finish,
    abandon,
  };
}

/**
 * Restore an in-progress trip on app launch.
 *
 * The trip store is deliberately not persisted, so this is what lets someone
 * force-quit mid-journey and come back to live navigation. The server is the
 * source of truth for what is still running.
 */
export function useRestoreActiveTrip(enabled: boolean): { restoring: boolean } {
  const [restoring, setRestoring] = useState(enabled);

  useEffect(() => {
    if (!enabled) {
      setRestoring(false);
      return;
    }

    let cancelled = false;

    void (async () => {
      try {
        const trip = await fetchActiveTrip();
        if (cancelled || !trip) return;

        useTripStore.getState().setActiveTrip(trip);
        useBudgetStore.getState().setTrip(trip.id, trip.budget_ceiling);

        if (trip.status === 'sos') {
          useSafetyStore.getState().triggerSOS();
        }

        try {
          const budget = await fetchBudget(trip.id);
          if (!cancelled) useBudgetStore.getState().syncFromServer(budget);
        } catch {
          // Budget is non-critical; the trip still restores without it.
        }
      } catch {
        // No active trip, or the server is unreachable. Either way the app
        // opens on the planning screen, which is the right default.
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return { restoring };
}

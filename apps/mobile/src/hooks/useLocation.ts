/**
 * Location tracking hook.
 *
 * `onFix` is stored in a ref so the subscription is created once per trip. If
 * the effect depended on the callback, every parent re-render would tear down
 * and recreate the GPS watcher — which on Android means repeatedly restarting
 * the location service and dropping fixes.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type * as Location from 'expo-location';

import {
  type Fix,
  type PermissionResult,
  getCurrentFix,
  getPermissionStatus,
  isLocationEnabled,
  requestPermissions,
  watchPosition,
} from '../services/location';

interface UseBackgroundLocationResult {
  permission: PermissionResult | null;
  tracking: boolean;
  lastFix: Fix | null;
  error: string | null;
  requestAccess: () => Promise<PermissionResult>;
}

export function useBackgroundLocation(
  tripId: string | null,
  onFix: (fix: Fix) => void,
): UseBackgroundLocationResult {
  const subscription = useRef<Location.LocationSubscription | null>(null);
  const callback = useRef(onFix);
  const [permission, setPermission] = useState<PermissionResult | null>(null);
  const [tracking, setTracking] = useState(false);
  const [lastFix, setLastFix] = useState<Fix | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest callback without retriggering the subscription effect
  useEffect(() => {
    callback.current = onFix;
  }, [onFix]);

  const requestAccess = useCallback(async (): Promise<PermissionResult> => {
    const result = await requestPermissions();
    setPermission(result);
    if (!result.foreground) {
      setError(result.message ?? 'Location permission denied');
    } else {
      setError(null);
    }
    return result;
  }, []);

  useEffect(() => {
    let cancelled = false;

    const stop = () => {
      subscription.current?.remove();
      subscription.current = null;
      setTracking(false);
    };

    if (!tripId) {
      stop();
      return stop;
    }

    void (async () => {
      const servicesOn = await isLocationEnabled();
      if (!servicesOn) {
        setError('Location services are turned off on this device.');
        return;
      }

      let status = await getPermissionStatus();
      if (!status.foreground) {
        status = await requestPermissions();
      }
      if (cancelled) return;

      setPermission(status);

      if (!status.foreground) {
        setError(status.message ?? 'Location permission is required to track your trip.');
        return;
      }
      setError(status.background ? null : (status.message ?? null));

      // Emit an immediate fix so the map centres without waiting for the
      // first interval to elapse.
      const initial = await getCurrentFix();
      if (!cancelled && initial) {
        setLastFix(initial);
        callback.current(initial);
      }

      if (cancelled) return;

      try {
        subscription.current = await watchPosition((fix) => {
          setLastFix(fix);
          callback.current(fix);
        });
        setTracking(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not start location tracking');
      }
    })();

    return () => {
      cancelled = true;
      stop();
    };
  }, [tripId]);

  return { permission, tracking, lastFix, error, requestAccess };
}

/** One-shot position read, for the planning screen's "from here". */
export function useCurrentLocation(): {
  fix: Fix | null;
  loading: boolean;
  refresh: () => Promise<Fix | null>;
} {
  const [fix, setFix] = useState<Fix | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async (): Promise<Fix | null> => {
    setLoading(true);
    try {
      const status = await getPermissionStatus();
      if (!status.foreground) {
        const granted = await requestPermissions();
        if (!granted.foreground) return null;
      }
      const current = await getCurrentFix();
      setFix(current);
      return current;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { fix, loading, refresh };
}

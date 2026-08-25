/**
 * Location permissions and tracking.
 *
 * Foreground and background permissions are requested separately, and in that
 * order, because both Android and iOS refuse a background grant that was not
 * preceded by a foreground one.
 *
 * Background permission is requested but never required. A user who declines
 * it still gets full navigation and safety monitoring while the app is open;
 * they only lose SOS detection with the screen off. Blocking the trip over it
 * would be a worse outcome than degraded coverage.
 */

import * as Location from 'expo-location';

import { telemetry } from '../constants/config';

export interface Fix {
  lat: number;
  lon: number;
  speed: number;
  accuracy: number;
  heading: number;
  altitude: number;
  at: number;
}

export interface PermissionResult {
  foreground: boolean;
  background: boolean;
  canAskAgain: boolean;
  message?: string;
}

export function toFix(location: Location.LocationObject): Fix {
  const { coords, timestamp } = location;
  return {
    lat: coords.latitude,
    lon: coords.longitude,
    // expo-location reports speed in m/s and can return -1 or null when
    // unknown; the backend expects a non-negative km/h value.
    speed: coords.speed && coords.speed > 0 ? coords.speed * 3.6 : 0,
    accuracy: coords.accuracy ?? 0,
    heading: coords.heading && coords.heading >= 0 ? coords.heading : 0,
    altitude: coords.altitude ?? 0,
    at: timestamp,
  };
}

export async function requestPermissions(): Promise<PermissionResult> {
  const foreground = await Location.requestForegroundPermissionsAsync();

  if (foreground.status !== 'granted') {
    return {
      foreground: false,
      background: false,
      canAskAgain: foreground.canAskAgain,
      message:
        'RAAHI needs location access to navigate your trip and warn you about unsafe areas.',
    };
  }

  // Best-effort only — see the module docstring.
  let background = false;
  try {
    const result = await Location.requestBackgroundPermissionsAsync();
    background = result.status === 'granted';
  } catch {
    background = false;
  }

  return {
    foreground: true,
    background,
    canAskAgain: true,
    message: background
      ? undefined
      : 'Background location is off, so SOS detection pauses when your screen is off.',
  };
}

export async function getPermissionStatus(): Promise<PermissionResult> {
  const foreground = await Location.getForegroundPermissionsAsync();
  let background = false;
  try {
    const result = await Location.getBackgroundPermissionsAsync();
    background = result.status === 'granted';
  } catch {
    background = false;
  }
  return {
    foreground: foreground.status === 'granted',
    background,
    canAskAgain: foreground.canAskAgain,
  };
}

export async function getCurrentFix(): Promise<Fix | null> {
  try {
    const location = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    });
    return toFix(location);
  } catch {
    // Fall back to the last cached position — stale beats nothing when the
    // user is trying to plan a trip from where they are.
    try {
      const last = await Location.getLastKnownPositionAsync();
      return last ? toFix(last) : null;
    } catch {
      return null;
    }
  }
}

export async function watchPosition(
  onFix: (fix: Fix) => void,
): Promise<Location.LocationSubscription> {
  return Location.watchPositionAsync(
    {
      accuracy: Location.Accuracy.High,
      timeInterval: telemetry.intervalMs,
      distanceInterval: telemetry.distanceM,
    },
    (location) => onFix(toFix(location)),
  );
}

export async function isLocationEnabled(): Promise<boolean> {
  try {
    return await Location.hasServicesEnabledAsync();
  } catch {
    return false;
  }
}

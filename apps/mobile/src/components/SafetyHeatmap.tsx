/**
 * Safety heatmap and zone overlay for the map.
 *
 * Uses `Heatmap` when available and falls back to translucent circles
 * otherwise. `react-native-maps` only implements Heatmap on the Google
 * provider, so on Apple Maps the import is undefined and rendering it
 * unconditionally crashes the screen.
 *
 * Zone polygons are decoded from the GeoJSON returned by
 * `GET /api/v1/safety/zones` and coloured by risk score.
 */

import React, { useMemo } from 'react';
import { Circle, Heatmap, Polygon } from 'react-native-maps';

import { riskColor } from '../constants/colors';

export interface HeatPoint {
  lat: number;
  lon: number;
  severity?: number;
}

export interface ZoneShape {
  id: string;
  name: string;
  risk_score: number;
  night_risk_score?: number | null;
  time_sensitive?: boolean;
  geojson: string;
}

interface LatLng {
  latitude: number;
  longitude: number;
}

/**
 * Decode a GeoJSON Polygon into map coordinates.
 *
 * GeoJSON is [lon, lat]; react-native-maps wants {latitude, longitude}.
 * Only the exterior ring is used — holes are not meaningful for a risk zone.
 */
function decodePolygon(geojson: string): LatLng[] {
  try {
    const parsed = JSON.parse(geojson) as {
      type?: string;
      coordinates?: number[][][] | number[][][][];
    };
    if (!parsed.coordinates?.length) return [];

    const ring =
      parsed.type === 'MultiPolygon'
        ? (parsed.coordinates as number[][][][])[0]?.[0]
        : (parsed.coordinates as number[][][])[0];

    if (!ring) return [];

    return ring
      .filter((pair): pair is number[] => Array.isArray(pair) && pair.length >= 2)
      .map((pair) => ({ latitude: pair[1] as number, longitude: pair[0] as number }));
  } catch {
    return [];
  }
}

/** Hex to rgba, for translucent polygon fills. */
function withAlpha(hex: string, alpha: number): string {
  const clean = hex.replace('#', '');
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

interface SafetyHeatmapProps {
  points: HeatPoint[];
  zones?: ZoneShape[];
  /** Apply night_risk_score instead of the daytime score. */
  nightMode?: boolean;
  showZones?: boolean;
}

export default function SafetyHeatmap({
  points,
  zones = [],
  nightMode = false,
  showZones = true,
}: SafetyHeatmapProps) {
  const heatPoints = useMemo(
    () =>
      points
        .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon))
        .map((p) => ({
          latitude: p.lat,
          longitude: p.lon,
          weight: Math.max(1, p.severity ?? 1),
        })),
    [points],
  );

  const polygons = useMemo(
    () =>
      zones
        .map((zone) => ({
          zone,
          coords: decodePolygon(zone.geojson),
          risk:
            nightMode && zone.time_sensitive && zone.night_risk_score
              ? zone.night_risk_score
              : zone.risk_score,
        }))
        .filter((entry) => entry.coords.length >= 3),
    [zones, nightMode],
  );

  // Heatmap is Google-provider only; undefined on Apple Maps builds.
  const heatmapAvailable = typeof Heatmap !== 'undefined' && Heatmap !== null;

  return (
    <>
      {showZones
        ? polygons.map(({ zone, coords, risk }) => (
            <Polygon
              key={zone.id}
              coordinates={coords}
              fillColor={withAlpha(riskColor(risk), risk >= 4 ? 0.3 : 0.16)}
              strokeColor={withAlpha(riskColor(risk), 0.75)}
              strokeWidth={1.5}
              tappable={false}
            />
          ))
        : null}

      {heatPoints.length > 0 && heatmapAvailable ? (
        <Heatmap
          points={heatPoints}
          radius={45}
          opacity={0.7}
          gradient={{
            colors: ['#4CAF50', '#FFC107', '#FF5722', '#FF3B30'],
            startPoints: [0.05, 0.35, 0.65, 1.0],
            colorMapSize: 256,
          }}
        />
      ) : (
        // Fallback: one translucent circle per report, sized by severity
        heatPoints.map((point, index) => (
          <Circle
            key={`heat-${index}-${point.latitude}-${point.longitude}`}
            center={{ latitude: point.latitude, longitude: point.longitude }}
            radius={60 + point.weight * 25}
            fillColor={withAlpha(riskColor(point.weight), 0.22)}
            strokeColor={withAlpha(riskColor(point.weight), 0.5)}
            strokeWidth={1}
          />
        ))
      )}
    </>
  );
}

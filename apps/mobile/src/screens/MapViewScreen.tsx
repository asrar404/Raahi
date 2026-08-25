/**
 * Live navigation map.
 *
 * This is the screen that does the actual safety work:
 *
 * 1. `useBackgroundLocation` produces GPS fixes.
 * 2. Each fix goes up the WebSocket, where the gateway persists it, evaluates
 *    risk in PostGIS, and publishes it for safety_watcher.
 * 3. Server events come back down and drive the banners, heatmap and reroute
 *    sheet.
 *
 * Auto-follow is disabled as soon as the user pans the map. Yanking the
 * viewport back to the user's position every 15 seconds makes it impossible to
 * look ahead at the route, which is the main reason to open the map at all.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRoute, type RouteProp } from '@react-navigation/native';
import { Alert, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE, type Region } from 'react-native-maps';

import ExpenseWidget from '../components/ExpenseWidget';
import { ConnectionPill, LegCard, RefugeStrip, RiskBanner } from '../components/MapOverlay';
import RerouteModal from '../components/RerouteModal';
import SOSButton from '../components/SOSButton';
import SafetyHeatmap, { type ZoneShape } from '../components/SafetyHeatmap';
import { colors, modeColors, radius, spacing } from '../constants/colors';
import { map as mapConfig } from '../constants/config';
import type { JourneyStackParamList } from '../constants/routes';
import { useBackgroundLocation } from '../hooks/useLocation';
import { useTrip } from '../hooks/useTrip';
import { useTripWebSocket } from '../hooks/useWebSocket';
import { fetchNearbyAlerts, fetchZones } from '../services/api';
import type { Fix } from '../services/location';
import { selectGeoAlerts, useSafetyStore } from '../store/safetySlice';
import { selectCurrentLeg, useTripStore } from '../store/tripSlice';

/** Dark map style, matched to the app palette. */
const DARK_MAP_STYLE = [
  { elementType: 'geometry', stylers: [{ color: '#1a1a2e' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#8a8a9a' }] },
  { elementType: 'labels.text.stroke', stylers: [{ color: '#0d0d1a' }] },
  { featureType: 'administrative', elementType: 'geometry', stylers: [{ color: '#2a2a3e' }] },
  { featureType: 'poi', elementType: 'labels.text.fill', stylers: [{ color: '#6b6b8a' }] },
  { featureType: 'poi.park', elementType: 'geometry', stylers: [{ color: '#16241e' }] },
  { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#16213e' }] },
  { featureType: 'road.arterial', elementType: 'geometry', stylers: [{ color: '#0f3460' }] },
  { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#1b4571' }] },
  { featureType: 'transit', elementType: 'geometry', stylers: [{ color: '#20203a' }] },
  { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#0b1a2e' }] },
];

/** Zones and reports are refreshed at most this often, ms. */
const CONTEXT_REFRESH_MS = 120_000;

export default function MapViewScreen() {
  const params = useRoute<RouteProp<JourneyStackParamList, 'MapView'>>().params;
  const mapRef = useRef<MapView>(null);

  const activeTrip = useTripStore((s) => s.activeTrip);
  const activeLegIdx = useTripStore((s) => s.activeLegIdx);
  const currentLeg = useTripStore(selectCurrentLeg);
  const selectedRoute = useTripStore((s) => s.selectedRoute);

  const inRiskZone = useSafetyStore((s) => s.inRiskZone);
  const riskLevel = useSafetyStore((s) => s.riskLevel);
  const safetyScore = useSafetyStore((s) => s.safetyScore);
  const offRoute = useSafetyStore((s) => s.offRoute);
  const sosActive = useSafetyStore((s) => s.sosActive);
  const zones = useSafetyStore((s) => s.zones);
  const refuges = useSafetyStore((s) => s.refuges);
  const geoAlerts = useSafetyStore(selectGeoAlerts);

  const { nextLeg, busy } = useTrip();

  const tripId = activeTrip?.id ?? params?.tripId ?? null;
  const { status, sendTelemetry, queued, reconnect } = useTripWebSocket(tripId);

  const [follow, setFollow] = useState(true);
  const [zoneShapes, setZoneShapes] = useState<ZoneShape[]>([]);
  const [reportPoints, setReportPoints] = useState<
    { lat: number; lon: number; severity: number }[]
  >([]);
  const lastContextFetch = useRef(0);

  // ── Telemetry ─────────────────────────────────────────────
  const handleFix = useCallback(
    (fix: Fix) => {
      sendTelemetry({
        lat: fix.lat,
        lon: fix.lon,
        speed: fix.speed,
        accuracy: fix.accuracy,
        heading: fix.heading,
        altitude: fix.altitude,
      });

      if (follow) {
        mapRef.current?.animateToRegion(
          {
            latitude: fix.lat,
            longitude: fix.lon,
            latitudeDelta: mapConfig.followDelta,
            longitudeDelta: mapConfig.followDelta,
          },
          600,
        );
      }

      // Refresh surrounding context occasionally, not per fix — these are
      // spatial queries and every 15 seconds would be wasteful.
      const now = Date.now();
      if (now - lastContextFetch.current > CONTEXT_REFRESH_MS) {
        lastContextFetch.current = now;
        void refreshContext(fix.lat, fix.lon);
      }
    },
    [follow, sendTelemetry],
  );

  const refreshContext = async (lat: number, lon: number) => {
    // Roughly a 3 km box around the user
    const pad = 0.015;
    try {
      const [zoneData, alertData] = await Promise.all([
        fetchZones({
          minLat: lat - pad,
          minLon: lon - pad,
          maxLat: lat + pad,
          maxLon: lon + pad,
        }).catch(() => []),
        fetchNearbyAlerts(lat, lon, 2000).catch(() => ({ alerts: [], count: 0 })),
      ]);

      setZoneShapes(
        zoneData.map((zone) => ({
          id: zone.id,
          name: zone.name,
          risk_score: zone.risk_score,
          night_risk_score: zone.night_risk_score,
          time_sensitive: zone.time_sensitive,
          geojson: zone.geojson,
        })),
      );
      setReportPoints(
        alertData.alerts.map((alert) => ({
          lat: alert.lat,
          lon: alert.lon,
          severity: alert.severity,
        })),
      );
    } catch {
      // Overlays are decoration; navigation must not fail without them.
    }
  };

  const { permission, error: locationError, requestAccess } = useBackgroundLocation(
    tripId,
    handleFix,
  );

  // Surface a hard permission failure once — without location, nothing works.
  useEffect(() => {
    if (locationError && permission && !permission.foreground) {
      Alert.alert('Location needed', locationError, [
        { text: 'Not now', style: 'cancel' },
        { text: 'Allow', onPress: () => void requestAccess() },
      ]);
    }
  }, [locationError, permission, requestAccess]);

  // ── Route geometry ────────────────────────────────────────
  const legs = useMemo(() => {
    if (activeTrip?.legs.length) {
      return activeTrip.legs.map((leg) => ({
        key: leg.id,
        mode: leg.mode,
        from: { latitude: leg.from_lat, longitude: leg.from_lon },
        to: { latitude: leg.to_lat, longitude: leg.to_lon },
        toName: leg.to_name,
        cost: leg.planned_cost,
        coords: leg.route_coords,
      }));
    }
    if (selectedRoute) {
      return selectedRoute.legs.map((leg) => ({
        key: `${leg.leg_order}-${leg.to_name}`,
        mode: leg.mode,
        from: { latitude: leg.from_lat, longitude: leg.from_lon },
        to: { latitude: leg.to_lat, longitude: leg.to_lon },
        toName: leg.to_name,
        cost: leg.planned_cost,
        coords: null,
      }));
    }
    return [];
  }, [activeTrip, selectedRoute]);

  // Fit the whole route once it is known
  useEffect(() => {
    if (!legs.length || !mapRef.current) return;
    const points = legs.flatMap((leg) => [leg.from, leg.to]);
    const timer = setTimeout(() => {
      mapRef.current?.fitToCoordinates(points, {
        edgePadding: { top: 160, right: 60, bottom: 260, left: 60 },
        animated: true,
      });
    }, 400);
    return () => clearTimeout(timer);
  }, [legs]);

  const handleRegionChange = (_region: Region, details?: { isGesture?: boolean }) => {
    // Only a deliberate gesture disables follow — programmatic animations
    // also fire this callback.
    if (details?.isGesture && follow) setFollow(false);
  };

  const handleAdvance = () => {
    const isLast = activeTrip ? activeLegIdx >= activeTrip.legs.length - 1 : false;
    Alert.alert(
      isLast ? 'Arrived?' : 'Finished this step?',
      isLast
        ? 'This will complete your trip.'
        : 'This marks the current step done and starts the next one.',
      [
        { text: 'Not yet', style: 'cancel' },
        { text: 'Yes', onPress: () => void nextLeg() },
      ],
    );
  };

  const zoneName = zones[0]?.zone_name;

  return (
    <View style={styles.container}>
      <MapView
        ref={mapRef}
        provider={PROVIDER_GOOGLE}
        style={StyleSheet.absoluteFillObject}
        initialRegion={mapConfig.initialRegion}
        customMapStyle={DARK_MAP_STYLE}
        showsUserLocation
        showsMyLocationButton={false}
        showsCompass={false}
        toolbarEnabled={false}
        onRegionChangeComplete={handleRegionChange}
      >
        <SafetyHeatmap points={reportPoints} zones={zoneShapes} />

        {legs.map((leg, index) => (
          <Polyline
            key={leg.key}
            coordinates={
              leg.coords?.length
                ? leg.coords
                    .filter((pair): pair is number[] => pair.length >= 2)
                    .map((pair) => ({
                      latitude: pair[0] as number,
                      longitude: pair[1] as number,
                    }))
                : [leg.from, leg.to]
            }
            strokeColor={
              inRiskZone
                ? colors.danger
                : index === activeLegIdx
                  ? (modeColors[leg.mode] ?? colors.accent)
                  : `${modeColors[leg.mode] ?? colors.accent}66`
            }
            strokeWidth={index === activeLegIdx ? 6 : 4}
            lineDashPattern={leg.mode === 'walk' ? [8, 6] : undefined}
          />
        ))}

        {legs.map((leg, index) => (
          <Marker
            key={`m-${leg.key}`}
            coordinate={leg.to}
            title={leg.toName}
            description={`${leg.mode}${leg.cost > 0 ? ` · ₹${leg.cost.toFixed(0)}` : ''}`}
            pinColor={index === activeLegIdx ? colors.accent : colors.muted}
          />
        ))}

        {geoAlerts.map((alert) => (
          <Marker
            key={alert.id}
            coordinate={{ latitude: alert.lat, longitude: alert.lon }}
            title={alert.type.toUpperCase()}
            description={alert.message}
            pinColor={colors.warning}
          />
        ))}
      </MapView>

      {/* Top overlays */}
      <View style={styles.topStack} pointerEvents="box-none">
        <ExpenseWidget />
      </View>

      <View style={styles.bannerStack} pointerEvents="box-none">
        <RiskBanner
          inRiskZone={inRiskZone}
          riskLevel={riskLevel}
          safetyScore={safetyScore}
          zoneName={zoneName}
          offRoute={offRoute}
          sosActive={sosActive}
        />
        {sosActive || inRiskZone ? <RefugeStrip refuges={refuges} /> : null}
        <ConnectionPill status={status} queued={queued} onRetry={reconnect} />
      </View>

      {/* Bottom overlays */}
      <View style={styles.bottomStack} pointerEvents="box-none">
        {!follow ? (
          <TouchableOpacity
            style={styles.recenterBtn}
            onPress={() => setFollow(true)}
            accessibilityRole="button"
            accessibilityLabel="Re-centre on my location"
          >
            <Text style={styles.recenterText}>Re-centre</Text>
          </TouchableOpacity>
        ) : null}

        <LegCard
          leg={currentLeg}
          legIndex={activeLegIdx}
          legCount={activeTrip?.legs.length ?? legs.length}
          onAdvance={handleAdvance}
          busy={busy}
        />
      </View>

      <SOSButton />
      <RerouteModal />

      {!tripId ? (
        <View style={styles.noTrip}>
          <Text style={styles.noTripTitle}>No active trip</Text>
          <Text style={styles.noTripBody}>
            Plan a journey from the Journey tab to start live navigation.
          </Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  topStack: { position: 'absolute', top: 0, left: 0, right: 0 },
  bannerStack: {
    position: 'absolute',
    top: 128,
    left: 0,
    right: 0,
    gap: spacing.sm,
  },
  bottomStack: {
    position: 'absolute',
    bottom: spacing.lg,
    left: spacing.md,
    right: 108,
    gap: spacing.sm,
  },
  recenterBtn: {
    alignSelf: 'flex-start',
    backgroundColor: `${colors.surface}F2`,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
  },
  recenterText: { color: colors.accent, fontSize: 12, fontWeight: '700' },
  noTrip: {
    position: 'absolute',
    top: '42%',
    left: spacing.lg,
    right: spacing.lg,
    backgroundColor: `${colors.surface}F7`,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    alignItems: 'center',
  },
  noTripTitle: { color: colors.text, fontSize: 17, fontWeight: '800', marginBottom: 6 },
  noTripBody: {
    color: colors.textDim,
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 19,
  },
});

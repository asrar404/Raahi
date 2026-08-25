# RAAHI — Phase 4: Mobile App (React Native / Expo)

## File: `apps/mobile/src/store/index.ts`

```typescript
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ── AUTH STORE ──────────────────────────────────────────────
interface AuthState {
  user: any | null;
  token: string | null;
  setUser: (u: any, t: string) => void;
  logout: () => void;
}
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null, token: null,
      setUser: (user, token) => set({ user, token }),
      logout: () => set({ user: null, token: null }),
    }),
    { name: 'raahi-auth', storage: createJSONStorage(() => AsyncStorage) }
  )
);

// ── TRIP STORE ──────────────────────────────────────────────
export interface RouteLeg {
  leg_order: number; mode: string;
  from_name: string; from_lat: number; from_lon: number;
  to_name: string;   to_lat: number;   to_lon: number;
  distance_km: number; planned_cost: number;
  duration_mins: number; provider?: string; safety_score: number;
}
export interface PlannedRoute {
  route_id: string; legs: RouteLeg[];
  total_cost: number; total_duration: number;
  utility_score: number; safety_rating: number; summary: string;
}
interface TripState {
  routes:        PlannedRoute[];
  selectedRoute: PlannedRoute | null;
  activeTrip:    any | null;
  activeLegIdx:  number;
  setRoutes:     (r: PlannedRoute[]) => void;
  selectRoute:   (r: PlannedRoute) => void;
  startTrip:     (trip: any) => void;
  advanceLeg:    () => void;
  endTrip:       () => void;
}
export const useTripStore = create<TripState>()((set, get) => ({
  routes: [], selectedRoute: null, activeTrip: null, activeLegIdx: 0,
  setRoutes:   (routes) => set({ routes }),
  selectRoute: (r)      => set({ selectedRoute: r }),
  startTrip:   (trip)   => set({ activeTrip: trip, activeLegIdx: 0 }),
  advanceLeg:  ()       => set({ activeLegIdx: get().activeLegIdx + 1 }),
  endTrip:     ()       => set({ activeTrip: null, activeLegIdx: 0, selectedRoute: null }),
}));

// ── SAFETY STORE ────────────────────────────────────────────
interface SafetyState {
  riskLevel:   number;   // 1-5
  inRiskZone:  boolean;
  offRoute:    boolean;
  sosActive:   boolean;
  alerts:      any[];
  setRisk:     (lvl: number, inZone: boolean) => void;
  setOffRoute: (v: boolean) => void;
  triggerSOS:  () => void;
  resetSOS:    () => void;
  addAlert:    (a: any) => void;
}
export const useSafetyStore = create<SafetyState>()((set) => ({
  riskLevel: 1, inRiskZone: false, offRoute: false, sosActive: false, alerts: [],
  setRisk:     (lvl, inZone) => set({ riskLevel: lvl, inRiskZone: inZone }),
  setOffRoute: (v)           => set({ offRoute: v }),
  triggerSOS:  ()            => set({ sosActive: true }),
  resetSOS:    ()            => set({ sosActive: false }),
  addAlert:    (a)           => set((s) => ({ alerts: [a, ...s.alerts].slice(0, 20) })),
}));

// ── BUDGET STORE ────────────────────────────────────────────
interface BudgetState {
  ceiling:  number;
  spent:    number;
  logs:     { amount: number; category: string; desc: string; at: string }[];
  setCeiling: (v: number) => void;
  addExpense: (amount: number, category: string, desc: string) => void;
}
export const useBudgetStore = create<BudgetState>()(
  persist(
    (set) => ({
      ceiling: 500, spent: 0, logs: [],
      setCeiling:  (ceiling) => set({ ceiling }),
      addExpense:  (amount, category, desc) =>
        set((s) => ({
          spent: s.spent + amount,
          logs: [{ amount, category, desc, at: new Date().toISOString() }, ...s.logs],
        })),
    }),
    { name: 'raahi-budget', storage: createJSONStorage(() => AsyncStorage) }
  )
);
```

---

## File: `apps/mobile/src/hooks/useWebSocket.ts`

```typescript
import { useEffect, useRef, useCallback } from 'react';
import { useSafetyStore } from '../store';
import { useTripStore } from '../store';
import { API_WS_URL } from '../constants/config';

export function useTripWebSocket(tripId: string | null) {
  const ws = useRef<WebSocket | null>(null);
  const { setRisk, setOffRoute, addAlert, triggerSOS } = useSafetyStore();
  const { setRoutes } = useTripStore();

  useEffect(() => {
    if (!tripId) return;
    const socket = new WebSocket(`${API_WS_URL}/ws/trip/${tripId}`);
    ws.current = socket;

    socket.onopen = () => console.log('[WS] Connected to trip', tripId);

    socket.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      switch (msg.event) {
        case 'SOS_ALERT':
          triggerSOS();
          addAlert(msg.data);
          break;
        case 'REROUTE':
          setRoutes(msg.data.new_routes);
          addAlert({ type: 'reroute', message: 'New route available!' });
          break;
        case 'RISK_UPDATE':
          setRisk(msg.data.risk_level, msg.data.in_risk_zone);
          break;
        case 'OFF_ROUTE':
          setOffRoute(true);
          addAlert({ type: 'off_route', message: 'You are off your planned route.' });
          break;
      }
    };

    socket.onerror  = (e) => console.error('[WS] Error', e);
    socket.onclose  = ()  => console.log('[WS] Disconnected');

    return () => socket.close();
  }, [tripId]);

  const sendTelemetry = useCallback((payload: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'TELEMETRY', ...payload }));
    }
  }, []);

  return { sendTelemetry };
}
```

---

## File: `apps/mobile/src/hooks/useLocation.ts`

```typescript
import { useEffect, useRef } from 'react';
import * as Location from 'expo-location';
import { useTripStore } from '../store';

export function useBackgroundLocation(
  tripId: string | null,
  onLocation: (lat: number, lon: number, speed: number, accuracy: number) => void
) {
  const sub = useRef<Location.LocationSubscription | null>(null);

  useEffect(() => {
    if (!tripId) { sub.current?.remove(); return; }

    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') return;

      await Location.requestBackgroundPermissionsAsync();

      sub.current = await Location.watchPositionAsync(
        {
          accuracy: Location.Accuracy.High,
          timeInterval: 15000,   // every 15s
          distanceInterval: 20,  // or every 20m
        },
        (loc) => {
          onLocation(
            loc.coords.latitude,
            loc.coords.longitude,
            loc.coords.speed ?? 0,
            loc.coords.accuracy ?? 0
          );
        }
      );
    })();

    return () => sub.current?.remove();
  }, [tripId]);
}
```

---

## File: `apps/mobile/src/screens/IntentInputScreen.tsx`

```tsx
import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ScrollView, ActivityIndicator
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { api } from '../services/api';
import { useTripStore, useBudgetStore } from '../store';
import { colors } from '../constants/colors';

export default function IntentInputScreen() {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const nav = useNavigation<any>();
  const { setRoutes } = useTripStore();
  const { setCeiling } = useBudgetStore();

  const handlePlan = async () => {
    if (!input.trim()) return;
    setLoading(true); setError('');
    try {
      const res = await api.post('/plan', { user_input: input });
      setRoutes(res.data.routes);
      if (res.data.intent?.budget_ceiling) setCeiling(res.data.intent.budget_ceiling);
      nav.navigate('RouteSelection');
    } catch (e: any) {
      setError(e?.message ?? 'Failed to plan route. Try again.');
    } finally { setLoading(false); }
  };

  return (
    <ScrollView style={s.container} contentContainerStyle={s.content}>
      <Text style={s.title}>Where to?</Text>
      <Text style={s.subtitle}>Tell RAAHI your journey in plain language</Text>
      <TextInput
        style={s.input}
        multiline numberOfLines={4}
        placeholder="e.g. I need to go from Paharganj to Saket under ₹150 by metro, I'm travelling alone at night"
        placeholderTextColor={colors.muted}
        value={input} onChangeText={setInput}
      />
      <View style={s.examplesRow}>
        {["Paharganj → Saket under ₹150", "CST → Bandra, budget ₹80"].map((ex) => (
          <TouchableOpacity key={ex} style={s.chip} onPress={() => setInput(ex)}>
            <Text style={s.chipText}>{ex}</Text>
          </TouchableOpacity>
        ))}
      </View>
      {error ? <Text style={s.error}>{error}</Text> : null}
      <TouchableOpacity style={s.btn} onPress={handlePlan} disabled={loading}>
        {loading ? <ActivityIndicator color="#fff" /> : <Text style={s.btnText}>Plan My Journey →</Text>}
      </TouchableOpacity>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content:   { padding: 24, paddingTop: 60 },
  title:     { fontSize: 32, fontWeight: '800', color: colors.text, marginBottom: 8 },
  subtitle:  { fontSize: 15, color: colors.muted, marginBottom: 24 },
  input:     {
    backgroundColor: colors.surface, borderRadius: 16,
    padding: 16, fontSize: 15, color: colors.text,
    minHeight: 110, textAlignVertical: 'top', marginBottom: 16,
    borderWidth: 1, borderColor: colors.border,
  },
  examplesRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 24 },
  chip:        { backgroundColor: colors.accent + '22', borderRadius: 20,
                 paddingHorizontal: 12, paddingVertical: 6 },
  chipText:    { color: colors.accent, fontSize: 12 },
  error:       { color: '#FF6B6B', marginBottom: 12, fontSize: 13 },
  btn:         { backgroundColor: colors.accent, borderRadius: 16,
                 paddingVertical: 16, alignItems: 'center' },
  btnText:     { color: '#fff', fontWeight: '700', fontSize: 16 },
});
```

---

## File: `apps/mobile/src/screens/MapViewScreen.tsx`

```tsx
import React, { useRef, useEffect } from 'react';
import { View, StyleSheet, Alert } from 'react-native';
import MapView, { Marker, Polyline, Heatmap, PROVIDER_GOOGLE } from 'react-native-maps';
import { useTripStore, useSafetyStore } from '../store';
import { useBackgroundLocation } from '../hooks/useLocation';
import { useTripWebSocket } from '../hooks/useWebSocket';
import SOSButton from '../components/SOSButton';
import RerouteModal from '../components/RerouteModal';
import ExpenseWidget from '../components/ExpenseWidget';
import { useAuthStore } from '../store';

export default function MapViewScreen() {
  const { activeTrip, selectedRoute } = useTripStore();
  const { inRiskZone, offRoute, alerts } = useSafetyStore();
  const { user } = useAuthStore();
  const mapRef = useRef<MapView>(null);

  const { sendTelemetry } = useTripWebSocket(activeTrip?.id ?? null);

  useBackgroundLocation(activeTrip?.id ?? null, (lat, lon, speed, accuracy) => {
    sendTelemetry({ lat, lon, speed, accuracy, user_id: user?.id });
    mapRef.current?.animateToRegion({ latitude: lat, longitude: lon,
      latitudeDelta: 0.01, longitudeDelta: 0.01 }, 500);
  });

  const routeCoords = selectedRoute?.legs.flatMap((l) => [
    { latitude: l.from_lat, longitude: l.from_lon },
    { latitude: l.to_lat,   longitude: l.to_lon   },
  ]) ?? [];

  return (
    <View style={s.container}>
      <MapView
        ref={mapRef} provider={PROVIDER_GOOGLE}
        style={StyleSheet.absoluteFillObject}
        initialRegion={{ latitude: 28.6139, longitude: 77.2090,
                         latitudeDelta: 0.08, longitudeDelta: 0.08 }}
        showsUserLocation showsMyLocationButton={false}
        customMapStyle={darkMapStyle}
      >
        {routeCoords.length > 0 && (
          <Polyline coordinates={routeCoords}
            strokeColor={inRiskZone ? '#FF4444' : '#6C63FF'}
            strokeWidth={4} lineDashPattern={[1]} />
        )}
        {selectedRoute?.legs.map((leg, i) => (
          <Marker key={i}
            coordinate={{ latitude: leg.to_lat, longitude: leg.to_lon }}
            title={leg.to_name} description={`${leg.mode} · ₹${leg.planned_cost}`}
          />
        ))}
        {alerts.length > 0 && (
          <Heatmap points={alerts.filter(a => a.lat).map(a => ({
            latitude: a.lat, longitude: a.lon, weight: a.severity ?? 1
          }))} radius={40} opacity={0.7} />
        )}
      </MapView>
      <SOSButton />
      <ExpenseWidget />
      {offRoute && <RerouteModal />}
    </View>
  );
}
const s = StyleSheet.create({ container: { flex: 1 } });
const darkMapStyle = [
  { elementType: 'geometry', stylers: [{ color: '#1a1a2e' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#8a8a9a' }] },
  { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#16213e' }] },
  { featureType: 'road.arterial', stylers: [{ color: '#0f3460' }] },
];
```

---

## File: `apps/mobile/src/components/SOSButton.tsx`

```tsx
import React, { useState } from 'react';
import { TouchableOpacity, Text, StyleSheet, Alert, Animated } from 'react-native';
import { useSafetyStore, useAuthStore, useTripStore } from '../store';
import { api } from '../services/api';

export default function SOSButton() {
  const { sosActive, triggerSOS } = useSafetyStore();
  const { user }                  = useAuthStore();
  const { activeTrip }            = useTripStore();
  const [pressing, setPressing]   = useState(false);
  const scale = new Animated.Value(1);

  const handlePress = () => {
    Alert.alert('🚨 SEND SOS?',
      'This will alert your emergency contacts and share your live location.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'SEND SOS', style: 'destructive', onPress: sendSOS },
      ]
    );
  };

  const sendSOS = async () => {
    triggerSOS();
    try {
      await api.post('/api/v1/safety/sos', {
        trip_id: activeTrip?.id, user_id: user?.id
      });
    } catch (e) { console.error('SOS API failed', e); }
  };

  return (
    <TouchableOpacity
      style={[s.btn, sosActive && s.btnActive]}
      onPress={handlePress} activeOpacity={0.8}
    >
      <Text style={s.text}>{sosActive ? '🚨 SOS ACTIVE' : 'SOS'}</Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  btn: {
    position: 'absolute', bottom: 40, right: 20,
    width: 70, height: 70, borderRadius: 35,
    backgroundColor: '#FF3B30', alignItems: 'center',
    justifyContent: 'center', elevation: 8,
    shadowColor: '#FF3B30', shadowRadius: 12, shadowOpacity: 0.6,
  },
  btnActive: { backgroundColor: '#8B0000', width: 100, borderRadius: 20 },
  text:      { color: '#fff', fontWeight: '900', fontSize: 14 },
});
```

---

## File: `apps/mobile/src/constants/colors.ts`

```typescript
export const colors = {
  bg:      '#0D0D1A',
  surface: '#1A1A2E',
  border:  '#2A2A3E',
  text:    '#EAEAFF',
  muted:   '#6B6B8A',
  accent:  '#6C63FF',
  success: '#4CAF50',
  warning: '#FF9800',
  danger:  '#FF3B30',
};
```

---

## File: `apps/mobile/src/constants/config.ts`

```typescript
export const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
export const API_WS_URL   = process.env.EXPO_PUBLIC_WS_URL  ?? 'ws://localhost:8000';
export const AI_ENGINE_URL = process.env.EXPO_PUBLIC_AI_URL ?? 'http://localhost:8001';
```

---

## File: `apps/mobile/src/services/api.ts`

```typescript
import axios from 'axios';
import { API_BASE_URL, AI_ENGINE_URL } from '../constants/config';
import { useAuthStore } from '../store';

export const api = axios.create({ baseURL: API_BASE_URL, timeout: 30000 });
export const aiApi = axios.create({ baseURL: AI_ENGINE_URL, timeout: 60000 });

// Attach JWT token
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Plan trip via AI engine
export const planTrip = (userInput: string) =>
  aiApi.post('/plan', { user_input: userInput });

// Log expense
export const logExpense = (tripId: string, amount: number, category: string, desc: string) =>
  api.post('/api/v1/budget/log', { trip_id: tripId, amount, category, description: desc });
```

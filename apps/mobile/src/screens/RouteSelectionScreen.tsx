/**
 * Route selection.
 *
 * Routes arrive pre-ranked by the AI engine's utility function, so the order is
 * preserved rather than re-sorted here — re-sorting client-side would silently
 * disagree with the server's reasoning.
 *
 * Planner warnings are shown above the list, and the interpreted origin and
 * destination are displayed prominently. Geocoding is fuzzy, and a user who
 * typed "MG Road" needs to see which MG Road it resolved to before they commit
 * to a journey.
 */

import React from 'react';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';

import RouteCard from '../components/RouteCard';
import { colors, radius, spacing } from '../constants/colors';
import type { JourneyStackParamList } from '../constants/routes';
import { useTrip } from '../hooks/useTrip';
import { useTripStore } from '../store/tripSlice';
import type { PlannedRoute } from '../store/types';

export default function RouteSelectionScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<JourneyStackParamList>>();
  const { begin, busy } = useTrip();

  const routes = useTripStore((s) => s.routes);
  const selectedRoute = useTripStore((s) => s.selectedRoute);
  const intent = useTripStore((s) => s.intent);
  const origin = useTripStore((s) => s.origin);
  const destination = useTripStore((s) => s.destination);
  const warnings = useTripStore((s) => s.planWarnings);

  const budgetCeiling = intent?.budget_ceiling;

  const handleSelect = (route: PlannedRoute) => {
    const overBudget = budgetCeiling !== undefined && route.total_cost > budgetCeiling;

    const start = async () => {
      const tripId = await begin(route);
      if (tripId) {
        navigation.navigate('MapView', { tripId });
      } else {
        Alert.alert('Could not start', 'The trip could not be created. Please try again.');
      }
    };

    if (overBudget) {
      Alert.alert(
        'Over your budget',
        `This route costs ₹${route.total_cost.toFixed(0)}, which is ₹${(
          route.total_cost - budgetCeiling
        ).toFixed(0)} more than your ₹${budgetCeiling.toFixed(0)} budget.`,
        [
          { text: 'Pick another', style: 'cancel' },
          { text: 'Start anyway', onPress: () => void start() },
        ],
      );
      return;
    }

    if (route.safety_rating < 2.5) {
      Alert.alert(
        'Low safety rating',
        `This route scores ${route.safety_rating.toFixed(1)}/5 on safety.${
          intent?.night_travel ? ' You are travelling at night.' : ''
        }\n\nConsider a safer option if you can.`,
        [
          { text: 'Pick another', style: 'cancel' },
          { text: 'Start anyway', style: 'destructive', onPress: () => void start() },
        ],
      );
      return;
    }

    void start();
  };

  if (!routes.length) {
    return (
      <View style={styles.emptyContainer}>
        <Text style={styles.emptyTitle}>No routes to show</Text>
        <Text style={styles.emptyBody}>
          Go back and describe your journey again, or try widening your budget.
        </Text>
        <TouchableOpacity style={styles.emptyBtn} onPress={() => navigation.goBack()}>
          <Text style={styles.emptyBtnText}>Back to planning</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>
        {routes.length} route{routes.length > 1 ? 's' : ''} found
      </Text>

      {/* What the parser understood */}
      <View style={styles.intentCard}>
        <View style={styles.intentRow}>
          <Text style={styles.intentLabel}>FROM</Text>
          <Text style={styles.intentValue} numberOfLines={1}>
            {origin?.name ?? intent?.source_raw ?? '—'}
          </Text>
        </View>
        <View style={styles.intentRow}>
          <Text style={styles.intentLabel}>TO</Text>
          <Text style={styles.intentValue} numberOfLines={1}>
            {destination?.name ?? intent?.destination_raw ?? '—'}
          </Text>
        </View>
        <View style={styles.intentRow}>
          <Text style={styles.intentLabel}>BUDGET</Text>
          <Text style={styles.intentValue}>
            ₹{budgetCeiling?.toFixed(0) ?? '—'}
            {intent?.night_travel ? '  ·  night travel' : ''}
            {intent?.safety_priority ? '  ·  safety first' : ''}
          </Text>
        </View>
      </View>

      {/* Non-fatal planner notes */}
      {warnings.length ? (
        <View style={styles.warnBox}>
          {warnings.map((warning) => (
            <Text key={warning} style={styles.warnText}>
              • {warning}
            </Text>
          ))}
        </View>
      ) : null}

      <Text style={styles.rankNote}>
        Ranked on cost, time and safety combined. The first is the best overall match.
      </Text>

      {routes.map((route, index) => (
        <RouteCard
          key={route.route_id}
          route={route}
          recommended={index === 0}
          selected={selectedRoute?.route_id === route.route_id}
          budgetCeiling={budgetCeiling}
          onSelect={handleSelect}
        />
      ))}

      {busy ? (
        <View style={styles.busyOverlay}>
          <ActivityIndicator color={colors.accent} size="large" />
          <Text style={styles.busyText}>Starting your trip…</Text>
        </View>
      ) : null}

      <TouchableOpacity style={styles.backBtn} onPress={() => navigation.goBack()}>
        <Text style={styles.backText}>Change my request</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingTop: spacing.lg, paddingBottom: spacing.xl },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: '900',
    marginBottom: spacing.md,
    paddingHorizontal: spacing.xs,
  },
  intentCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
    gap: 8,
  },
  intentRow: { flexDirection: 'row', alignItems: 'center' },
  intentLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: '800',
    width: 62,
    letterSpacing: 0.6,
  },
  intentValue: { color: colors.text, fontSize: 13, flex: 1, fontWeight: '600' },
  warnBox: {
    backgroundColor: `${colors.info}14`,
    borderLeftWidth: 3,
    borderLeftColor: colors.info,
    borderRadius: radius.sm,
    padding: spacing.sm + 2,
    marginBottom: spacing.md,
  },
  warnText: { color: colors.textDim, fontSize: 12, lineHeight: 18 },
  rankNote: {
    color: colors.muted,
    fontSize: 11,
    marginBottom: spacing.md,
    paddingHorizontal: spacing.xs,
    lineHeight: 16,
  },
  busyOverlay: { alignItems: 'center', paddingVertical: spacing.lg, gap: spacing.sm },
  busyText: { color: colors.textDim, fontSize: 13 },
  backBtn: { alignItems: 'center', paddingVertical: spacing.md },
  backText: { color: colors.accent, fontSize: 13, fontWeight: '600' },
  emptyContainer: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
  },
  emptyTitle: { color: colors.text, fontSize: 20, fontWeight: '800', marginBottom: spacing.sm },
  emptyBody: {
    color: colors.textDim,
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: spacing.lg,
  },
  emptyBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: 13,
  },
  emptyBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
});

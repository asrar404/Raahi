/**
 * Reroute bottom sheet.
 *
 * Shown when the server pushes alternative routes mid-journey. It asks rather
 * than acting: silently replacing someone's route while they are walking is
 * disorienting, and the reason for the change (off route, stalled, risk zone)
 * is information they need.
 *
 * `Modal` is used rather than an absolutely-positioned view so it reliably sits
 * above the map on both platforms — MapView renders as a native view and wins
 * z-order fights against sibling RN views on Android.
 */

import React, { useState } from 'react';
import {
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';

import { colors, modeColors, radius, safetyColor, spacing } from '../constants/colors';
import { useSafetyStore } from '../store/safetySlice';
import { useTripStore } from '../store/tripSlice';
import type { PlannedRoute } from '../store/types';

const FALLBACK_COPY = {
  title: 'Alternative routes',
  body: 'Here are other ways to reach your destination from here.',
};

const TRIGGER_COPY: Record<string, { title: string; body: string }> = {
  off_route: {
    title: 'You have gone off route',
    body: 'You are more than 300 m from your planned path. Here are alternatives from where you are now.',
  },
  delay: {
    title: 'You have not moved in a while',
    body: 'Your expected service may not be running. These options are available on demand.',
  },
  risk_zone: {
    title: 'Leave this area',
    body: 'You are in an area flagged as high risk. These routes avoid walking and get you out fastest.',
  },
  budget: {
    title: 'Budget running low',
    body: 'These options cost less for the rest of your journey.',
  },
  manual: FALLBACK_COPY,
};

function ModeStrip({ route }: { route: PlannedRoute }) {
  return (
    <View style={styles.modeStrip}>
      {route.legs.map((leg, index) => (
        <React.Fragment key={`${leg.leg_order}-${leg.mode}`}>
          <Text style={[styles.modeText, { color: modeColors[leg.mode] ?? colors.accent }]}>
            {leg.mode}
          </Text>
          {index < route.legs.length - 1 ? (
            <Text style={styles.modeSep}>›</Text>
          ) : null}
        </React.Fragment>
      ))}
    </View>
  );
}

export default function RerouteModal() {
  const pendingReroute = useSafetyStore((s) => s.pendingReroute);
  const rerouteTrigger = useSafetyStore((s) => s.rerouteTrigger);
  const dismissReroute = useSafetyStore((s) => s.dismissReroute);
  const setOffRoute = useSafetyStore((s) => s.setOffRoute);
  const selectRoute = useTripStore((s) => s.selectRoute);

  const [chosen, setChosen] = useState<string | null>(null);

  const visible = Boolean(pendingReroute?.length);
  const copy = TRIGGER_COPY[rerouteTrigger ?? 'manual'] ?? FALLBACK_COPY;
  const urgent = rerouteTrigger === 'risk_zone';

  const accept = (route: PlannedRoute) => {
    setChosen(route.route_id);
    // Swap the displayed route. The trip's persisted legs are not rewritten
    // here — that needs a server-side plan update, which is intentionally out
    // of scope for an in-journey suggestion.
    selectRoute(route);
    setOffRoute(false);
    dismissReroute();
    setChosen(null);
  };

  if (!visible || !pendingReroute) return null;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={dismissReroute}
    >
      <View style={styles.backdrop}>
        <View style={[styles.sheet, urgent && styles.sheetUrgent]}>
          <View style={styles.handle} />

          <Text style={[styles.title, urgent && { color: colors.danger }]}>
            {copy.title}
          </Text>
          <Text style={styles.body}>{copy.body}</Text>

          <ScrollView style={styles.list} contentContainerStyle={styles.listContent}>
            {pendingReroute.map((route, index) => (
              <View
                key={route.route_id}
                style={[styles.option, index === 0 && styles.optionFirst]}
              >
                <View style={styles.optionHeader}>
                  <ModeStrip route={route} />
                  <Text style={styles.optionCost}>₹{route.total_cost.toFixed(0)}</Text>
                </View>

                <View style={styles.optionMetaRow}>
                  <Text style={styles.optionMeta}>{route.total_duration} min</Text>
                  <Text style={styles.optionDot}>·</Text>
                  <Text
                    style={[styles.optionMeta, { color: safetyColor(route.safety_rating) }]}
                  >
                    safety {route.safety_rating.toFixed(1)}/5
                  </Text>
                </View>

                {route.summary ? (
                  <Text style={styles.optionSummary} numberOfLines={2}>
                    {route.summary}
                  </Text>
                ) : null}

                {route.warnings?.length ? (
                  <Text style={styles.optionWarn}>• {route.warnings[0]}</Text>
                ) : null}

                <TouchableOpacity
                  style={[styles.acceptBtn, urgent && styles.acceptBtnUrgent]}
                  onPress={() => accept(route)}
                  disabled={chosen !== null}
                  accessibilityRole="button"
                >
                  <Text style={styles.acceptText}>
                    {index === 0 ? 'Take this route' : 'Use this instead'}
                  </Text>
                </TouchableOpacity>
              </View>
            ))}
          </ScrollView>

          <TouchableOpacity
            style={styles.dismissBtn}
            onPress={dismissReroute}
            accessibilityRole="button"
          >
            <Text style={styles.dismissText}>
              {urgent ? 'I am safe, keep my route' : 'Keep my current route'}
            </Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radius.lg + 6,
    borderTopRightRadius: radius.lg + 6,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.lg,
    maxHeight: '82%',
    borderTopWidth: 2,
    borderTopColor: colors.accent,
  },
  sheetUrgent: { borderTopColor: colors.danger },
  handle: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.border,
    marginBottom: spacing.md,
  },
  title: { color: colors.text, fontSize: 20, fontWeight: '800', marginBottom: 6 },
  body: { color: colors.textDim, fontSize: 13, lineHeight: 19, marginBottom: spacing.md },
  list: { flexGrow: 0 },
  listContent: { paddingBottom: spacing.sm },
  option: {
    backgroundColor: colors.bg,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm + 2,
    marginBottom: spacing.sm,
  },
  optionFirst: { borderColor: colors.accent },
  optionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  modeStrip: { flexDirection: 'row', alignItems: 'center', flex: 1, flexWrap: 'wrap' },
  modeText: { fontSize: 12, fontWeight: '700', textTransform: 'capitalize' },
  modeSep: { color: colors.muted, marginHorizontal: 4 },
  optionCost: { color: colors.text, fontSize: 17, fontWeight: '800' },
  optionMetaRow: { flexDirection: 'row', alignItems: 'center', marginTop: 4 },
  optionMeta: { color: colors.muted, fontSize: 12 },
  optionDot: { color: colors.muted, marginHorizontal: 6 },
  optionSummary: { color: colors.textDim, fontSize: 12, marginTop: 6, lineHeight: 17 },
  optionWarn: { color: colors.warning, fontSize: 11, marginTop: 4 },
  acceptBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.sm,
    paddingVertical: 11,
    alignItems: 'center',
    marginTop: spacing.sm,
  },
  acceptBtnUrgent: { backgroundColor: colors.danger },
  acceptText: { color: '#fff', fontWeight: '800', fontSize: 13 },
  dismissBtn: { paddingVertical: spacing.sm + 4, alignItems: 'center' },
  dismissText: { color: colors.muted, fontSize: 13 },
});

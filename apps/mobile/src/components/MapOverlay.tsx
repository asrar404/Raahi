/**
 * Map overlays: risk banner, current-leg card, connection state, refuges.
 *
 * Split out of MapViewScreen so the screen stays focused on the map itself and
 * each overlay can be reasoned about independently.
 *
 * All overlays use `pointerEvents="box-none"` so touches pass through to the
 * map anywhere they are not directly over a control — otherwise a full-width
 * banner would silently block panning.
 */

import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { colors, modeColors, radius, riskColor, safetyColor, spacing } from '../constants/colors';
import type { SafeRefuge, TripLeg } from '../store/types';
import type { WsStatus } from '../services/websocket';

// ============================================================
// Risk banner
// ============================================================
interface RiskBannerProps {
  inRiskZone: boolean;
  riskLevel: number;
  safetyScore: number | null;
  zoneName?: string;
  offRoute: boolean;
  sosActive: boolean;
}

export function RiskBanner({
  inRiskZone,
  riskLevel,
  safetyScore,
  zoneName,
  offRoute,
  sosActive,
}: RiskBannerProps) {
  // Only one banner at a time, ordered by severity. Stacking three would
  // cover the map and bury the most important one.
  if (sosActive) {
    return (
      <View style={[styles.banner, { backgroundColor: colors.dangerDark }]}>
        <Text style={styles.bannerTitle}>SOS ACTIVE</Text>
        <Text style={styles.bannerBody}>
          Your emergency contacts have your live location.
        </Text>
      </View>
    );
  }

  if (inRiskZone) {
    return (
      <View style={[styles.banner, { backgroundColor: riskColor(riskLevel) }]}>
        <Text style={styles.bannerTitle}>
          High risk area{zoneName ? `: ${zoneName}` : ''}
        </Text>
        <Text style={styles.bannerBody}>
          Risk {riskLevel}/5. Stay alert and consider moving to a busier street.
        </Text>
      </View>
    );
  }

  if (offRoute) {
    return (
      <View style={[styles.banner, { backgroundColor: colors.warning }]}>
        <Text style={styles.bannerTitle}>Off route</Text>
        <Text style={styles.bannerBody}>
          You have drifted from your planned path.
        </Text>
      </View>
    );
  }

  if (safetyScore !== null && safetyScore < 3) {
    return (
      <View style={[styles.banner, { backgroundColor: colors.warning }]}>
        <Text style={styles.bannerTitle}>
          Area safety {safetyScore.toFixed(1)}/5
        </Text>
        <Text style={styles.bannerBody}>Keep to well-lit, busy roads.</Text>
      </View>
    );
  }

  return null;
}

// ============================================================
// Current leg card
// ============================================================
interface LegCardProps {
  leg: TripLeg | null;
  legIndex: number;
  legCount: number;
  onAdvance: () => void;
  busy?: boolean;
}

export function LegCard({ leg, legIndex, legCount, onAdvance, busy }: LegCardProps) {
  if (!leg) return null;

  const tint = modeColors[leg.mode] ?? colors.accent;
  const isLast = legIndex >= legCount - 1;

  return (
    <View style={styles.legCard}>
      <View style={styles.legCardTop}>
        <View style={[styles.legBadge, { backgroundColor: `${tint}26`, borderColor: tint }]}>
          <Text style={[styles.legBadgeText, { color: tint }]}>{leg.mode}</Text>
        </View>
        <Text style={styles.legProgress}>
          Step {legIndex + 1} of {legCount}
        </Text>
      </View>

      <Text style={styles.legTo} numberOfLines={1}>
        → {leg.to_name}
      </Text>

      <View style={styles.legMetaRow}>
        <Text style={styles.legMeta}>
          {leg.distance_km ? `${leg.distance_km.toFixed(1)} km` : '—'}
        </Text>
        <Text style={styles.legDot}>·</Text>
        <Text style={styles.legMeta}>
          {leg.planned_duration_mins ? `${leg.planned_duration_mins} min` : '—'}
        </Text>
        <Text style={styles.legDot}>·</Text>
        <Text style={styles.legMeta}>
          {leg.planned_cost > 0 ? `₹${leg.planned_cost.toFixed(0)}` : 'Free'}
        </Text>
        {leg.safety_score != null ? (
          <>
            <Text style={styles.legDot}>·</Text>
            <Text style={[styles.legMeta, { color: safetyColor(leg.safety_score) }]}>
              {leg.safety_score.toFixed(1)}/5
            </Text>
          </>
        ) : null}
      </View>

      <TouchableOpacity
        style={styles.advanceBtn}
        onPress={onAdvance}
        disabled={busy}
        accessibilityRole="button"
      >
        <Text style={styles.advanceText}>
          {busy ? 'Saving…' : isLast ? 'I have arrived' : 'Done — next step'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

// ============================================================
// Connection pill
// ============================================================
export function ConnectionPill({
  status,
  queued,
  onRetry,
}: {
  status: WsStatus;
  queued: number;
  onRetry: () => void;
}) {
  // Silent when healthy — a permanent "connected" badge is just noise.
  if (status === 'open' && queued === 0) return null;

  const copy =
    status === 'failed'
      ? 'Offline — tap to retry'
      : status === 'connecting'
        ? 'Reconnecting…'
        : status === 'open'
          ? `Syncing ${queued} update(s)`
          : 'Not connected';

  const tint =
    status === 'failed' ? colors.danger : status === 'open' ? colors.warning : colors.muted;

  return (
    <TouchableOpacity
      style={[styles.pill, { borderColor: tint }]}
      onPress={onRetry}
      disabled={status === 'connecting'}
      accessibilityRole="button"
    >
      <View style={[styles.pillDot, { backgroundColor: tint }]} />
      <Text style={[styles.pillText, { color: tint }]}>{copy}</Text>
    </TouchableOpacity>
  );
}

// ============================================================
// Refuge strip
// ============================================================
export function RefugeStrip({ refuges }: { refuges: SafeRefuge[] }) {
  if (!refuges.length) return null;
  const nearest = refuges[0];
  if (!nearest) return null;

  return (
    <View style={styles.refugeStrip}>
      <Text style={styles.refugeLabel}>Nearest safe place</Text>
      <Text style={styles.refugeName} numberOfLines={1}>
        {nearest.zone_name} · {Math.round(nearest.distance_m)} m
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    marginHorizontal: spacing.md,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
  },
  bannerTitle: { color: '#fff', fontWeight: '800', fontSize: 14 },
  bannerBody: { color: 'rgba(255,255,255,0.9)', fontSize: 12, marginTop: 3, lineHeight: 17 },

  legCard: {
    backgroundColor: `${colors.surface}F5`,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  legCardTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  legBadge: {
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: 9,
    paddingVertical: 3,
  },
  legBadgeText: { fontSize: 11, fontWeight: '800', textTransform: 'capitalize' },
  legProgress: { color: colors.muted, fontSize: 11 },
  legTo: { color: colors.text, fontSize: 17, fontWeight: '700', marginTop: spacing.sm },
  legMetaRow: { flexDirection: 'row', alignItems: 'center', marginTop: 5 },
  legMeta: { color: colors.textDim, fontSize: 12 },
  legDot: { color: colors.muted, marginHorizontal: 6 },
  advanceBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: spacing.md,
  },
  advanceText: { color: '#fff', fontWeight: '800', fontSize: 14 },

  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    backgroundColor: `${colors.bg}E6`,
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 5,
    marginHorizontal: spacing.md,
  },
  pillDot: { width: 7, height: 7, borderRadius: 4, marginRight: 6 },
  pillText: { fontSize: 11, fontWeight: '600' },

  refugeStrip: {
    backgroundColor: `${colors.success}22`,
    borderLeftWidth: 3,
    borderLeftColor: colors.success,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.sm,
    marginHorizontal: spacing.md,
  },
  refugeLabel: { color: colors.success, fontSize: 10, fontWeight: '700', letterSpacing: 0.5 },
  refugeName: { color: colors.text, fontSize: 13, fontWeight: '600', marginTop: 2 },
});

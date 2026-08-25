/**
 * Route card for the selection screen.
 *
 * The card leads with the trade-off, not the score. A traveller choosing
 * between routes cares about cost, time and how safe the walk is — the utility
 * score is an implementation detail and is shown only as a subtle "best match"
 * badge on the top-ranked option.
 *
 * Warnings from the planner are shown prominently and never truncated. They are
 * the whole point of a safety-first planner: a route that crosses a flagged
 * zone must say so before the user commits to it.
 */

import React, { useState } from 'react';
import { LayoutAnimation, Platform, StyleSheet, Text, TouchableOpacity, UIManager, View } from 'react-native';

import { colors, modeColors, radius, safetyColor, spacing } from '../constants/colors';
import type { PlannedRoute, RouteLeg } from '../store/types';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

const MODE_ICONS: Record<string, string> = {
  walk: 'Walk',
  metro: 'Metro',
  bus: 'Bus',
  train: 'Train',
  auto: 'Auto',
  cab: 'Cab',
  rapido: 'Bike',
  ferry: 'Ferry',
};

function Stars({ rating }: { rating: number }) {
  const filled = Math.round(rating);
  return (
    <View style={styles.starsRow}>
      {[1, 2, 3, 4, 5].map((n) => (
        <Text
          key={n}
          style={[
            styles.star,
            { color: n <= filled ? safetyColor(rating) : colors.border },
          ]}
        >
          ★
        </Text>
      ))}
      <Text style={[styles.safetyValue, { color: safetyColor(rating) }]}>
        {rating.toFixed(1)}
      </Text>
    </View>
  );
}

function LegRow({ leg }: { leg: RouteLeg }) {
  const tint = modeColors[leg.mode] ?? colors.accent;
  return (
    <View style={styles.legRow}>
      <View style={[styles.legDot, { backgroundColor: tint }]} />
      <View style={styles.legBody}>
        <Text style={styles.legHeading}>
          <Text style={{ color: tint, fontWeight: '700' }}>
            {MODE_ICONS[leg.mode] ?? leg.mode}
          </Text>
          {'  '}
          {leg.from_name} → {leg.to_name}
        </Text>
        <Text style={styles.legMeta}>
          {leg.distance_km.toFixed(1)} km · {leg.duration_mins} min ·{' '}
          {leg.planned_cost > 0 ? `₹${leg.planned_cost.toFixed(0)}` : 'Free'}
          {leg.provider ? ` · ${leg.provider}` : ''}
        </Text>
        <Text style={[styles.legSafety, { color: safetyColor(leg.safety_score) }]}>
          Safety {leg.safety_score.toFixed(1)}/5
        </Text>
      </View>
    </View>
  );
}

interface RouteCardProps {
  route: PlannedRoute;
  /** Marks the top-ranked option. */
  recommended?: boolean;
  selected?: boolean;
  budgetCeiling?: number;
  onSelect: (route: PlannedRoute) => void;
}

export default function RouteCard({
  route,
  recommended = false,
  selected = false,
  budgetCeiling,
  onSelect,
}: RouteCardProps) {
  const [expanded, setExpanded] = useState(false);

  const toggle = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setExpanded((prev) => !prev);
  };

  const walkKm = route.legs
    .filter((leg) => leg.mode === 'walk')
    .reduce((sum, leg) => sum + leg.distance_km, 0);

  const overBudget = budgetCeiling !== undefined && route.total_cost > budgetCeiling;
  const transfers = route.legs.filter((leg) => leg.mode !== 'walk').length - 1;

  return (
    <View
      style={[
        styles.card,
        selected && styles.cardSelected,
        overBudget && styles.cardOverBudget,
      ]}
    >
      {recommended && !overBudget ? (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>BEST MATCH</Text>
        </View>
      ) : null}

      {/* Mode sequence */}
      <View style={styles.modeRow}>
        {route.legs.map((leg, index) => (
          <View key={`${leg.leg_order}-${leg.mode}`} style={styles.modeChipWrap}>
            <View
              style={[
                styles.modeChip,
                { borderColor: modeColors[leg.mode] ?? colors.accent },
              ]}
            >
              <Text
                style={[styles.modeChipText, { color: modeColors[leg.mode] ?? colors.accent }]}
              >
                {MODE_ICONS[leg.mode] ?? leg.mode}
              </Text>
            </View>
            {index < route.legs.length - 1 ? (
              <Text style={styles.modeArrow}>→</Text>
            ) : null}
          </View>
        ))}
      </View>

      {/* Headline figures */}
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={[styles.statValue, overBudget && { color: colors.danger }]}>
            ₹{route.total_cost.toFixed(0)}
          </Text>
          <Text style={styles.statLabel}>cost</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.stat}>
          <Text style={styles.statValue}>{route.total_duration}</Text>
          <Text style={styles.statLabel}>minutes</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.stat}>
          <Text style={styles.statValue}>{walkKm.toFixed(1)}</Text>
          <Text style={styles.statLabel}>km walk</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.stat}>
          <Text style={styles.statValue}>{Math.max(0, transfers)}</Text>
          <Text style={styles.statLabel}>changes</Text>
        </View>
      </View>

      <Stars rating={route.safety_rating} />

      {route.summary ? <Text style={styles.summary}>{route.summary}</Text> : null}

      {/* Planner warnings — never truncated */}
      {route.warnings?.length ? (
        <View style={styles.warnBox}>
          {route.warnings.map((warning) => (
            <Text key={warning} style={styles.warnText}>
              • {warning}
            </Text>
          ))}
        </View>
      ) : null}

      {/* Expandable leg breakdown */}
      <TouchableOpacity onPress={toggle} style={styles.expandBtn} accessibilityRole="button">
        <Text style={styles.expandText}>
          {expanded ? 'Hide details' : `Show all ${route.legs.length} steps`}
        </Text>
      </TouchableOpacity>

      {expanded ? (
        <View style={styles.legList}>
          {route.legs.map((leg) => (
            <LegRow key={`${leg.leg_order}-${leg.to_name}`} leg={leg} />
          ))}
        </View>
      ) : null}

      <TouchableOpacity
        style={[styles.selectBtn, selected && styles.selectBtnActive]}
        onPress={() => onSelect(route)}
        accessibilityRole="button"
        accessibilityLabel={`Choose this route: ₹${route.total_cost.toFixed(0)}, ${route.total_duration} minutes`}
      >
        <Text style={styles.selectBtnText}>
          {selected ? 'Selected' : 'Start this route'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  cardSelected: {
    borderColor: colors.accent,
    backgroundColor: colors.surfaceAlt,
  },
  cardOverBudget: {
    borderColor: colors.danger,
  },
  badge: {
    alignSelf: 'flex-start',
    backgroundColor: `${colors.accent}33`,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
    borderRadius: radius.pill,
    marginBottom: spacing.sm,
  },
  badgeText: {
    color: colors.accent,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.8,
  },
  modeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  modeChipWrap: { flexDirection: 'row', alignItems: 'center' },
  modeChip: {
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  modeChipText: { fontSize: 11, fontWeight: '700' },
  modeArrow: { color: colors.muted, marginHorizontal: 5, fontSize: 12 },
  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: spacing.sm,
  },
  stat: { flex: 1, alignItems: 'center' },
  statValue: { color: colors.text, fontSize: 18, fontWeight: '800' },
  statLabel: { color: colors.muted, fontSize: 10, marginTop: 2 },
  statDivider: { width: 1, height: 26, backgroundColor: colors.border },
  starsRow: { flexDirection: 'row', alignItems: 'center', marginBottom: spacing.sm },
  star: { fontSize: 14, marginRight: 1 },
  safetyValue: { marginLeft: 6, fontSize: 12, fontWeight: '700' },
  summary: {
    color: colors.textDim,
    fontSize: 13,
    lineHeight: 18,
    marginBottom: spacing.sm,
  },
  warnBox: {
    backgroundColor: `${colors.warning}1A`,
    borderLeftWidth: 3,
    borderLeftColor: colors.warning,
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginBottom: spacing.sm,
  },
  warnText: { color: colors.warning, fontSize: 12, lineHeight: 17 },
  expandBtn: { paddingVertical: 6 },
  expandText: { color: colors.accent, fontSize: 12, fontWeight: '600' },
  legList: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.sm,
    marginBottom: spacing.sm,
  },
  legRow: { flexDirection: 'row', marginBottom: spacing.sm },
  legDot: { width: 8, height: 8, borderRadius: 4, marginTop: 5, marginRight: spacing.sm },
  legBody: { flex: 1 },
  legHeading: { color: colors.text, fontSize: 13, lineHeight: 18 },
  legMeta: { color: colors.muted, fontSize: 11, marginTop: 2 },
  legSafety: { fontSize: 11, marginTop: 2, fontWeight: '600' },
  selectBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: 13,
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  selectBtnActive: { backgroundColor: colors.success },
  selectBtnText: { color: '#fff', fontWeight: '800', fontSize: 14 },
});

/**
 * Budget widget, overlaid on the map.
 *
 * Collapsed it is a single progress bar; tapping expands the expense log and a
 * quick-add row. It sits at the top of the screen rather than the bottom so it
 * never competes with the SOS button for thumb space.
 *
 * Expenses are written to the local store first and pushed to the gateway
 * after. A traveller logging a ₹20 auto fare in a basement metro station with
 * no signal must not lose the entry.
 */

import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  LayoutAnimation,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import { colors, radius, spacing } from '../constants/colors';
import { logExpense } from '../services/api';
import {
  selectPercentUsed,
  selectRemaining,
  selectSeverity,
  useBudgetStore,
} from '../store/budgetSlice';
import { useTripStore } from '../store/tripSlice';
import type { ExpenseCategory } from '../store/types';

const CATEGORIES: ExpenseCategory[] = ['transit', 'food', 'stay', 'misc'];

export default function ExpenseWidget() {
  const ceiling = useBudgetStore((s) => s.ceiling);
  const spent = useBudgetStore((s) => s.spent);
  const logs = useBudgetStore((s) => s.logs);
  const serverLogs = useBudgetStore((s) => s.serverLogs);
  const remaining = useBudgetStore(selectRemaining);
  const percentUsed = useBudgetStore(selectPercentUsed);
  const severity = useBudgetStore(selectSeverity);
  const addExpense = useBudgetStore((s) => s.addExpense);
  const markSynced = useBudgetStore((s) => s.markSynced);

  const activeTripId = useTripStore((s) => s.activeTrip?.id ?? null);

  const [expanded, setExpanded] = useState(false);
  const [amount, setAmount] = useState('');
  const [category, setCategory] = useState<ExpenseCategory>('transit');
  const [saving, setSaving] = useState(false);

  const barColor =
    severity === 'critical'
      ? colors.danger
      : severity === 'warning'
        ? colors.warning
        : colors.success;

  const combined = useMemo(() => {
    const local = logs.map((log) => ({
      key: `local-${log.at}`,
      amount: log.amount,
      category: log.category,
      description: log.desc,
      at: log.at,
      pending: !log.synced,
    }));
    const remote = serverLogs.map((log) => ({
      key: `srv-${log.id}`,
      amount: log.amount,
      category: log.category,
      description: log.description ?? '',
      at: log.recorded_at,
      pending: false,
    }));
    return [...local, ...remote]
      .sort((a, b) => b.at.localeCompare(a.at))
      .slice(0, 20);
  }, [logs, serverLogs]);

  const toggle = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setExpanded((prev) => !prev);
  };

  const submit = async () => {
    const value = Number.parseFloat(amount);
    if (!Number.isFinite(value) || value <= 0) return;

    const at = new Date().toISOString();
    // Optimistic local write, so the number updates instantly and survives
    // a failed request.
    addExpense(value, category, '', false);
    setAmount('');

    if (!activeTripId) return;

    setSaving(true);
    try {
      await logExpense(activeTripId, value, category);
      markSynced(at);
    } catch {
      // Stays flagged pending; ExpenseLogScreen offers a retry.
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.wrapper} pointerEvents="box-none">
      <TouchableOpacity
        style={styles.card}
        onPress={toggle}
        activeOpacity={0.9}
        accessibilityRole="button"
        accessibilityLabel={`Budget: ₹${spent.toFixed(0)} of ₹${ceiling.toFixed(0)} spent`}
      >
        <View style={styles.headerRow}>
          <Text style={styles.spent}>
            ₹{spent.toFixed(0)}
            <Text style={styles.ceiling}> / ₹{ceiling.toFixed(0)}</Text>
          </Text>
          <Text style={[styles.remaining, { color: barColor }]}>
            {remaining >= 0
              ? `₹${remaining.toFixed(0)} left`
              : `₹${Math.abs(remaining).toFixed(0)} over`}
          </Text>
        </View>

        <View style={styles.track}>
          <View
            style={[
              styles.fill,
              { width: `${Math.min(100, percentUsed)}%`, backgroundColor: barColor },
            ]}
          />
        </View>

        <Text style={styles.hint}>
          {expanded ? 'Tap to collapse' : 'Tap to log an expense'}
        </Text>
      </TouchableOpacity>

      {expanded ? (
        <View style={styles.panel}>
          {/* Quick add */}
          <View style={styles.addRow}>
            <TextInput
              style={styles.amountInput}
              placeholder="₹0"
              placeholderTextColor={colors.muted}
              keyboardType="decimal-pad"
              value={amount}
              onChangeText={setAmount}
              returnKeyType="done"
              onSubmitEditing={() => void submit()}
            />
            <TouchableOpacity
              style={styles.addBtn}
              onPress={() => void submit()}
              disabled={saving || !amount}
            >
              {saving ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.addBtnText}>Add</Text>
              )}
            </TouchableOpacity>
          </View>

          <View style={styles.catRow}>
            {CATEGORIES.map((cat) => (
              <TouchableOpacity
                key={cat}
                style={[styles.catChip, category === cat && styles.catChipActive]}
                onPress={() => setCategory(cat)}
              >
                <Text
                  style={[styles.catText, category === cat && styles.catTextActive]}
                >
                  {cat}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Log */}
          {combined.length ? (
            <ScrollView style={styles.logList} nestedScrollEnabled>
              {combined.map((entry) => (
                <View key={entry.key} style={styles.logRow}>
                  <Text style={styles.logCat}>{entry.category}</Text>
                  <Text style={styles.logDesc} numberOfLines={1}>
                    {entry.description || '—'}
                    {entry.pending ? '  (pending)' : ''}
                  </Text>
                  <Text style={styles.logAmount}>₹{entry.amount.toFixed(0)}</Text>
                </View>
              ))}
            </ScrollView>
          ) : (
            <Text style={styles.empty}>No expenses logged yet.</Text>
          )}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    position: 'absolute',
    top: 50,
    left: spacing.md,
    right: spacing.md,
  },
  card: {
    backgroundColor: `${colors.surface}F2`,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm + 2,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    marginBottom: 6,
  },
  spent: { color: colors.text, fontSize: 17, fontWeight: '800' },
  ceiling: { color: colors.muted, fontSize: 13, fontWeight: '500' },
  remaining: { fontSize: 12, fontWeight: '700' },
  track: {
    height: 6,
    backgroundColor: colors.border,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  fill: { height: '100%', borderRadius: radius.pill },
  hint: { color: colors.muted, fontSize: 10, marginTop: 5, textAlign: 'center' },
  panel: {
    backgroundColor: `${colors.surface}F7`,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm + 2,
    marginTop: spacing.sm,
  },
  addRow: { flexDirection: 'row', gap: spacing.sm },
  amountInput: {
    flex: 1,
    backgroundColor: colors.bg,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: spacing.sm,
    paddingVertical: 9,
    fontSize: 15,
  },
  addBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    justifyContent: 'center',
    minWidth: 64,
    alignItems: 'center',
  },
  addBtnText: { color: '#fff', fontWeight: '700', fontSize: 13 },
  catRow: { flexDirection: 'row', gap: 6, marginTop: spacing.sm },
  catChip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  catChipActive: { borderColor: colors.accent, backgroundColor: `${colors.accent}22` },
  catText: { color: colors.muted, fontSize: 11 },
  catTextActive: { color: colors.accent, fontWeight: '700' },
  logList: { maxHeight: 150, marginTop: spacing.sm },
  logRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 6,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  logCat: { color: colors.accent, fontSize: 11, width: 58, textTransform: 'capitalize' },
  logDesc: { color: colors.textDim, fontSize: 12, flex: 1 },
  logAmount: { color: colors.text, fontSize: 13, fontWeight: '700' },
  empty: { color: colors.muted, fontSize: 12, textAlign: 'center', marginTop: spacing.sm },
});

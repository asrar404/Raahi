/**
 * Expense log.
 *
 * Server totals are authoritative, but locally-entered expenses that have not
 * synced are shown too, with a retry. Losing a fare someone typed in while
 * underground would quietly corrupt their budget tracking, which is the one
 * number this screen exists to get right.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import { colors, radius, spacing } from '../constants/colors';
import { deleteExpense, fetchBudget, logExpense } from '../services/api';
import {
  selectPercentUsed,
  selectRemaining,
  selectSeverity,
  selectUnsynced,
  useBudgetStore,
} from '../store/budgetSlice';
import { useTripStore } from '../store/tripSlice';
import type { ExpenseCategory } from '../store/types';

const CATEGORIES: ExpenseCategory[] = ['transit', 'food', 'stay', 'misc'];

const CATEGORY_LABELS: Record<ExpenseCategory, string> = {
  transit: 'Transit',
  food: 'Food',
  stay: 'Stay',
  misc: 'Other',
};

export default function ExpenseLogScreen() {
  const activeTrip = useTripStore((s) => s.activeTrip);
  const tripId = activeTrip?.id ?? null;

  const ceiling = useBudgetStore((s) => s.ceiling);
  const spent = useBudgetStore((s) => s.spent);
  const planned = useBudgetStore((s) => s.planned);
  const serverLogs = useBudgetStore((s) => s.serverLogs);
  const remaining = useBudgetStore(selectRemaining);
  const percentUsed = useBudgetStore(selectPercentUsed);
  const severity = useBudgetStore(selectSeverity);
  const unsynced = useBudgetStore(selectUnsynced);
  const syncFromServer = useBudgetStore((s) => s.syncFromServer);
  const addExpense = useBudgetStore((s) => s.addExpense);
  const markSynced = useBudgetStore((s) => s.markSynced);

  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<ExpenseCategory>('transit');
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    if (!tripId) return;
    setRefreshing(true);
    try {
      const summary = await fetchBudget(tripId);
      syncFromServer(summary);
    } catch {
      // Offline: the local figures remain on screen.
    } finally {
      setRefreshing(false);
    }
  }, [tripId, syncFromServer]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const submit = async () => {
    const value = Number.parseFloat(amount);
    if (!Number.isFinite(value) || value <= 0) {
      Alert.alert('Enter an amount', 'Type the amount you spent, for example 40.');
      return;
    }

    const at = new Date().toISOString();
    addExpense(value, category, description, false);
    setAmount('');
    setDescription('');

    if (!tripId) return;

    setSaving(true);
    try {
      const summary = await logExpense(tripId, value, category, description || undefined);
      markSynced(at);
      syncFromServer(summary);
    } catch {
      Alert.alert(
        'Saved locally',
        'Could not reach the server, so this expense is stored on your phone. Pull down to retry once you have signal.',
      );
    } finally {
      setSaving(false);
    }
  };

  const retryUnsynced = async () => {
    if (!tripId || !unsynced.length) return;
    setSaving(true);
    try {
      for (const log of unsynced) {
        await logExpense(tripId, log.amount, log.category, log.desc || undefined);
        markSynced(log.at);
      }
      await refresh();
    } catch {
      Alert.alert('Still offline', 'Could not sync. Try again when you have signal.');
    } finally {
      setSaving(false);
    }
  };

  const removeServerLog = (id: string) => {
    Alert.alert('Delete this expense?', 'This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            const summary = await deleteExpense(id);
            syncFromServer(summary);
          } catch {
            Alert.alert('Could not delete', 'Please try again.');
          }
        },
      },
    ]);
  };

  const byCategory = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const log of serverLogs) {
      totals[log.category] = (totals[log.category] ?? 0) + log.amount;
    }
    for (const log of unsynced) {
      totals[log.category] = (totals[log.category] ?? 0) + log.amount;
    }
    return totals;
  }, [serverLogs, unsynced]);

  const barColor =
    severity === 'critical'
      ? colors.danger
      : severity === 'warning'
        ? colors.warning
        : colors.success;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => void refresh()}
          tintColor={colors.accent}
        />
      }
    >
      <Text style={styles.title}>Budget</Text>

      {/* Summary */}
      <View style={styles.summaryCard}>
        <Text style={styles.summarySpent}>
          ₹{spent.toFixed(0)}
          <Text style={styles.summaryCeiling}> of ₹{ceiling.toFixed(0)}</Text>
        </Text>

        <View style={styles.track}>
          <View
            style={[
              styles.fill,
              { width: `${Math.min(100, percentUsed)}%`, backgroundColor: barColor },
            ]}
          />
        </View>

        <View style={styles.summaryRow}>
          <Text style={[styles.summaryStat, { color: barColor }]}>
            {remaining >= 0
              ? `₹${remaining.toFixed(0)} remaining`
              : `₹${Math.abs(remaining).toFixed(0)} over budget`}
          </Text>
          <Text style={styles.summaryMuted}>{percentUsed.toFixed(0)}% used</Text>
        </View>

        {planned > 0 ? (
          <Text style={styles.summaryMuted}>
            Planned cost was ₹{planned.toFixed(0)}
            {spent > planned
              ? ` — you are ₹${(spent - planned).toFixed(0)} above plan`
              : spent > 0
                ? ` — you are ₹${(planned - spent).toFixed(0)} under plan`
                : ''}
          </Text>
        ) : null}
      </View>

      {/* Unsynced warning */}
      {unsynced.length ? (
        <TouchableOpacity style={styles.syncBox} onPress={() => void retryUnsynced()}>
          <Text style={styles.syncTitle}>
            {unsynced.length} expense{unsynced.length > 1 ? 's' : ''} not synced
          </Text>
          <Text style={styles.syncBody}>Tap to retry now.</Text>
        </TouchableOpacity>
      ) : null}

      {/* Add */}
      <View style={styles.addCard}>
        <Text style={styles.sectionLabel}>ADD AN EXPENSE</Text>

        <View style={styles.catRow}>
          {CATEGORIES.map((cat) => (
            <TouchableOpacity
              key={cat}
              style={[styles.catChip, category === cat && styles.catChipActive]}
              onPress={() => setCategory(cat)}
            >
              <Text style={[styles.catText, category === cat && styles.catTextActive]}>
                {CATEGORY_LABELS[cat]}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <View style={styles.inputRow}>
          <TextInput
            style={styles.amountInput}
            placeholder="₹0"
            placeholderTextColor={colors.muted}
            keyboardType="decimal-pad"
            value={amount}
            onChangeText={setAmount}
          />
          <TextInput
            style={styles.descInput}
            placeholder="What was it for? (optional)"
            placeholderTextColor={colors.muted}
            value={description}
            onChangeText={setDescription}
          />
        </View>

        <TouchableOpacity
          style={[styles.addBtn, saving && styles.addBtnDisabled]}
          onPress={() => void submit()}
          disabled={saving}
        >
          {saving ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.addBtnText}>Add expense</Text>
          )}
        </TouchableOpacity>
      </View>

      {/* Breakdown */}
      {Object.keys(byCategory).length ? (
        <View style={styles.breakdownCard}>
          <Text style={styles.sectionLabel}>BY CATEGORY</Text>
          {CATEGORIES.filter((cat) => byCategory[cat]).map((cat) => (
            <View key={cat} style={styles.breakdownRow}>
              <Text style={styles.breakdownLabel}>{CATEGORY_LABELS[cat]}</Text>
              <Text style={styles.breakdownValue}>
                ₹{(byCategory[cat] ?? 0).toFixed(0)}
              </Text>
            </View>
          ))}
        </View>
      ) : null}

      {/* Log */}
      <Text style={styles.sectionLabel}>HISTORY</Text>

      {unsynced.map((log) => (
        <View key={`local-${log.at}`} style={[styles.logRow, styles.logRowPending]}>
          <View style={styles.logMain}>
            <Text style={styles.logCategory}>{CATEGORY_LABELS[log.category]}</Text>
            <Text style={styles.logDesc} numberOfLines={1}>
              {log.desc || 'No description'} · pending sync
            </Text>
          </View>
          <Text style={styles.logAmount}>₹{log.amount.toFixed(0)}</Text>
        </View>
      ))}

      {serverLogs.map((log) => (
        <TouchableOpacity
          key={log.id}
          style={styles.logRow}
          onLongPress={() => removeServerLog(log.id)}
        >
          <View style={styles.logMain}>
            <Text style={styles.logCategory}>
              {CATEGORY_LABELS[log.category as ExpenseCategory] ?? log.category}
            </Text>
            <Text style={styles.logDesc} numberOfLines={1}>
              {log.description || 'No description'}
            </Text>
            <Text style={styles.logTime}>
              {new Date(log.recorded_at).toLocaleString('en-IN', {
                day: 'numeric',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </Text>
          </View>
          <Text style={styles.logAmount}>₹{log.amount.toFixed(0)}</Text>
        </TouchableOpacity>
      ))}

      {!serverLogs.length && !unsynced.length ? (
        <Text style={styles.empty}>
          {tripId
            ? 'No expenses yet. Log fares as you go to keep your budget accurate.'
            : 'Start a trip to begin tracking expenses.'}
        </Text>
      ) : (
        <Text style={styles.hint}>Long-press an entry to delete it.</Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingTop: 56, paddingBottom: spacing.xl },
  title: { color: colors.text, fontSize: 30, fontWeight: '900', marginBottom: spacing.md },
  summaryCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  summarySpent: { color: colors.text, fontSize: 28, fontWeight: '900' },
  summaryCeiling: { color: colors.muted, fontSize: 15, fontWeight: '500' },
  track: {
    height: 8,
    backgroundColor: colors.border,
    borderRadius: radius.pill,
    overflow: 'hidden',
    marginTop: spacing.sm,
  },
  fill: { height: '100%', borderRadius: radius.pill },
  summaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.sm,
  },
  summaryStat: { fontSize: 13, fontWeight: '700' },
  summaryMuted: { color: colors.muted, fontSize: 12, marginTop: 2 },
  syncBox: {
    backgroundColor: `${colors.warning}1A`,
    borderLeftWidth: 3,
    borderLeftColor: colors.warning,
    borderRadius: radius.sm,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  syncTitle: { color: colors.warning, fontWeight: '700', fontSize: 13 },
  syncBody: { color: colors.textDim, fontSize: 12, marginTop: 2 },
  addCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  sectionLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.8,
    marginBottom: spacing.sm,
  },
  catRow: { flexDirection: 'row', gap: 6, marginBottom: spacing.sm },
  catChip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.pill,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  catChipActive: { borderColor: colors.accent, backgroundColor: `${colors.accent}22` },
  catText: { color: colors.muted, fontSize: 12 },
  catTextActive: { color: colors.accent, fontWeight: '700' },
  inputRow: { flexDirection: 'row', gap: spacing.sm },
  amountInput: {
    width: 96,
    backgroundColor: colors.bg,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: spacing.sm,
    paddingVertical: 11,
    fontSize: 16,
    fontWeight: '700',
  },
  descInput: {
    flex: 1,
    backgroundColor: colors.bg,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: spacing.sm,
    paddingVertical: 11,
    fontSize: 14,
  },
  addBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: 13,
    alignItems: 'center',
    marginTop: spacing.sm,
  },
  addBtnDisabled: { opacity: 0.6 },
  addBtnText: { color: '#fff', fontWeight: '800', fontSize: 14 },
  breakdownCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  breakdownRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 5,
  },
  breakdownLabel: { color: colors.textDim, fontSize: 13 },
  breakdownValue: { color: colors.text, fontSize: 13, fontWeight: '700' },
  logRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm + 2,
    marginBottom: spacing.sm,
  },
  logRowPending: { borderColor: colors.warning, borderStyle: 'dashed' },
  logMain: { flex: 1 },
  logCategory: { color: colors.accent, fontSize: 11, fontWeight: '700' },
  logDesc: { color: colors.text, fontSize: 13, marginTop: 2 },
  logTime: { color: colors.muted, fontSize: 10, marginTop: 2 },
  logAmount: { color: colors.text, fontSize: 16, fontWeight: '800' },
  empty: {
    color: colors.muted,
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 19,
    marginTop: spacing.md,
  },
  hint: { color: colors.muted, fontSize: 11, textAlign: 'center', marginTop: spacing.sm },
});

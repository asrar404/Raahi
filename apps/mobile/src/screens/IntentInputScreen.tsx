/**
 * Intent input — the entry point of the whole flow.
 *
 * One free-text field rather than origin/destination/budget/mode pickers. The
 * pitch is that you describe the journey the way you would to a friend, and the
 * AI engine extracts the structure. Example chips exist because a blank text
 * box gives no clue about what the parser understands.
 *
 * If the parser fell back to heuristics, the screen says so, so a user whose
 * request was misread knows to rephrase rather than assuming the app is broken.
 */

import React, { useState } from 'react';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import { colors, radius, spacing } from '../constants/colors';
import type { JourneyStackParamList } from '../constants/routes';
import { useTrip } from '../hooks/useTrip';
import { useCurrentLocation } from '../hooks/useLocation';
import { useAuthStore } from '../store/authSlice';
import { useTripStore } from '../store/tripSlice';

const EXAMPLES = [
  'Paharganj to Saket under ₹150 by metro',
  'CST to Bandra, budget ₹80',
  'Kashmere Gate to Dwarka at 11pm, I am travelling alone',
  'Koramangala to Whitefield under ₹400',
];

export default function IntentInputScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<JourneyStackParamList>>();
  const { plan, planning, planError } = useTrip();
  const activeTrip = useTripStore((s) => s.activeTrip);
  const userName = useAuthStore((s) => s.user?.full_name ?? '');
  const { fix } = useCurrentLocation();

  const [input, setInput] = useState('');

  const handlePlan = async () => {
    if (!input.trim() || planning) return;
    const ok = await plan(input.trim());
    if (ok) navigation.navigate('RouteSelection');
  };

  const firstName = userName.split(' ')[0] ?? '';

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.greeting}>
          {firstName ? `Hi ${firstName}.` : 'Hi.'}
        </Text>
        <Text style={styles.title}>Where to?</Text>
        <Text style={styles.subtitle}>
          Describe your journey in plain language — where from, where to, and what you
          can spend.
        </Text>

        {/* Resume an in-progress trip */}
        {activeTrip ? (
          <TouchableOpacity
            style={styles.resumeCard}
            onPress={() => navigation.navigate('MapView', { tripId: activeTrip.id })}
            accessibilityRole="button"
          >
            <Text style={styles.resumeLabel}>TRIP IN PROGRESS</Text>
            <Text style={styles.resumeRoute} numberOfLines={1}>
              {activeTrip.origin_name} → {activeTrip.dest_name}
            </Text>
            <Text style={styles.resumeHint}>Tap to return to navigation</Text>
          </TouchableOpacity>
        ) : null}

        <TextInput
          style={styles.input}
          multiline
          numberOfLines={4}
          placeholder="e.g. I need to get from Paharganj to Saket under ₹150 by metro, I'm travelling alone at night"
          placeholderTextColor={colors.muted}
          value={input}
          onChangeText={setInput}
          editable={!planning}
          accessibilityLabel="Describe your journey"
        />

        <Text style={styles.examplesLabel}>Try one of these</Text>
        <View style={styles.chipWrap}>
          {EXAMPLES.map((example) => (
            <TouchableOpacity
              key={example}
              style={styles.chip}
              onPress={() => setInput(example)}
              disabled={planning}
            >
              <Text style={styles.chipText}>{example}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {fix ? (
          <Text style={styles.locationNote}>
            Location found — you can say "from here" as your starting point.
          </Text>
        ) : (
          <Text style={styles.locationNote}>
            Enable location access to use "from here" as a starting point.
          </Text>
        )}

        {planError ? (
          <View style={styles.errorBox}>
            <Text style={styles.errorText}>{planError}</Text>
          </View>
        ) : null}

        <TouchableOpacity
          style={[styles.button, (planning || !input.trim()) && styles.buttonDisabled]}
          onPress={() => void handlePlan()}
          disabled={planning || !input.trim()}
          accessibilityRole="button"
          accessibilityLabel="Plan my journey"
        >
          {planning ? (
            <View style={styles.buttonBusy}>
              <ActivityIndicator color="#fff" />
              <Text style={styles.buttonText}>Planning…</Text>
            </View>
          ) : (
            <Text style={styles.buttonText}>Plan my journey</Text>
          )}
        </TouchableOpacity>

        <Text style={styles.footnote}>
          RAAHI ranks routes on cost, time and safety together — never cost alone.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.lg, paddingTop: 64, paddingBottom: spacing.xl },
  greeting: { color: colors.muted, fontSize: 14, marginBottom: 4 },
  title: { color: colors.text, fontSize: 34, fontWeight: '900', marginBottom: 6 },
  subtitle: {
    color: colors.textDim,
    fontSize: 14,
    lineHeight: 20,
    marginBottom: spacing.lg,
  },
  resumeCard: {
    backgroundColor: `${colors.accent}1F`,
    borderWidth: 1,
    borderColor: colors.accent,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  resumeLabel: {
    color: colors.accent,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.8,
  },
  resumeRoute: { color: colors.text, fontSize: 15, fontWeight: '700', marginTop: 5 },
  resumeHint: { color: colors.muted, fontSize: 11, marginTop: 3 },
  input: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    padding: spacing.md,
    fontSize: 15,
    lineHeight: 21,
    minHeight: 118,
    textAlignVertical: 'top',
  },
  examplesLabel: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: '600',
    marginTop: spacing.md,
    marginBottom: spacing.sm,
    letterSpacing: 0.5,
  },
  chipWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    backgroundColor: `${colors.accent}1A`,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm + 4,
    paddingVertical: 7,
  },
  chipText: { color: colors.accent, fontSize: 12 },
  locationNote: {
    color: colors.muted,
    fontSize: 11,
    marginTop: spacing.md,
    lineHeight: 16,
  },
  errorBox: {
    backgroundColor: `${colors.danger}1A`,
    borderLeftWidth: 3,
    borderLeftColor: colors.danger,
    borderRadius: radius.sm,
    padding: spacing.md,
    marginTop: spacing.md,
  },
  errorText: { color: colors.danger, fontSize: 13, lineHeight: 18 },
  button: {
    backgroundColor: colors.accent,
    borderRadius: radius.lg,
    paddingVertical: 16,
    alignItems: 'center',
    marginTop: spacing.lg,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonBusy: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  buttonText: { color: '#fff', fontWeight: '800', fontSize: 16 },
  footnote: {
    color: colors.muted,
    fontSize: 11,
    textAlign: 'center',
    marginTop: spacing.md,
    lineHeight: 16,
  },
});

/**
 * Auth screen.
 *
 * Two modes:
 *
 * - **Supabase configured** — phone OTP (the norm in India) or email/password.
 * - **Not configured** — a "continue in demo mode" path that calls
 *   `/auth/verify` against the gateway's seeded demo user. This keeps the app
 *   fully explorable without provisioning Supabase, which matters for local
 *   development and for anyone evaluating the project.
 *
 * Phone is collected on sign-up because `users.phone` is NOT NULL and SOS
 * escalation depends on it.
 */

import React, { useState } from 'react';
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
import { verifyAuth } from '../services/api';
import {
  isAuthEnabled,
  sendOtp,
  signInWithEmail,
  signUpWithEmail,
  verifyOtp,
} from '../services/supabase';
import { useAuthStore } from '../store/authSlice';

type Mode = 'phone' | 'email' | 'demo';
type Step = 'entry' | 'otp';

export default function AuthScreen() {
  const setUser = useAuthStore((s) => s.setUser);

  const [mode, setMode] = useState<Mode>(isAuthEnabled ? 'phone' : 'demo');
  const [step, setStep] = useState<Step>('entry');
  const [signingUp, setSigningUp] = useState(false);

  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  /** Exchange the Supabase session (or demo fallback) for a RAAHI profile. */
  const provision = async () => {
    const { user } = await verifyAuth({
      full_name: fullName || undefined,
      phone: phone || undefined,
      email: email || undefined,
    });
    setUser(user, useAuthStore.getState().token);
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong';
      // Log full error details to Metro console for debugging
      console.error('[AuthScreen] Auth action failed:', {
        message,
        stack: err instanceof Error ? err.stack : undefined,
        error: err,
      });
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  const handleSendOtp = () =>
    run(async () => {
      if (phone.trim().length < 10) throw new Error('Enter a valid phone number');
      await sendOtp(phone.trim());
      setStep('otp');
    });

  const handleVerifyOtp = () =>
    run(async () => {
      if (otp.trim().length < 4) throw new Error('Enter the code you received');
      await verifyOtp(phone.trim(), otp.trim());
      await provision();
    });

  const handleEmail = () =>
    run(async () => {
      if (!email.includes('@')) throw new Error('Enter a valid email address');
      if (password.length < 6) throw new Error('Password must be at least 6 characters');

      if (signingUp) {
        if (phone.trim().length < 10) {
          throw new Error('A phone number is required so RAAHI can raise an SOS for you');
        }
        const token = await signUpWithEmail(email.trim(), password);
        if (!token) {
          throw new Error('Check your email to confirm your account, then sign in.');
        }
      } else {
        try {
          await signInWithEmail(email.trim(), password);
        } catch (err) {
          // Detailed logging for Supabase auth errors
          console.error('[AuthScreen] signInWithEmail failed:', {
            message: err instanceof Error ? err.message : String(err),
            stack: err instanceof Error ? err.stack : undefined,
            error: err,
            email: email.trim(),
          });
          throw err;
        }
      }
      await provision();
    });

  const handleDemo = () =>
    run(async () => {
      // No Supabase token. The gateway's dev bypass resolves this to the
      // seeded demo user, and refuses to do so in production.
      await provision();
    });

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.logo}>RAAHI</Text>
        <Text style={styles.tagline}>
          Budget travel across India, planned around your safety.
        </Text>

        {isAuthEnabled ? (
          <View style={styles.tabs}>
            {(['phone', 'email'] as const).map((m) => (
              <TouchableOpacity
                key={m}
                style={[styles.tab, mode === m && styles.tabActive]}
                onPress={() => {
                  setMode(m);
                  setStep('entry');
                  setError('');
                }}
              >
                <Text style={[styles.tabText, mode === m && styles.tabTextActive]}>
                  {m === 'phone' ? 'Phone' : 'Email'}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        ) : null}

        {/* ── Phone OTP ─────────────────────────────────── */}
        {mode === 'phone' && isAuthEnabled ? (
          <View style={styles.form}>
            {step === 'entry' ? (
              <>
                <Text style={styles.label}>Phone number</Text>
                <TextInput
                  style={styles.input}
                  placeholder="+91 98765 43210"
                  placeholderTextColor={colors.muted}
                  keyboardType="phone-pad"
                  autoComplete="tel"
                  value={phone}
                  onChangeText={setPhone}
                />
                <Text style={styles.helper}>
                  We text a one-time code. Your number is also used to reach you if
                  an SOS is raised.
                </Text>
                <PrimaryButton label="Send code" busy={busy} onPress={handleSendOtp} />
              </>
            ) : (
              <>
                <Text style={styles.label}>Enter the 6-digit code</Text>
                <TextInput
                  style={[styles.input, styles.otpInput]}
                  placeholder="······"
                  placeholderTextColor={colors.muted}
                  keyboardType="number-pad"
                  maxLength={8}
                  value={otp}
                  onChangeText={setOtp}
                />
                <PrimaryButton label="Verify and continue" busy={busy} onPress={handleVerifyOtp} />
                <TouchableOpacity onPress={() => setStep('entry')} style={styles.linkBtn}>
                  <Text style={styles.linkText}>Change number</Text>
                </TouchableOpacity>
              </>
            )}
          </View>
        ) : null}

        {/* ── Email + password ──────────────────────────── */}
        {mode === 'email' && isAuthEnabled ? (
          <View style={styles.form}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              style={styles.input}
              placeholder="you@example.com"
              placeholderTextColor={colors.muted}
              keyboardType="email-address"
              autoCapitalize="none"
              autoComplete="email"
              value={email}
              onChangeText={setEmail}
            />

            <Text style={styles.label}>Password</Text>
            <TextInput
              style={styles.input}
              placeholder="At least 6 characters"
              placeholderTextColor={colors.muted}
              secureTextEntry
              value={password}
              onChangeText={setPassword}
            />

            {signingUp ? (
              <>
                <Text style={styles.label}>Full name</Text>
                <TextInput
                  style={styles.input}
                  placeholder="Your name"
                  placeholderTextColor={colors.muted}
                  value={fullName}
                  onChangeText={setFullName}
                />
                <Text style={styles.label}>Phone number</Text>
                <TextInput
                  style={styles.input}
                  placeholder="+91 98765 43210"
                  placeholderTextColor={colors.muted}
                  keyboardType="phone-pad"
                  value={phone}
                  onChangeText={setPhone}
                />
                <Text style={styles.helper}>
                  Required so RAAHI can contact you, and so your emergency contacts
                  know who is calling.
                </Text>
              </>
            ) : null}

            <PrimaryButton
              label={signingUp ? 'Create account' : 'Sign in'}
              busy={busy}
              onPress={handleEmail}
            />
            <TouchableOpacity
              onPress={() => {
                setSigningUp((prev) => !prev);
                setError('');
              }}
              style={styles.linkBtn}
            >
              <Text style={styles.linkText}>
                {signingUp ? 'I already have an account' : 'Create a new account'}
              </Text>
            </TouchableOpacity>
          </View>
        ) : null}

        {/* ── Demo mode ─────────────────────────────────── */}
        {!isAuthEnabled ? (
          <View style={styles.form}>
            <View style={styles.noticeBox}>
              <Text style={styles.noticeTitle}>Demo mode</Text>
              <Text style={styles.noticeBody}>
                Supabase is not configured in this build, so sign-in is unavailable.
                You can continue as the seeded demo traveller to explore planning,
                live tracking and SOS.
              </Text>
            </View>
            <PrimaryButton label="Continue in demo mode" busy={busy} onPress={handleDemo} />
            <Text style={styles.helper}>
              Set EXPO_PUBLIC_SUPABASE_URL and EXPO_PUBLIC_SUPABASE_ANON_KEY to
              enable real accounts.
            </Text>
          </View>
        ) : null}

        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function PrimaryButton({
  label,
  busy,
  onPress,
}: {
  label: string;
  busy: boolean;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      style={[styles.button, busy && styles.buttonDisabled]}
      onPress={onPress}
      disabled={busy}
      accessibilityRole="button"
    >
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>{label}</Text>}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.lg, paddingTop: 90, paddingBottom: spacing.xl },
  logo: {
    color: colors.accent,
    fontSize: 42,
    fontWeight: '900',
    letterSpacing: 4,
    marginBottom: spacing.sm,
  },
  tagline: {
    color: colors.textDim,
    fontSize: 15,
    lineHeight: 21,
    marginBottom: spacing.xl,
  },
  tabs: {
    flexDirection: 'row',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: 4,
    marginBottom: spacing.lg,
  },
  tab: { flex: 1, paddingVertical: 9, borderRadius: radius.sm, alignItems: 'center' },
  tabActive: { backgroundColor: colors.accent },
  tabText: { color: colors.muted, fontWeight: '600', fontSize: 13 },
  tabTextActive: { color: '#fff' },
  form: { gap: spacing.sm },
  label: { color: colors.textDim, fontSize: 12, fontWeight: '600', marginTop: spacing.sm },
  input: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: spacing.md,
    paddingVertical: 13,
    fontSize: 15,
  },
  otpInput: { fontSize: 24, letterSpacing: 8, textAlign: 'center' },
  helper: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  button: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: 15,
    alignItems: 'center',
    marginTop: spacing.md,
  },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  linkBtn: { alignItems: 'center', paddingVertical: spacing.sm },
  linkText: { color: colors.accent, fontSize: 13 },
  noticeBox: {
    backgroundColor: `${colors.info}1A`,
    borderLeftWidth: 3,
    borderLeftColor: colors.info,
    borderRadius: radius.sm,
    padding: spacing.md,
  },
  noticeTitle: { color: colors.info, fontWeight: '800', fontSize: 13, marginBottom: 4 },
  noticeBody: { color: colors.textDim, fontSize: 12, lineHeight: 18 },
  error: {
    color: colors.danger,
    fontSize: 13,
    marginTop: spacing.md,
    lineHeight: 18,
  },
});

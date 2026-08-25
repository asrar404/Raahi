/**
 * Profile and emergency contacts.
 *
 * Emergency contacts get top billing because they are the single setting that
 * determines whether SOS actually reaches anyone. A user with zero contacts
 * sees a prominent prompt rather than a quiet empty state — an SOS with nobody
 * to call is a silent failure.
 */

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import { colors, radius, spacing } from '../constants/colors';
import { fetchMe, updateEmergencyContacts, updateProfile } from '../services/api';
import { isAuthEnabled, signOut } from '../services/supabase';
import { useAuthStore } from '../store/authSlice';
import type { EmergencyContact } from '../store/types';

const MAX_CONTACTS = 5;

export default function ProfileScreen() {
  const user = useAuthStore((s) => s.user);
  const patchUser = useAuthStore((s) => s.patchUser);
  const setUser = useAuthStore((s) => s.setUser);

  const [contacts, setContacts] = useState<EmergencyContact[]>([]);
  const [newName, setNewName] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [newRelation, setNewRelation] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setContacts(user?.emergency_contacts ?? []);
  }, [user?.emergency_contacts]);

  // Refresh from the server on mount so contacts edited on another device
  // are reflected.
  useEffect(() => {
    void (async () => {
      try {
        const fresh = await fetchMe();
        setUser(fresh, useAuthStore.getState().token);
      } catch {
        // Offline: the persisted profile stays on screen.
      }
    })();
  }, [setUser]);

  const persistContacts = async (next: EmergencyContact[]) => {
    setSaving(true);
    try {
      const updated = await updateEmergencyContacts(next);
      setUser(updated, useAuthStore.getState().token);
      setContacts(updated.emergency_contacts);
    } catch (err) {
      Alert.alert(
        'Could not save',
        err instanceof Error ? err.message : 'Please try again.',
      );
      // Revert so the UI never shows a contact the server does not have —
      // that would imply SOS coverage the user does not actually have.
      setContacts(user?.emergency_contacts ?? []);
    } finally {
      setSaving(false);
    }
  };

  const addContact = () => {
    if (!newName.trim() || !newPhone.trim()) {
      Alert.alert('Missing details', 'A name and phone number are both required.');
      return;
    }
    if (contacts.length >= MAX_CONTACTS) {
      Alert.alert(
        'Limit reached',
        `You can save up to ${MAX_CONTACTS} contacts. Beyond that an alert stops being actionable.`,
      );
      return;
    }

    const next = [
      ...contacts,
      {
        name: newName.trim(),
        phone: newPhone.trim(),
        relation: newRelation.trim() || null,
      },
    ];
    setNewName('');
    setNewPhone('');
    setNewRelation('');
    void persistContacts(next);
  };

  const removeContact = (index: number) => {
    const target = contacts[index];
    if (!target) return;
    Alert.alert('Remove contact?', `${target.name} will no longer be alerted on SOS.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: () => void persistContacts(contacts.filter((_, i) => i !== index)),
      },
    ]);
  };

  const toggleSos = async (value: boolean) => {
    patchUser({ sos_enabled: value });
    try {
      const updated = await updateProfile({ sos_enabled: value });
      setUser(updated, useAuthStore.getState().token);
    } catch {
      patchUser({ sos_enabled: !value });
      Alert.alert('Could not update', 'Please try again.');
    }
  };

  const handleSignOut = () => {
    Alert.alert('Sign out?', 'You will need to sign in again to plan a trip.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: () => void signOut() },
    ]);
  };

  if (!user) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Profile</Text>

      {/* Identity */}
      <View style={styles.card}>
        <Text style={styles.name}>{user.full_name}</Text>
        <Text style={styles.detail}>{user.phone}</Text>
        {user.email ? <Text style={styles.detail}>{user.email}</Text> : null}
        {user.home_city ? <Text style={styles.detail}>{user.home_city}</Text> : null}
      </View>

      {/* SOS toggle */}
      <View style={styles.card}>
        <View style={styles.switchRow}>
          <View style={styles.switchLabel}>
            <Text style={styles.switchTitle}>SOS notifications</Text>
            <Text style={styles.switchBody}>
              Text and call your contacts when RAAHI detects danger.
            </Text>
          </View>
          <Switch
            value={user.sos_enabled}
            onValueChange={(value) => void toggleSos(value)}
            trackColor={{ false: colors.border, true: `${colors.accent}99` }}
            thumbColor={user.sos_enabled ? colors.accent : colors.muted}
          />
        </View>
      </View>

      {/* Emergency contacts */}
      <Text style={styles.sectionLabel}>EMERGENCY CONTACTS</Text>

      {!contacts.length ? (
        <View style={styles.alertBox}>
          <Text style={styles.alertTitle}>No contacts saved</Text>
          <Text style={styles.alertBody}>
            RAAHI has nobody to alert if you trigger an SOS. Add at least one person
            you trust.
          </Text>
        </View>
      ) : null}

      {contacts.map((contact, index) => (
        <View key={`${contact.phone}-${index}`} style={styles.contactRow}>
          <View style={styles.contactMain}>
            <Text style={styles.contactName}>{contact.name}</Text>
            <Text style={styles.contactPhone}>
              {contact.phone}
              {contact.relation ? ` · ${contact.relation}` : ''}
            </Text>
          </View>
          <TouchableOpacity
            onPress={() => removeContact(index)}
            style={styles.removeBtn}
            accessibilityRole="button"
            accessibilityLabel={`Remove ${contact.name}`}
          >
            <Text style={styles.removeText}>Remove</Text>
          </TouchableOpacity>
        </View>
      ))}

      {contacts.length < MAX_CONTACTS ? (
        <View style={styles.addCard}>
          <TextInput
            style={styles.input}
            placeholder="Name"
            placeholderTextColor={colors.muted}
            value={newName}
            onChangeText={setNewName}
          />
          <TextInput
            style={styles.input}
            placeholder="+91 98765 43210"
            placeholderTextColor={colors.muted}
            keyboardType="phone-pad"
            value={newPhone}
            onChangeText={setNewPhone}
          />
          <TextInput
            style={styles.input}
            placeholder="Relation (optional)"
            placeholderTextColor={colors.muted}
            value={newRelation}
            onChangeText={setNewRelation}
          />
          <TouchableOpacity
            style={[styles.addBtn, saving && styles.addBtnDisabled]}
            onPress={addContact}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.addBtnText}>Add contact</Text>
            )}
          </TouchableOpacity>
        </View>
      ) : null}

      {/* Preferences */}
      <Text style={styles.sectionLabel}>PREFERENCES</Text>
      <View style={styles.card}>
        <View style={styles.prefRow}>
          <Text style={styles.prefLabel}>Default budget</Text>
          <Text style={styles.prefValue}>₹{user.budget_default.toFixed(0)}</Text>
        </View>
        <View style={styles.prefRow}>
          <Text style={styles.prefLabel}>Preferred modes</Text>
          <Text style={styles.prefValue}>
            {user.preferred_modes.join(', ') || '—'}
          </Text>
        </View>
      </View>

      {isAuthEnabled ? (
        <TouchableOpacity style={styles.signOutBtn} onPress={handleSignOut}>
          <Text style={styles.signOutText}>Sign out</Text>
        </TouchableOpacity>
      ) : (
        <Text style={styles.demoNote}>
          Running in demo mode. Configure Supabase to enable real accounts.
        </Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingTop: 56, paddingBottom: spacing.xl },
  centered: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: { color: colors.text, fontSize: 30, fontWeight: '900', marginBottom: spacing.md },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  name: { color: colors.text, fontSize: 19, fontWeight: '800' },
  detail: { color: colors.textDim, fontSize: 13, marginTop: 4 },
  switchRow: { flexDirection: 'row', alignItems: 'center' },
  switchLabel: { flex: 1, paddingRight: spacing.md },
  switchTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  switchBody: { color: colors.muted, fontSize: 12, marginTop: 3, lineHeight: 17 },
  sectionLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.8,
    marginBottom: spacing.sm,
    marginTop: spacing.sm,
  },
  alertBox: {
    backgroundColor: `${colors.danger}1A`,
    borderLeftWidth: 3,
    borderLeftColor: colors.danger,
    borderRadius: radius.sm,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  alertTitle: { color: colors.danger, fontWeight: '800', fontSize: 13 },
  alertBody: { color: colors.textDim, fontSize: 12, marginTop: 4, lineHeight: 18 },
  contactRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.sm + 2,
    marginBottom: spacing.sm,
  },
  contactMain: { flex: 1 },
  contactName: { color: colors.text, fontSize: 15, fontWeight: '700' },
  contactPhone: { color: colors.muted, fontSize: 12, marginTop: 2 },
  removeBtn: { paddingHorizontal: spacing.sm, paddingVertical: 6 },
  removeText: { color: colors.danger, fontSize: 12, fontWeight: '600' },
  addCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
    gap: spacing.sm,
  },
  input: {
    backgroundColor: colors.bg,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: 11,
    fontSize: 14,
  },
  addBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: 13,
    alignItems: 'center',
  },
  addBtnDisabled: { opacity: 0.6 },
  addBtnText: { color: '#fff', fontWeight: '800', fontSize: 14 },
  prefRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 6,
  },
  prefLabel: { color: colors.textDim, fontSize: 13 },
  prefValue: { color: colors.text, fontSize: 13, fontWeight: '600', textTransform: 'capitalize' },
  signOutBtn: {
    borderWidth: 1,
    borderColor: colors.danger,
    borderRadius: radius.md,
    paddingVertical: 13,
    alignItems: 'center',
    marginTop: spacing.md,
  },
  signOutText: { color: colors.danger, fontWeight: '700', fontSize: 14 },
  demoNote: {
    color: colors.muted,
    fontSize: 12,
    textAlign: 'center',
    marginTop: spacing.md,
    lineHeight: 17,
  },
});

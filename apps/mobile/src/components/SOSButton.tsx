/**
 * SOS button.
 *
 * Deliberate design choices for a panic button:
 *
 * - **Large and fixed.** 76pt, bottom-right, always in the same place, so it
 *   can be found without looking.
 * - **Confirmation required.** A single tap does not alert anyone. An
 *   accidental SOS that wakes a parent at 2am erodes trust in the feature,
 *   and a user who stops trusting it stops relying on it.
 * - **Pulses when active.** Continuous visual feedback that the alert is live,
 *   without needing to read anything.
 * - **Honest about Twilio.** If notifications are not configured, the
 *   confirmation says so rather than implying contacts were reached.
 */

import React, { useEffect, useRef } from 'react';
import { Alert, Animated, Easing, StyleSheet, Text, TouchableOpacity } from 'react-native';

import { colors, radius } from '../constants/colors';
import { useSOS } from '../hooks/useSOS';
import { useAuthStore } from '../store/authSlice';

export default function SOSButton() {
  const { sosActive, sending, trigger, resolve } = useSOS();
  const contacts = useAuthStore((s) => s.user?.emergency_contacts ?? []);
  const pulse = useRef(new Animated.Value(1)).current;

  // Pulse only while active, and stop cleanly otherwise — a permanently
  // animating view keeps the JS thread busy for no reason.
  useEffect(() => {
    if (!sosActive) {
      pulse.setValue(1);
      return;
    }

    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1.18,
          duration: 600,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 1,
          duration: 600,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [sosActive, pulse]);

  const confirmAndSend = () => {
    const contactLine = contacts.length
      ? `${contacts.length} emergency contact${contacts.length > 1 ? 's' : ''} will be called and texted your live location.`
      : 'You have no emergency contacts saved. Add them in Profile so RAAHI can reach someone for you.';

    Alert.alert(
      'Send SOS?',
      `${contactLine}\n\nOnly use this if you feel unsafe.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Send SOS',
          style: 'destructive',
          onPress: async () => {
            const outcome = await trigger();

            if (!outcome.ok) {
              Alert.alert(
                'SOS could not be sent',
                `${outcome.error ?? 'Unknown error'}\n\nCall someone directly if you are in danger.`,
              );
              return;
            }
            if (outcome.alreadyActive) return;

            const lines: string[] = [];
            if (outcome.twilioEnabled) {
              lines.push(
                `${outcome.smsSent ?? 0} message(s) sent, ${outcome.callsPlaced ?? 0} call(s) placed.`,
              );
            } else {
              // Never imply help was summoned when it was not.
              lines.push(
                'Notifications are not configured on this server, so no messages were actually sent.',
              );
            }
            if (outcome.refuges?.length) {
              const nearest = outcome.refuges[0];
              if (nearest) {
                lines.push(
                  `Nearest safe place: ${nearest.zone_name}, ${Math.round(nearest.distance_m)} m away.`,
                );
              }
            }
            Alert.alert('SOS active', lines.join('\n\n'));
          },
        },
      ],
      { cancelable: true },
    );
  };

  const confirmResolve = () => {
    Alert.alert('Cancel SOS?', 'Only do this if you are safe.', [
      { text: 'Keep active', style: 'cancel' },
      { text: "I'm safe", onPress: () => void resolve() },
    ]);
  };

  return (
    <Animated.View
      style={[styles.wrapper, { transform: [{ scale: pulse }] }]}
      pointerEvents="box-none"
    >
      <TouchableOpacity
        style={[styles.btn, sosActive && styles.btnActive]}
        onPress={sosActive ? confirmResolve : confirmAndSend}
        disabled={sending}
        activeOpacity={0.85}
        accessibilityRole="button"
        accessibilityLabel={sosActive ? 'SOS is active. Tap to cancel.' : 'Send SOS'}
        accessibilityHint="Alerts your emergency contacts with your live location"
      >
        <Text style={styles.label}>
          {sending ? '...' : sosActive ? 'SOS\nACTIVE' : 'SOS'}
        </Text>
      </TouchableOpacity>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    position: 'absolute',
    bottom: 36,
    right: 20,
  },
  btn: {
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: colors.danger,
    alignItems: 'center',
    justifyContent: 'center',
    elevation: 10,
    shadowColor: colors.danger,
    shadowRadius: 14,
    shadowOpacity: 0.6,
    shadowOffset: { width: 0, height: 4 },
    borderWidth: 3,
    borderColor: 'rgba(255,255,255,0.25)',
  },
  btnActive: {
    backgroundColor: colors.dangerDark,
    width: 96,
    height: 76,
    borderRadius: radius.lg,
  },
  label: {
    color: '#fff',
    fontWeight: '900',
    fontSize: 15,
    textAlign: 'center',
    letterSpacing: 0.5,
  },
});

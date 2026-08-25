/**
 * SOS hook.
 *
 * Wraps the escalation call with the guardrails that matter for a panic button:
 *
 * - **Optimistic UI.** `sosActive` flips before the network call returns. When
 *   someone presses this, the button must visibly respond immediately.
 * - **Local cooldown.** Repeat presses inside the window are suppressed so the
 *   user cannot SMS their contacts five times by tapping. The gateway dedupes
 *   too; this just avoids the round trips.
 * - **Honest failure.** If the call fails the SOS state is kept, but the caller
 *   is told, so the UI can offer a direct phone call instead of implying help
 *   is on the way.
 */

import { useCallback, useRef, useState } from 'react';

import { resolveSOS, sendSOS } from '../services/api';
import { getCurrentFix } from '../services/location';
import { useSafetyStore } from '../store/safetySlice';
import { useTripStore } from '../store/tripSlice';

/** Minimum gap between manual SOS presses, ms. */
const COOLDOWN_MS = 30_000;

export interface SosOutcome {
  ok: boolean;
  alreadyActive?: boolean;
  contactsAlerted?: number;
  smsSent?: number;
  callsPlaced?: number;
  twilioEnabled?: boolean;
  refuges?: { zone_name: string; distance_m: number }[];
  error?: string;
}

export function useSOS(): {
  sosActive: boolean;
  sending: boolean;
  lastOutcome: SosOutcome | null;
  trigger: () => Promise<SosOutcome>;
  resolve: () => Promise<boolean>;
} {
  const sosActive = useSafetyStore((s) => s.sosActive);
  const sending = useSafetyStore((s) => s.sosSending);
  const [lastOutcome, setLastOutcome] = useState<SosOutcome | null>(null);
  const lastPress = useRef<number>(0);

  const trigger = useCallback(async (): Promise<SosOutcome> => {
    const now = Date.now();
    if (now - lastPress.current < COOLDOWN_MS) {
      const outcome: SosOutcome = { ok: true, alreadyActive: true };
      setLastOutcome(outcome);
      return outcome;
    }
    lastPress.current = now;

    const safety = useSafetyStore.getState();
    const trip = useTripStore.getState();

    safety.setSosSending(true);
    // Optimistic: the button must react instantly.
    safety.triggerSOS();

    try {
      // A fresh fix is worth a short wait — an accurate location is the single
      // most useful thing an emergency contact receives. The gateway falls back
      // to the last stored telemetry point if this comes back null.
      const fix = await getCurrentFix();

      const result = await sendSOS({
        trip_id: trip.activeTrip?.id ?? null,
        lat: fix?.lat,
        lon: fix?.lon,
        trigger_source: 'manual',
      });

      safety.triggerSOS(result.sos_event_id ?? null);
      if (result.safe_refuges?.length) {
        safety.setRefuges(
          result.safe_refuges.map((r) => ({
            zone_id: '',
            zone_name: r.zone_name,
            risk_score: 1,
            distance_m: r.distance_m,
          })),
        );
      }
      safety.addAlert({
        type: 'sos',
        message: result.already_active
          ? 'SOS already active — your contacts have been alerted.'
          : `SOS sent. ${result.sms_sent} message(s), ${result.calls_placed} call(s) placed.`,
        lat: fix?.lat,
        lon: fix?.lon,
      });

      const outcome: SosOutcome = {
        ok: true,
        alreadyActive: result.already_active,
        contactsAlerted: result.contacts_alerted,
        smsSent: result.sms_sent,
        callsPlaced: result.calls_placed,
        twilioEnabled: result.twilio_enabled,
        refuges: result.safe_refuges,
      };
      setLastOutcome(outcome);
      return outcome;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not send SOS';
      // sosActive stays true deliberately — the user pressed it, and the UI
      // should keep showing an emergency state while offering a fallback.
      safety.addAlert({
        type: 'sos',
        message: `SOS could not be sent: ${message}. Call your contacts directly.`,
      });
      const outcome: SosOutcome = { ok: false, error: message };
      setLastOutcome(outcome);
      return outcome;
    } finally {
      useSafetyStore.getState().setSosSending(false);
    }
  }, []);

  const resolve = useCallback(async (): Promise<boolean> => {
    const trip = useTripStore.getState();
    const safety = useSafetyStore.getState();

    if (!trip.activeTrip?.id) {
      safety.resetSOS();
      return true;
    }

    try {
      await resolveSOS(trip.activeTrip.id);
      safety.resetSOS();
      lastPress.current = 0;
      return true;
    } catch {
      // Clear locally regardless: the user has said they are safe, and leaving
      // the alarm on screen because a request failed is not helpful.
      safety.resetSOS();
      return false;
    }
  }, []);

  return { sosActive, sending, lastOutcome, trigger, resolve };
}

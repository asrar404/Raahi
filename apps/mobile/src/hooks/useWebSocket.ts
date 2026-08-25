/**
 * Live trip WebSocket hook.
 *
 * Owns one `TripSocket` per trip and maps server events onto the safety and
 * trip stores.
 *
 * The socket is held in a ref and the effect depends only on `tripId`, so it is
 * created once per trip. Depending on the store actions instead would tear down
 * and rebuild the socket on every state change — losing telemetry continuity
 * several times a minute.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  type TelemetryPayload,
  TripSocket,
  type WsStatus,
} from '../services/websocket';
import { useAuthStore } from '../store/authSlice';
import { useSafetyStore } from '../store/safetySlice';
import { useTripStore } from '../store/tripSlice';
import type { PlannedRoute, RiskZone, SafeRefuge, WsMessage } from '../store/types';

interface UseTripWebSocketResult {
  status: WsStatus;
  sendTelemetry: (payload: TelemetryPayload) => boolean;
  queued: number;
  reconnect: () => void;
}

export function useTripWebSocket(tripId: string | null): UseTripWebSocketResult {
  const socketRef = useRef<TripSocket | null>(null);
  const [status, setStatus] = useState<WsStatus>('idle');
  const [queued, setQueued] = useState(0);

  useEffect(() => {
    if (!tripId) {
      socketRef.current?.close();
      socketRef.current = null;
      setStatus('idle');
      return;
    }

    const handleMessage = (message: WsMessage) => {
      // Store actions are read via getState() rather than closed over, so this
      // handler never needs to be rebuilt.
      const safety = useSafetyStore.getState();
      const trip = useTripStore.getState();
      const data = message.data as Record<string, unknown>;

      switch (message.event) {
        case 'SOS_ALERT': {
          const risk = (data.risk ?? {}) as {
            max_risk?: number;
            risk_zones?: RiskZone[];
            safety_score?: number;
          };
          const refuges = (data.refuges ?? []) as SafeRefuge[];
          const location = (data.location ?? {}) as { lat?: number; lon?: number };

          safety.triggerSOS();
          safety.setRisk(risk.max_risk ?? 5, true, risk.safety_score ?? null);
          safety.setZones(risk.risk_zones ?? []);
          safety.setRefuges(refuges);
          safety.addAlert({
            type: 'sos',
            message: String(data.message ?? 'High risk area detected.'),
            severity: risk.max_risk ?? 5,
            lat: location.lat,
            lon: location.lon,
          });
          break;
        }

        case 'SOS_RESOLVED':
          safety.resetSOS();
          safety.addAlert({ type: 'info', message: 'SOS cleared.' });
          break;

        case 'REROUTE': {
          const routes = (data.new_routes ?? []) as PlannedRoute[];
          if (routes.length) {
            // Held as pending rather than applied: silently swapping someone's
            // route mid-journey is disorienting. RerouteModal asks first.
            safety.setPendingReroute(routes, String(data.trigger ?? 'manual'));
            safety.addAlert({
              type: 'reroute',
              message: `${routes.length} alternative route${routes.length > 1 ? 's' : ''} available.`,
            });
          }
          break;
        }

        case 'RISK_UPDATE': {
          const level = Number(data.risk_level ?? 0);
          const inZone = Boolean(data.in_risk_zone);
          const score = data.safety_score == null ? null : Number(data.safety_score);
          safety.setRisk(level, inZone, score);
          safety.setZones((data.zones ?? []) as RiskZone[]);
          break;
        }

        case 'OFF_ROUTE':
          safety.setOffRoute(true);
          safety.addAlert({
            type: 'off_route',
            message: String(data.message ?? 'You are off your planned route.'),
            lat: data.lat as number | undefined,
            lon: data.lon as number | undefined,
          });
          break;

        case 'BACK_ON_ROUTE':
          safety.setOffRoute(false);
          safety.addAlert({ type: 'info', message: 'Back on route.' });
          break;

        case 'BUDGET_ALERT':
          safety.addAlert({
            type: 'budget',
            message:
              data.severity === 'critical'
                ? `Over budget by ₹${Math.abs(Number(data.remaining ?? 0)).toFixed(0)}.`
                : `${Number(data.percent_used ?? 0).toFixed(0)}% of your budget used.`,
          });
          break;

        case 'LEG_ADVANCED': {
          const legOrder = Number(data.leg_order ?? 0);
          trip.setActiveLegIdx(legOrder);
          break;
        }

        case 'TRIP_COMPLETED':
          safety.addAlert({ type: 'info', message: 'Trip complete. Stay safe.' });
          break;

        case 'TELEMETRY_ACK':
        case 'PONG':
          // Nothing to surface.
          break;

        case 'ERROR':
          safety.addAlert({
            type: 'info',
            message: String(data.message ?? 'Server reported an error.'),
          });
          break;

        default:
          break;
      }
    };

    const socket = new TripSocket({
      tripId,
      token: useAuthStore.getState().token,
      onMessage: handleMessage,
      onStatusChange: (next) => {
        setStatus(next);
        setQueued(socketRef.current?.queuedCount() ?? 0);
      },
    });

    socketRef.current = socket;
    socket.connect();

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [tripId]);

  const sendTelemetry = useCallback((payload: TelemetryPayload): boolean => {
    const socket = socketRef.current;
    if (!socket) return false;
    const sent = socket.sendTelemetry(payload);
    setQueued(socket.queuedCount());
    return sent;
  }, []);

  const reconnect = useCallback(() => {
    socketRef.current?.connect();
  }, []);

  return { status, sendTelemetry, queued, reconnect };
}

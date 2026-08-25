/**
 * WebSocket transport for the live trip channel.
 *
 * React Native's WebSocket cannot set custom headers on the handshake, so the
 * auth token travels as a query parameter — which is what the gateway's
 * `/ws/trip/{trip_id}` endpoint expects.
 *
 * Reconnection uses exponential backoff with jitter. Without jitter, every
 * client that dropped during a brief server restart reconnects in lockstep and
 * knocks it over again.
 *
 * Telemetry sent while the socket is down is queued, not discarded: those fixes
 * are what drive risk evaluation, and losing the last minute of someone's
 * location is exactly the wrong thing to do.
 */

import { API_WS_URL, telemetry } from '../constants/config';
import type { WsMessage } from '../store/types';

export type WsStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'failed';

export interface TelemetryPayload {
  lat: number;
  lon: number;
  speed?: number;
  accuracy?: number;
  heading?: number;
  altitude?: number;
  battery?: number;
}

interface TripSocketOptions {
  tripId: string;
  token?: string | null;
  onMessage: (message: WsMessage) => void;
  onStatusChange?: (status: WsStatus) => void;
}

/** Queued fixes are dropped past this many, oldest first. */
const MAX_QUEUE = 20;

export class TripSocket {
  private socket: WebSocket | null = null;
  private readonly opts: TripSocketOptions;
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private queue: TelemetryPayload[] = [];
  private closedByUs = false;
  private status: WsStatus = 'idle';

  constructor(opts: TripSocketOptions) {
    this.opts = opts;
  }

  private setStatus(status: WsStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.opts.onStatusChange?.(status);
  }

  getStatus(): WsStatus {
    return this.status;
  }

  private url(): string {
    const { tripId, token } = this.opts;
    const query = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${API_WS_URL}/ws/trip/${encodeURIComponent(tripId)}${query}`;
  }

  connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN ||
                        this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.closedByUs = false;
    this.setStatus('connecting');

    const socket = new WebSocket(this.url());
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.setStatus('open');
      // Anything buffered while offline goes out first, in order.
      this.flushQueue();
    };

    socket.onmessage = (event: WebSocketMessageEvent) => {
      try {
        const parsed = JSON.parse(String(event.data)) as WsMessage;
        if (parsed && typeof parsed.event === 'string') {
          this.opts.onMessage(parsed);
        }
      } catch {
        // A malformed frame is not worth tearing down a live trip's socket.
      }
    };

    socket.onerror = () => {
      // onclose always follows, so reconnection is handled there.
    };

    socket.onclose = () => {
      this.socket = null;
      if (this.closedByUs) {
        this.setStatus('closed');
        return;
      }
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.attempts >= telemetry.maxReconnectAttempts) {
      this.setStatus('failed');
      return;
    }

    this.attempts += 1;
    const base = Math.min(
      telemetry.reconnectBaseMs * 2 ** (this.attempts - 1),
      telemetry.reconnectMaxMs,
    );
    // Jitter prevents a thundering herd after a server restart
    const delay = base * (0.5 + Math.random() * 0.5);

    this.setStatus('connecting');
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private flushQueue(): void {
    if (!this.queue.length) return;
    const pending = this.queue;
    this.queue = [];
    for (const payload of pending) {
      this.sendTelemetry(payload);
    }
  }

  sendTelemetry(payload: TelemetryPayload): boolean {
    const frame = JSON.stringify({ type: 'TELEMETRY', ...payload });

    if (this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(frame);
        return true;
      } catch {
        // Fall through and queue it
      }
    }

    this.queue.push(payload);
    if (this.queue.length > MAX_QUEUE) {
      // Keep the newest: an old position is less useful than a current one.
      this.queue = this.queue.slice(-MAX_QUEUE);
    }
    return false;
  }

  ping(): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'PING' }));
    }
  }

  queuedCount(): number {
    return this.queue.length;
  }

  close(): void {
    this.closedByUs = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        // Already closing
      }
      this.socket = null;
    }
    this.setStatus('closed');
  }
}

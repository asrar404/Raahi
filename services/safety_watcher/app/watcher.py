"""Safety watcher orchestration.

Consumes telemetry, drives one state machine per trip, and escalates:

    HIGH_RISK_ZONE  -> SOS (contacts alerted, trip flagged)
    OFF_ROUTE       -> reroute offer, after OFF_ROUTE_STRIKES consecutive fixes
    STATIONARY      -> reroute offer, after STATIONARY_REROUTE_SECS

Escalation fires on **state transitions**, not on state. Someone standing in a
high-risk zone for ten minutes is one incident, not forty; keying off steady
state would SMS their emergency contacts on every fix.

Entries are acknowledged only after processing completes, so a crash
mid-evaluation leaves the fix pending for redelivery rather than dropping it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.config import settings
from app.geofence_evaluator import evaluate, load_trip_context
from app.sos_pipeline import trigger_reroute, trigger_sos
from app.state_machine import TripContext, TripState
from app.telemetry_consumer import TelemetryConsumer, TelemetryFix

log = logging.getLogger(__name__)


class SafetyWatcher:
    def __init__(self) -> None:
        self.active_trips: dict[str, TripContext] = {}
        self.consumer = TelemetryConsumer()
        self._running = False
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.stats = {
            "fixes_processed": 0,
            "sos_triggered": 0,
            "reroutes_offered": 0,
            "errors": 0,
        }

    # ── Lifecycle ───────────────────────────────────────────
    async def start(self) -> None:
        """Run the consume loop until stopped.

        Reconnects with backoff on Redis failure rather than exiting: this
        service going quiet means nobody is watching live trips.
        """
        self._running = True
        backoff = 1.0

        while self._running and not self._stop.is_set():
            try:
                await self.consumer.connect()
                backoff = 1.0
                log.info("Safety watcher listening on %s", settings.TELEMETRY_STREAM)

                self._tasks.append(asyncio.create_task(self._janitor()))

                while self._running and not self._stop.is_set():
                    async for fix in self.consumer.read():
                        try:
                            await self._process(fix)
                            self.stats["fixes_processed"] += 1
                        except Exception as exc:  # noqa: BLE001
                            # One bad fix must not stop the stream for others.
                            self.stats["errors"] += 1
                            log.exception("Failed to process fix for trip %s: %s",
                                          fix.trip_id, exc)
                        finally:
                            # Acknowledge either way: a fix that reliably
                            # crashes processing would otherwise be redelivered
                            # forever and block the group.
                            await self.consumer.ack(fix.entry_id)

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"] += 1
                log.error("Watcher loop error (retrying in %.0fs): %s", backoff, exc)
                await self.consumer.close()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

        await self.consumer.close()
        log.info("Safety watcher stopped")

    async def stop(self) -> None:
        self._running = False
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        await self.consumer.close()

    # ── Per-fix processing ──────────────────────────────────
    async def _context_for(self, fix: TelemetryFix) -> Optional[TripContext]:
        """Fetch or create the context for a trip.

        Contacts normally ride along on the stream so an SOS needs no extra
        round trip. When absent (an older producer, or a fix published before
        contacts were cached) they are loaded from the database once.
        """
        ctx = self.active_trips.get(fix.trip_id)

        if ctx is None:
            ctx = TripContext(
                trip_id=fix.trip_id,
                user_id=fix.user_id,
                contacts=list(fix.contacts or []),
            )

            if not ctx.contacts:
                details = await load_trip_context(fix.trip_id)
                if details is None:
                    log.warning("Telemetry for unknown trip %s; ignoring", fix.trip_id)
                    return None
                if details["status"] in ("completed", "cancelled"):
                    log.info("Telemetry for %s trip %s; ignoring",
                             details["status"], fix.trip_id)
                    return None
                ctx.contacts = details.get("emergency_contacts") or []
                if not details.get("sos_enabled", True):
                    log.info("[%s] SOS notifications disabled for this user", fix.trip_id)

            self.active_trips[fix.trip_id] = ctx
            log.info("Now watching trip %s (%d contacts known)",
                     fix.trip_id, len(ctx.contacts))
        else:
            # Refresh contacts if the stream carries newer ones
            if fix.contacts:
                ctx.contacts = list(fix.contacts)

        ctx.spent_budget = fix.spent
        return ctx

    async def _process(self, fix: TelemetryFix) -> None:
        """Evaluate one fix and escalate if the state transition warrants it."""
        ctx = await self._context_for(fix)
        if ctx is None:
            return

        result = await evaluate(ctx, fix.lat, fix.lon)

        # ── Trip finished ───────────────────────────────────
        if ctx.state is TripState.COMPLETED:
            log.info("Trip %s finished (%s); releasing context",
                     ctx.trip_id, result.get("trip_status"))
            self.active_trips.pop(ctx.trip_id, None)
            return

        # ── SOS: entering a high-risk zone ──────────────────
        if result["in_high_risk"]:
            outcome = await trigger_sos(ctx, fix.lat, fix.lon, result)
            if not outcome.get("skipped") and not outcome.get("error"):
                self.stats["sos_triggered"] += 1
            # An SOS supersedes any reroute this fix might have prompted.
            return

        # ── Reroute: sustained deviation ────────────────────
        if ctx.state is TripState.OFF_ROUTE and result["state_changed"]:
            outcome = await trigger_reroute(
                ctx, fix.lat, fix.lon, fix.spent, "off_route"
            )
            if outcome.get("routes"):
                self.stats["reroutes_offered"] += 1
            return

        # ── Reroute: stalled ────────────────────────────────
        if (
            ctx.state is TripState.STATIONARY
            and result["stationary_secs"] > settings.STATIONARY_REROUTE_SECS
        ):
            outcome = await trigger_reroute(
                ctx, fix.lat, fix.lon, fix.spent, "delay"
            )
            if outcome.get("routes"):
                self.stats["reroutes_offered"] += 1

    # ── Housekeeping ────────────────────────────────────────
    async def _janitor(self) -> None:
        """Drop contexts for trips that have stopped reporting.

        A phone that dies or loses signal mid-trip would otherwise leave its
        context in memory indefinitely.
        """
        while self._running and not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=settings.JANITOR_INTERVAL_SECS
                )
                return  # stop was set
            except asyncio.TimeoutError:
                pass

            stale = [
                trip_id for trip_id, ctx in self.active_trips.items()
                if ctx.is_stale(settings.TRIP_CONTEXT_TTL_SECS)
            ]
            for trip_id in stale:
                ctx = self.active_trips.pop(trip_id, None)
                if ctx is not None:
                    log.info("Releasing stale context for trip %s (last seen %s)",
                             trip_id, ctx.last_seen_at.isoformat())

            if stale:
                log.info("Janitor released %d stale trip context(s)", len(stale))

    # ── Introspection ───────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "watched_trips": len(self.active_trips),
            "stats": dict(self.stats),
            "trips": [ctx.snapshot() for ctx in self.active_trips.values()],
        }

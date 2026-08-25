"""SOS and reroute escalation.

Both pipelines talk to the gateway over HTTP with the internal service token.
The watcher deliberately does not send SMS itself or write to the SOS audit
table directly: the gateway owns Twilio credentials and the `sos_events`
table, and duplicating that here would mean two code paths capable of
notifying someone's emergency contacts.

A single shared `httpx.AsyncClient` is reused for connection pooling. Building
one per escalation adds a TCP and TLS handshake to the critical path of an
emergency.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings
from app.state_machine import TripContext

log = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.INTERNAL_API_KEY:
        headers["X-Internal-Token"] = settings.INTERNAL_API_KEY
    return headers


async def init_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=settings.HTTP_TIMEOUT_SECONDS,
            headers=_headers(),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        log.info("HTTP client ready (backend=%s ai=%s)",
                 settings.BACKEND_URL, settings.AI_ENGINE_URL)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialised")
    return _client


# ============================================================
# SOS
# ============================================================
async def trigger_sos(
    ctx: TripContext,
    lat: float,
    lon: float,
    risk_info: dict[str, Any],
) -> dict[str, Any]:
    """Escalate to SOS via the gateway.

    Idempotent at two levels: the context's `may_trigger_sos` gate here, and
    the gateway's own dedupe window. Both matter — the local gate avoids the
    request entirely, and the remote one covers a watcher restart that lost
    its in-memory state.
    """
    if not ctx.may_trigger_sos(settings.SOS_RETRY_AFTER_SECS):
        log.debug("[%s] SOS suppressed: already active", ctx.trip_id)
        return {"skipped": True, "reason": "already_active"}

    zone_name = None
    zones = risk_info.get("risk_zones") or []
    if zones:
        zone_name = zones[0].get("zone_name")

    log.warning(
        "[%s] ESCALATING SOS at (%.5f, %.5f) zone=%s risk=%s",
        ctx.trip_id, lat, lon, zone_name, risk_info.get("max_risk"),
    )

    # Mark before the request so a slow or failed call cannot produce a
    # duplicate escalation from the next fix fifteen seconds later.
    ctx.mark_sos()

    payload = {
        "trip_id": ctx.trip_id,
        "user_id": ctx.user_id,
        "lat": lat,
        "lon": lon,
        "trigger_source": "watcher",
        "risk_info": {
            "max_risk": risk_info.get("max_risk"),
            "risk_zones": zones,
            "safe_refuges": risk_info.get("safe_refuges", []),
            "safety_score": risk_info.get("safety_score"),
            "night_mode": risk_info.get("night_mode"),
            "state": risk_info.get("state"),
        },
        "notify_contacts": True,
        "message": (
            f"You have entered {zone_name}. Move to a safer area."
            if zone_name else "You have entered a high-risk area. Stay alert."
        ),
    }

    try:
        response = await _get_client().post(
            f"{settings.BACKEND_URL}/api/v1/safety/sos", json=payload
        )
        response.raise_for_status()
        result = response.json()
        log.warning(
            "[%s] SOS delivered: event=%s sms=%s calls=%s subscribers=%s",
            ctx.trip_id, result.get("sos_event_id"), result.get("sms_sent"),
            result.get("calls_placed"), result.get("subscribers_notified"),
        )
        return result
    except httpx.HTTPStatusError as exc:
        log.error("[%s] SOS rejected by gateway (%s): %s",
                  ctx.trip_id, exc.response.status_code, exc.response.text[:300])
        # Allow a retry: the escalation did not actually happen.
        ctx.sos_triggered_at = None
        return {"error": f"gateway returned {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] SOS request failed: %s", ctx.trip_id, exc)
        ctx.sos_triggered_at = None
        return {"error": str(exc)}


async def notify_contacts_directly(
    ctx: TripContext, lat: float, lon: float, zone_name: Optional[str] = None
) -> dict[str, Any]:
    """Notify emergency contacts without the full SOS flow.

    A fallback for when /safety/sos itself is failing but notification might
    still get through. Contacts are sent inline from the cached stream payload.
    """
    if not ctx.contacts:
        return {"skipped": True, "reason": "no_contacts"}

    try:
        response = await _get_client().post(
            f"{settings.BACKEND_URL}/api/v1/safety/notify-contacts",
            json={
                "trip_id": ctx.trip_id,
                "user_id": ctx.user_id,
                "lat": lat,
                "lon": lon,
                "contacts": ctx.contacts,
                "zone_name": zone_name,
                "voice": True,
            },
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] Direct contact notification failed: %s", ctx.trip_id, exc)
        return {"error": str(exc)}


# ============================================================
# Reroute
# ============================================================
async def trigger_reroute(
    ctx: TripContext,
    lat: float,
    lon: float,
    spent: float,
    trigger: str,
) -> dict[str, Any]:
    """Fetch the trip's intent, replan via the AI engine, broadcast the result.

    Three chained calls. Each failure is reported distinctly, because "the trip
    has no stored intent" and "the AI engine is down" need different fixes.
    """
    if not ctx.may_reroute(settings.REROUTE_COOLDOWN_SECS):
        log.debug("[%s] reroute suppressed by cooldown", ctx.trip_id)
        return {"skipped": True, "reason": "cooldown"}

    ctx.mark_reroute()
    client = _get_client()

    # ── 1. Original intent ──────────────────────────────────
    try:
        response = await client.get(
            f"{settings.BACKEND_URL}/api/v1/trips/{ctx.trip_id}/intent"
        )
        response.raise_for_status()
        intent = response.json()
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] Could not fetch trip intent: %s", ctx.trip_id, exc)
        return {"error": f"intent fetch failed: {exc}"}

    if not intent or not intent.get("destination_raw"):
        log.warning("[%s] Trip has no usable intent; cannot reroute", ctx.trip_id)
        return {"error": "trip has no stored intent"}

    # ── 2. Replan ───────────────────────────────────────────
    try:
        response = await client.post(
            f"{settings.AI_ENGINE_URL}/reroute",
            json={
                "trip_id": ctx.trip_id,
                "intent": intent,
                "current_lat": lat,
                "current_lon": lon,
                "elapsed_mins": 0,
                "spent_budget": spent,
                "trigger": trigger,
            },
        )
        response.raise_for_status()
        replan = response.json()
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] AI engine reroute failed: %s", ctx.trip_id, exc)
        return {"error": f"replan failed: {exc}"}

    new_routes = replan.get("new_routes") or []
    if not new_routes:
        log.info("[%s] Reroute produced no alternatives (%s)",
                 ctx.trip_id, replan.get("error"))
        return {"routes": 0, "error": replan.get("error")}

    # ── 3. Push to the traveller ────────────────────────────
    try:
        response = await client.post(
            f"{settings.BACKEND_URL}/api/v1/safety/reroute",
            json={
                "trip_id": ctx.trip_id,
                "new_routes": new_routes,
                "trigger": trigger,
            },
        )
        response.raise_for_status()
        delivery = response.json()
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] Could not broadcast reroute: %s", ctx.trip_id, exc)
        return {"routes": len(new_routes), "error": f"broadcast failed: {exc}"}

    log.info(
        "[%s] Reroute (%s) delivered: %d routes to %s subscribers",
        ctx.trip_id, trigger, len(new_routes), delivery.get("delivered"),
    )
    return {
        "routes": len(new_routes),
        "delivered": delivery.get("delivered", 0),
        "trigger": trigger,
    }

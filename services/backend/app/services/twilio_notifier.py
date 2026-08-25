"""Twilio SMS and voice escalation for SOS events.

Three things the obvious implementation gets wrong, handled here:

1. The client is built lazily. Constructing `Client()` at import time with
   blank credentials raises, which would take down the whole gateway just
   because Twilio is not configured in local dev.
2. The Twilio SDK is synchronous. Called directly from async code it blocks
   the event loop — during an SOS, that stalls the very WebSocket carrying
   the traveller's location. Every call goes through `asyncio.to_thread`.
3. Failures are per-contact. One bad number must not stop the remaining
   contacts from being alerted, so results are gathered rather than raised.

With TWILIO_ENABLED=false the module logs exactly what it would have sent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from app.config import settings

log = logging.getLogger(__name__)

_client: Optional[Any] = None
_client_failed = False

# Twilio caps SMS at 1600 chars; voice calls to the two primary contacts only
MAX_VOICE_CONTACTS = 2


def _get_client():
    """Build the Twilio client on first use. Returns None if unavailable."""
    global _client, _client_failed

    if _client is not None:
        return _client
    if _client_failed or not settings.twilio_configured:
        return None

    try:
        from twilio.rest import Client  # imported lazily: optional dependency

        _client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        log.info("Twilio client initialised")
        return _client
    except Exception as exc:  # noqa: BLE001
        log.error("Twilio client init failed, notifications disabled: %s", exc)
        _client_failed = True
        return None


def _maps_link(lat: float, lon: float) -> str:
    return f"https://maps.google.com/?q={lat},{lon}"


def _sms_body(user_name: str, lat: float, lon: float, zone_name: Optional[str] = None) -> str:
    where = f" near {zone_name}" if zone_name else ""
    return (
        "RAAHI SOS ALERT\n"
        f"{user_name} may be in danger{where}.\n"
        f"Last known location: {_maps_link(lat, lon)}\n"
        "Please contact them immediately."
    )


def _voice_twiml(user_name: str) -> str:
    # Repeated once — the first seconds of an automated call are often missed.
    line = (
        f"Emergency alert from Raahi. {user_name} has triggered an S O S "
        "and may be in danger. Please contact them immediately."
    )
    return (
        "<Response>"
        f'<Say voice="alice" language="en-IN">{line}</Say>'
        '<Pause length="1"/>'
        f'<Say voice="alice" language="en-IN">{line}</Say>'
        "</Response>"
    )


def _valid_contacts(contacts: Sequence[dict]) -> list[dict]:
    """Drop entries without a phone number, preserving caller order."""
    return [c for c in contacts if isinstance(c, dict) and c.get("phone")]


# ============================================================
# SMS
# ============================================================
async def send_sos_sms(
    contacts: Sequence[dict],
    user_name: str,
    lat: float,
    lon: float,
    zone_name: Optional[str] = None,
) -> dict[str, Any]:
    """SMS every emergency contact. Returns per-contact results."""
    targets = _valid_contacts(contacts)
    if not targets:
        log.warning("SOS SMS requested for %s but no contacts have phone numbers", user_name)
        return {"sent": 0, "failed": 0, "results": [], "dry_run": False}

    body = _sms_body(user_name, lat, lon, zone_name)
    client = _get_client()

    if client is None:
        for c in targets:
            log.warning("[SOS DRY-RUN] SMS -> %s\n%s", c["phone"], body)
        return {"sent": 0, "failed": 0, "results": [], "dry_run": True}

    def _send_one(phone: str) -> str:
        message = client.messages.create(
            body=body, from_=settings.TWILIO_FROM_NUMBER, to=phone
        )
        return message.sid

    async def _dispatch(contact: dict) -> dict[str, Any]:
        phone = contact["phone"]
        try:
            sid = await asyncio.to_thread(_send_one, phone)
            log.info("SOS SMS delivered to %s (sid=%s)", phone, sid)
            return {"phone": phone, "ok": True, "sid": sid}
        except Exception as exc:  # noqa: BLE001
            log.error("SOS SMS failed for %s: %s", phone, exc)
            return {"phone": phone, "ok": False, "error": str(exc)}

    results = await asyncio.gather(*(_dispatch(c) for c in targets))
    sent = sum(1 for r in results if r["ok"])
    return {"sent": sent, "failed": len(results) - sent, "results": results, "dry_run": False}


# ============================================================
# Voice
# ============================================================
async def make_sos_voice_call(
    contacts: Sequence[dict],
    user_name: str,
    lat: float,
    lon: float,
) -> dict[str, Any]:
    """Place voice calls to the primary contacts.

    Limited to MAX_VOICE_CONTACTS: calls are slow, expensive, and the goal is
    to reach one human fast rather than dial everyone at once.
    """
    targets = _valid_contacts(contacts)[:MAX_VOICE_CONTACTS]
    if not targets:
        return {"placed": 0, "failed": 0, "results": [], "dry_run": False}

    twiml = _voice_twiml(user_name)
    client = _get_client()

    if client is None:
        for c in targets:
            log.warning("[SOS DRY-RUN] VOICE CALL -> %s", c["phone"])
        return {"placed": 0, "failed": 0, "results": [], "dry_run": True}

    def _call_one(phone: str) -> str:
        call = client.calls.create(
            twiml=twiml, from_=settings.TWILIO_FROM_NUMBER, to=phone
        )
        return call.sid

    async def _dispatch(contact: dict) -> dict[str, Any]:
        phone = contact["phone"]
        try:
            sid = await asyncio.to_thread(_call_one, phone)
            log.info("SOS voice call placed to %s (sid=%s)", phone, sid)
            return {"phone": phone, "ok": True, "sid": sid}
        except Exception as exc:  # noqa: BLE001
            log.error("SOS voice call failed for %s: %s", phone, exc)
            return {"phone": phone, "ok": False, "error": str(exc)}

    results = await asyncio.gather(*(_dispatch(c) for c in targets))
    placed = sum(1 for r in results if r["ok"])
    return {"placed": placed, "failed": len(results) - placed, "results": results, "dry_run": False}


# ============================================================
# Combined escalation
# ============================================================
async def escalate(
    contacts: Sequence[dict],
    user_name: str,
    lat: float,
    lon: float,
    zone_name: Optional[str] = None,
    voice: bool = True,
) -> dict[str, Any]:
    """Full escalation: SMS everyone, then call the primary contacts.

    SMS goes first and is awaited before dialling — it is near-instant and
    carries the map link, which is the single most useful piece of
    information a contact can receive.
    """
    sms_result = await send_sos_sms(contacts, user_name, lat, lon, zone_name)
    voice_result: dict[str, Any] = {"placed": 0, "failed": 0, "results": [], "skipped": True}
    if voice:
        voice_result = await make_sos_voice_call(contacts, user_name, lat, lon)

    return {
        "sms": sms_result,
        "voice": voice_result,
        "contacts_alerted": len(_valid_contacts(contacts)),
        "twilio_enabled": settings.twilio_configured,
    }

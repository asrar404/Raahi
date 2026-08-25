"""Authentication routes.

Supabase owns credentials and issues the JWT. RAAHI keeps a mirror row in
`users` keyed on the token's `sub` claim, because trips, telemetry and SOS
contacts all need a local foreign key.

POST /verify is the bridge: call it once after Supabase sign-in and it
provisions or refreshes the mirror row.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.dependencies import CurrentUser, DbConn
from app.middleware.auth import (
    DEMO_SUPABASE_UID,
    AuthError,
    _dev_auth_bypass_allowed,
    decode_token,
)
from app.models.user import (
    AuthVerifyRequest,
    AuthVerifyResponse,
    EmergencyContact,
    EmergencyContactsUpdate,
    UserProfile,
    UserProfileUpdate,
)

log = logging.getLogger(__name__)
router = APIRouter()
bearer_scheme = HTTPBearer(auto_error=False)

USER_COLUMNS = """
    id, supabase_uid, full_name, phone, email, gender,
    preferred_modes, budget_default, emergency_contacts,
    sos_enabled, home_city, created_at, updated_at
"""


def _to_profile(row: dict[str, Any]) -> UserProfile:
    """Map a DB row to the API model, tolerating malformed contact JSON.

    A single bad contact entry (hand-edited, or written by an older client)
    must not make the whole profile unreadable — the user would be locked out
    of the app entirely.
    """
    data = dict(row)
    contacts: list[EmergencyContact] = []
    for raw in data.get("emergency_contacts") or []:
        try:
            contacts.append(EmergencyContact(**raw))
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping malformed emergency contact %r: %s", raw, exc)
    data["emergency_contacts"] = contacts
    return UserProfile(**data)


@router.post("/verify", response_model=AuthVerifyResponse)
async def verify(
    payload: AuthVerifyRequest,
    conn: DbConn,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthVerifyResponse:
    """Exchange a Supabase JWT for a RAAHI profile, provisioning on first use.

    Idempotent: safe to call on every app launch. Later calls only fill in
    fields that are still empty, so they never clobber edits made in Profile.
    """
    # ── Resolve identity ────────────────────────────────────
    if credentials and credentials.credentials:
        claims = decode_token(credentials.credentials)
        supabase_uid = claims.get("sub")
        if not supabase_uid:
            raise AuthError("Token has no 'sub' claim")
        claim_email = claims.get("email")
        claim_phone = claims.get("phone")
    elif _dev_auth_bypass_allowed():
        log.warning("DEV AUTH BYPASS: /auth/verify resolving to the seeded demo user")
        supabase_uid = DEMO_SUPABASE_UID
        claim_email = None
        claim_phone = None
    else:
        raise AuthError("Missing bearer token")

    existing = await conn.fetchrow(
        f"SELECT {USER_COLUMNS} FROM users WHERE supabase_uid = $1", supabase_uid
    )

    if existing is not None:
        # Backfill only what is still missing.
        updated = await conn.fetchrow(
            f"""
            UPDATE users
            SET full_name  = COALESCE(NULLIF($2, ''), full_name),
                email      = COALESCE(email, NULLIF($3, '')),
                home_city  = COALESCE(home_city, NULLIF($4, '')),
                gender     = COALESCE(gender, NULLIF($5, ''))
            WHERE supabase_uid = $1
            RETURNING {USER_COLUMNS}
            """,
            supabase_uid,
            payload.full_name or "",
            payload.email or claim_email or "",
            payload.home_city or "",
            payload.gender or "",
        )
        return AuthVerifyResponse(user=_to_profile(dict(updated)), created=False)

    # ── First login: provision ──────────────────────────────
    phone = payload.phone or claim_phone
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A phone number is required to create a RAAHI profile — SOS "
                "escalation depends on it. Pass 'phone' in the request body."
            ),
        )

    full_name = payload.full_name or "RAAHI Traveller"

    try:
        row = await conn.fetchrow(
            f"""
            INSERT INTO users
                (supabase_uid, full_name, phone, email, gender, home_city, budget_default)
            VALUES ($1, $2, $3, NULLIF($4, ''), NULLIF($5, ''), NULLIF($6, ''),
                    COALESCE($7, 500.00))
            RETURNING {USER_COLUMNS}
            """,
            supabase_uid,
            full_name,
            phone,
            payload.email or claim_email or "",
            payload.gender or "",
            payload.home_city or "",
            payload.budget_default,
        )
    except asyncpg.UniqueViolationError as exc:
        # phone and email are both UNIQUE; surface which one collided.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account already exists with those details ({exc.constraint_name})",
        ) from exc

    log.info("Provisioned user %s (supabase_uid=%s)", row["id"], supabase_uid)
    return AuthVerifyResponse(user=_to_profile(dict(row)), created=True)


@router.get("/me", response_model=UserProfile)
async def me(user: CurrentUser) -> UserProfile:
    """Current user's profile."""
    return _to_profile(user)


@router.patch("/me", response_model=UserProfile)
async def update_me(
    payload: UserProfileUpdate,
    user: CurrentUser,
    conn: DbConn,
) -> UserProfile:
    """Partially update the profile. Only supplied fields are written."""
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        return _to_profile(user)

    allowed = {
        "full_name", "phone", "email", "gender",
        "preferred_modes", "budget_default", "sos_enabled", "home_city",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return _to_profile(user)

    # $1 is the user id, so value placeholders start at $2.
    assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(fields))
    values = list(fields.values())

    try:
        row = await conn.fetchrow(
            f"UPDATE users SET {assignments} WHERE id = $1 RETURNING {USER_COLUMNS}",  # noqa: S608
            user["id"], *values,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Value already in use ({exc.constraint_name})",
        ) from exc

    return _to_profile(dict(row))


@router.patch("/emergency-contacts", response_model=UserProfile)
async def update_emergency_contacts(
    payload: EmergencyContactsUpdate,
    user: CurrentUser,
    conn: DbConn,
) -> UserProfile:
    """Replace the emergency contact list.

    A full replace rather than a merge: the list is short and users expect
    deleting a contact here to actually delete them.
    """
    contacts = [c.model_dump() for c in payload.contacts]
    row = await conn.fetchrow(
        f"UPDATE users SET emergency_contacts = $2 WHERE id = $1 RETURNING {USER_COLUMNS}",
        user["id"], contacts,
    )
    log.info("Updated %d emergency contacts for user %s", len(contacts), user["id"])
    return _to_profile(dict(row))


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    """What the client needs to know about this server's auth setup.

    Lets the app show a clear "auth not configured" state instead of failing
    with an opaque 401 when the backend has no Supabase secret.
    """
    return {
        "auth_configured": settings.auth_configured,
        "supabase_url": settings.SUPABASE_URL or None,
        "dev_bypass_active": _dev_auth_bypass_allowed(),
        "environment": settings.ENVIRONMENT,
    }

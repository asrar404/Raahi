"""Supabase JWT authentication.

Supabase signs access tokens with a project-wide HS256 secret, so the gateway
can verify them locally with no network call to Supabase on the request path.

The `users` table mirrors Supabase identities via `supabase_uid` (the `sub`
claim). POST /auth/verify provisions that mirror row on first login; every
other endpoint expects it to already exist.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

import asyncpg
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.services.db import get_db

log = logging.getLogger(__name__)

# auto_error=False so optional-auth routes can fall through instead of 403ing
bearer_scheme = HTTPBearer(auto_error=False)

DEMO_SUPABASE_UID = "demo-supabase-uid-0001"


class AuthError(HTTPException):
    def __init__(self, detail: str = "Not authenticated"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def _dev_auth_bypass_allowed() -> bool:
    """True only in non-production with no JWT secret configured.

    Lets the mobile app be exercised end to end before Supabase is wired up.
    Both conditions must hold: setting SUPABASE_JWT_SECRET, or
    ENVIRONMENT=production, closes the bypass immediately.
    """
    return not settings.is_production and not settings.auth_configured


def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims."""
    if not settings.SUPABASE_JWT_SECRET:
        raise AuthError("Auth is not configured on this server")

    try:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.SUPABASE_JWT_AUDIENCE,
            options={"verify_aud": bool(settings.SUPABASE_JWT_AUDIENCE)},
        )
    except JWTError as exc:
        log.info("JWT rejected: %s", exc)
        raise AuthError(f"Invalid or expired token: {exc}") from exc


async def get_claims(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Decoded JWT claims for the caller."""
    if credentials is None or not credentials.credentials:
        raise AuthError("Missing bearer token")
    return decode_token(credentials.credentials)


async def _load_user_by_uid(conn: asyncpg.Connection, supabase_uid: str) -> Optional[dict]:
    row = await conn.fetchrow(
        """
        SELECT id, supabase_uid, full_name, phone, email, gender,
               preferred_modes, budget_default, emergency_contacts,
               sos_enabled, home_city, created_at, updated_at
        FROM users
        WHERE supabase_uid = $1
        """,
        supabase_uid,
    )
    return dict(row) if row else None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Resolve the caller to a row in `users`.

    Raises 401 for a bad token and 404 when the token is valid but no mirror
    row exists — the client should call POST /auth/verify first.
    """
    if credentials is None or not credentials.credentials:
        if _dev_auth_bypass_allowed():
            log.warning(
                "DEV AUTH BYPASS: unauthenticated %s %s served as the demo user. "
                "Set SUPABASE_JWT_SECRET to require real tokens.",
                request.method,
                request.url.path,
            )
            user = await _load_user_by_uid(conn, DEMO_SUPABASE_UID)
            if user is None:
                raise AuthError("Dev bypass active but the demo user is not seeded")
            return user
        raise AuthError("Missing bearer token")

    claims = decode_token(credentials.credentials)
    supabase_uid = claims.get("sub")
    if not supabase_uid:
        raise AuthError("Token has no 'sub' claim")

    user = await _load_user_by_uid(conn, supabase_uid)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not provisioned. Call POST /api/v1/auth/verify first.",
        )

    request.state.user_id = str(user["id"])
    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    conn: asyncpg.Connection = Depends(get_db),
) -> Optional[dict[str, Any]]:
    """Same as get_current_user but returns None instead of raising.

    Used by endpoints that accept anonymous input, such as crowdsourced
    reports — a bad token should not stop someone flagging a hazard.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        claims = decode_token(credentials.credentials)
    except HTTPException:
        return None
    supabase_uid = claims.get("sub")
    if not supabase_uid:
        return None
    return await _load_user_by_uid(conn, supabase_uid)


async def require_trip_owner(
    trip_id: str,
    user: dict[str, Any],
    conn: asyncpg.Connection,
) -> dict[str, Any]:
    """Fetch a trip, enforcing that the caller owns it.

    404 (not 403) for someone else's trip: confirming a trip ID exists would
    leak location history to a probing client.
    """
    row = await conn.fetchrow(
        "SELECT id, user_id, status FROM trips WHERE id = $1", trip_id
    )
    if row is None or str(row["user_id"]) != str(user["id"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return dict(row)


# ============================================================
# Service-to-service authentication
# ============================================================
def is_internal_call(request: Request) -> bool:
    """True when X-Internal-Token matches the configured shared secret.

    safety_watcher and ai_engine call back into the gateway with no end-user
    JWT — they act on the system's behalf, not a user's.
    """
    if not settings.INTERNAL_API_KEY:
        return False
    token = request.headers.get("X-Internal-Token")
    if not token:
        return False
    # Constant-time compare: this secret guards SOS escalation.
    return secrets.compare_digest(token, settings.INTERNAL_API_KEY)


async def get_internal_or_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    conn: asyncpg.Connection = Depends(get_db),
) -> Optional[dict[str, Any]]:
    """Allow either an internal service token or an authenticated user.

    Returns the user dict for a user call, or None for an internal call.
    Raises 401 when neither is present.
    """
    if is_internal_call(request):
        request.state.internal = True
        return None

    if credentials is None or not credentials.credentials:
        if _dev_auth_bypass_allowed():
            log.warning(
                "DEV AUTH BYPASS: internal endpoint %s served without credentials",
                request.url.path,
            )
            return await _load_user_by_uid(conn, DEMO_SUPABASE_UID)
        raise AuthError("Requires a user token or a valid X-Internal-Token")

    claims = decode_token(credentials.credentials)
    supabase_uid = claims.get("sub")
    if not supabase_uid:
        raise AuthError("Token has no 'sub' claim")
    user = await _load_user_by_uid(conn, supabase_uid)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not provisioned. Call POST /api/v1/auth/verify first.",
        )
    return user

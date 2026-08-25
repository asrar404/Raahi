"""Shared FastAPI dependencies.

Single import site for route modules, so the wiring between routers,
the connection pool and authentication stays in one place.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

import asyncpg
from fastapi import Depends, Query

from app.middleware.auth import (
    get_claims,
    get_current_user,
    get_internal_or_user,
    get_optional_user,
    is_internal_call,
    require_trip_owner,
)
from app.services.db import get_db

# ── Annotated aliases ───────────────────────────────────────
DbConn = Annotated[asyncpg.Connection, Depends(get_db)]
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
OptionalUser = Annotated[Optional[dict[str, Any]], Depends(get_optional_user)]
Claims = Annotated[dict[str, Any], Depends(get_claims)]
# Either an end-user JWT or a peer service's X-Internal-Token.
# None means the call came from a trusted internal service.
InternalOrUser = Annotated[Optional[dict[str, Any]], Depends(get_internal_or_user)]


class Pagination:
    """Bounded limit/offset for list endpoints."""

    def __init__(
        self,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


Paginate = Annotated[Pagination, Depends(Pagination)]

__all__ = [
    "Claims",
    "CurrentUser",
    "DbConn",
    "InternalOrUser",
    "OptionalUser",
    "Paginate",
    "Pagination",
    "get_current_user",
    "get_db",
    "get_internal_or_user",
    "get_optional_user",
    "is_internal_call",
    "require_trip_owner",
]

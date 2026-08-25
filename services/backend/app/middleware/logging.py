"""Request logging middleware.

Attaches a request ID to every call, times it, and logs a single structured
line on completion. The ID is echoed back as `X-Request-ID` so a mobile bug
report can be tied to a specific server log line.

Coordinates are never logged. Telemetry endpoints carry live location data,
and the traveller's whereabouts should not end up in a log aggregator.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("raahi.access")

# Health checks are polled every few seconds by Docker; logging them buries
# everything else.
QUIET_PATHS = {"/health", "/health/live", "/health/ready", "/metrics", "/favicon.ico"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        started = time.perf_counter()
        quiet = request.url.path in QUIET_PATHS

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            # exc_info so the traceback survives; the global handler in main.py
            # turns this into a 500 for the client.
            log.exception(
                "%s %s -> unhandled exception in %.1fms [rid=%s]",
                request.method, request.url.path, elapsed_ms, request_id,
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"

        if not quiet:
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            log.log(
                level,
                "%s %s -> %d in %.1fms [rid=%s]",
                request.method, request.url.path, response.status_code,
                elapsed_ms, request_id,
            )

        return response


def configure_logging(level: str = "INFO") -> None:
    """Set up root logging once, at startup.

    force=True replaces the handler uvicorn installs, so application and
    access logs share one format instead of interleaving two.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    # These are noisy at DEBUG and rarely useful
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

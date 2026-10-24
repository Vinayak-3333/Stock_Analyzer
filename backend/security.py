"""
StockRadar IN — Hardening (Phase 4)
===================================
Three concerns, all wired by `install_security(app)`:

  • SR-4 Security response headers — nosniff, frame-deny, referrer policy,
    permissions policy, COOP, a strict CSP, and (opt-in) HSTS.
  • SR-6 Rate limiting — a sliding-window limiter keyed by session identity
    when there is one, else client IP, with per-path-prefix rules so
    /api/trigger and /auth/* are far stricter than dashboard polling.
  • SR-8 /docs lockdown — FastAPI's built-in doc routes are disabled in api.py
    and re-served here behind `require_user`, or switched off entirely.

Why a hand-rolled limiter instead of slowapi: the design doc allows "slowapi
or equivalent", and this backend is a single uvicorn process (laptop + one NAS
container), which is exactly the regime where slowapi's default in-memory
backend works and no more. Rolling it here keeps the dependency list short,
avoids rebuilding the NAS images for a new wheel, and lets the policy be
expressed as path-prefix rules instead of a decorator on every route. If this
ever runs multiple workers, swap `_WINDOWS` for Redis (already a dependency)
or drop in slowapi with a Redis backend — the middleware boundary stays.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Depends
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from audit import audit, client_ip

# ── Config ─────────────────────────────────────────────────────────────────────
def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


# HSTS is meaningless (and ignored) over plain http, and actively unhelpful on a
# localhost dev box, so it is opt-in and should be on wherever TLS terminates.
HSTS_ENABLED = _flag("SECURITY_HSTS_ENABLED")
HSTS_MAX_AGE = int(os.environ.get("SECURITY_HSTS_MAX_AGE", "31536000"))  # 1 year

# Only honour X-Forwarded-For when a trusted proxy actually sets it.
TRUST_PROXY_HEADERS = _flag("TRUST_PROXY_HEADERS")

# /docs: served to an authenticated user by default; set false to remove them.
DOCS_ENABLED = _flag("DOCS_ENABLED", "true")

RATE_LIMIT_ENABLED = _flag("RATE_LIMIT_ENABLED", "true")


# ── SR-4: security headers ─────────────────────────────────────────────────────
# This app serves JSON, not HTML, so it can afford the strictest possible CSP.
# Note this CSP governs *API* responses only — the SPA's own page is served from
# a different origin (Vercel / nginx) and carries its own policy.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# Swagger UI is real HTML and pulls its bundle from jsDelivr, so the doc routes
# need their own, looser policy. Still no frames, no form posts.
_DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' https://fastapi.tiangolo.com data:; "
    "connect-src 'self'; "
    "font-src https://cdn.jsdelivr.net; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach SR-4 headers to every response, including error responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers

        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), interest-cohort=()"
        h["Content-Security-Policy"] = (
            _DOCS_CSP if request.url.path in _DOC_PATHS else _API_CSP
        )
        if HSTS_ENABLED:
            h["Strict-Transport-Security"] = f"max-age={HSTS_MAX_AGE}; includeSubDomains"
        return response


# ── SR-6: rate limiting ────────────────────────────────────────────────────────
# (limit, window_seconds) per bucket. Most specific prefix wins.
_Rule = Tuple[int, int]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# A full run is minutes of CPU plus Yahoo/NSE quota plus an email, so triggers
# are throttled by the hour, not the minute. The dashboard polls 3 endpoints
# every 30s (6 req/min) and re-polls every 5s while a run is in flight
# (36 req/min), so the /api budget has to clear that comfortably.
RULES: Tuple[Tuple[str, _Rule], ...] = (
    ("/api/trigger", (_int_env("RATE_LIMIT_TRIGGER", 5), 3600)),
    ("/auth/",       (_int_env("RATE_LIMIT_AUTH", 20), 60)),
    ("/api/",        (_int_env("RATE_LIMIT_API", 240), 60)),
)

# Docker healthchecks poll this constantly and it is public by design.
EXEMPT_PATHS = frozenset({"/api/health"})

_WINDOWS: Dict[str, Deque[float]] = {}
_LOCK = threading.Lock()
_LAST_PRUNE = 0.0


def _rule_for(path: str) -> Optional[Tuple[str, _Rule]]:
    """Longest matching prefix wins, so /api/trigger beats /api/."""
    best: Optional[Tuple[str, _Rule]] = None
    for prefix, rule in RULES:
        if path.startswith(prefix):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, rule)
    return best


def _identity(request: Request) -> Optional[str]:
    """Session email if the request carries one. Cheap: no DB, no crypto —
    SessionMiddleware has already decoded the cookie by the time we run."""
    try:
        user = request.session.get("user")
        if isinstance(user, dict):
            return user.get("email")
    except Exception:
        # No SessionMiddleware in the chain (or a malformed cookie) — fall back
        # to IP-based limiting rather than failing the request.
        pass
    return None


def _prune(now: float) -> None:
    """Drop windows that have gone quiet so the dict can't grow without bound."""
    global _LAST_PRUNE
    if now - _LAST_PRUNE < 300:
        return
    _LAST_PRUNE = now
    for key in [k for k, dq in _WINDOWS.items() if not dq or now - dq[-1] > 3600]:
        _WINDOWS.pop(key, None)


def _check(key: str, limit: int, window: int, now: float) -> Tuple[bool, int]:
    """Sliding window. Returns (allowed, retry_after_seconds)."""
    with _LOCK:
        _prune(now)
        dq = _WINDOWS.setdefault(key, deque())
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            # Oldest hit in the window decides when a slot frees up.
            return False, max(1, int(dq[0] + window - now) + 1)
        dq.append(now)
        return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-identity (or per-IP) sliding-window limiter.

    Sits *inside* SessionMiddleware so `request.session` is already decoded,
    which lets an authenticated user get their own budget instead of sharing
    one with everyone behind the same NAT.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not RATE_LIMIT_ENABLED or path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        matched = _rule_for(path)
        if matched is None:
            return await call_next(request)

        prefix, (limit, window) = matched
        ip = client_ip(request, TRUST_PROXY_HEADERS)
        who = _identity(request)
        key = f"{prefix}|{who or 'ip:' + ip}"

        allowed, retry_after = _check(key, limit, window, time.time())
        if not allowed:
            audit(
                "ratelimit.blocked",
                identity=who,
                ip=ip,
                path=path,
                limit=f"{limit}/{window}s",
                retry_after=retry_after,
            )
            return JSONResponse(
                {"detail": "Rate limit exceeded. Slow down and try again shortly."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


# ── SR-8: /docs behind auth ────────────────────────────────────────────────────
def install_docs(app) -> None:
    """Re-serve Swagger UI + the OpenAPI schema, authenticated.

    api.py creates the app with docs_url/redoc_url/openapi_url set to None, so
    these are the only doc routes that exist. The schema is protected too —
    publishing the full route list unauthenticated would undo most of the point.
    """
    if not DOCS_ENABLED:
        return

    from auth import require_user  # imported here to avoid a circular import

    @app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_user)])
    def protected_openapi():
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=[Depends(require_user)])
    def protected_docs():
        from fastapi.openapi.docs import get_swagger_ui_html

        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} — docs")


def security_summary() -> str:
    """One-line description of the active posture, for the startup log."""
    return (
        f"headers=on hsts={'on' if HSTS_ENABLED else 'off'} "
        f"ratelimit={'on' if RATE_LIMIT_ENABLED else 'off'}"
        f"(trigger={RULES[0][1][0]}/h auth={RULES[1][1][0]}/min api={RULES[2][1][0]}/min) "
        f"docs={'auth-required' if DOCS_ENABLED else 'disabled'} "
        f"trust_proxy={'on' if TRUST_PROXY_HEADERS else 'off'}"
    )

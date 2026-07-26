"""
StockRadar IN — OIDC Authentication (Phase 1)
=============================================
Google OIDC (Authorization Code + PKCE) behind a Backend-for-Frontend (BFF)
pattern:

  • The browser never sees OIDC tokens. This backend performs the code↔token
    exchange, validates the ID token (signature via Google's JWKS + nonce),
    checks the verified email against an allow-list, and stores a *minimal*
    identity in a signed, httpOnly session cookie (Starlette SessionMiddleware).
  • Endpoints (all unprefixed, NOT under /api): /auth/login, /auth/callback,
    /auth/logout, /auth/me.

Config comes from the environment (loaded from .env by api.py before this
module is imported). If OIDC_CLIENT_ID / OIDC_CLIENT_SECRET are absent the
module still imports cleanly and the auth endpoints return 503 — so the backend
keeps booting before Google credentials are set up.

Phase 2 will apply `require_user` to the /api/* routes. This module deliberately
does NOT protect any existing route yet.
"""

from __future__ import annotations

import os
import time
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

log = logging.getLogger("stockradar.auth")

# ── Config (read once at import; .env is already loaded by api.py) ─────────────
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

OIDC_CLIENT_ID     = os.environ.get("OIDC_CLIENT_ID", "").strip()
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "").strip()
OIDC_REDIRECT_URI  = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8000/auth/callback").strip()

# Where to send the browser after login/logout (the SPA).
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")

# Authorization allow-list: only these verified emails may obtain a session.
# Defaults to RECIPIENT_EMAIL so the single-user case needs no extra config.
_allowed_raw = os.environ.get("ALLOWED_EMAILS") or os.environ.get("RECIPIENT_EMAIL", "")
ALLOWED_EMAILS = {e.strip().lower() for e in _allowed_raw.split(",") if e.strip()}

# ── Session cookie settings ────────────────────────────────────────────────────
# SESSION_SECRET signs the cookie. If unset we generate an ephemeral one so dev
# still works, but sessions won't survive a restart — set it in prod.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
    log.warning(
        "SESSION_SECRET not set — using an ephemeral key. Sessions will not "
        "survive a restart. Set SESSION_SECRET in .env for production."
    )

SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "stockradar_session")
SESSION_MAX_AGE     = int(os.environ.get("SESSION_MAX_AGE", "28800"))  # 8h
# 'lax' for same-origin (behind the NAS reverse proxy); 'none' for the
# cross-origin Vercel topology (requires Secure=True).
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "lax").lower()
# Secure must be False for plain-http localhost dev, True in prod/HTTPS.
SESSION_COOKIE_SECURE   = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

OIDC_ENABLED = bool(OIDC_CLIENT_ID and OIDC_CLIENT_SECRET)

# ── OAuth client (registered only when credentials are present) ────────────────
_oauth = None
if OIDC_ENABLED:
    try:
        from authlib.integrations.starlette_client import OAuth

        _oauth = OAuth()
        _oauth.register(
            name="google",
            server_metadata_url=GOOGLE_DISCOVERY_URL,
            client_id=OIDC_CLIENT_ID,
            client_secret=OIDC_CLIENT_SECRET,
            client_kwargs={
                "scope": "openid email profile",
                # Enable PKCE — Authlib stores the code_verifier in the session
                # during authorize_redirect and sends the challenge to Google.
                "code_challenge_method": "S256",
            },
        )
        log.info(
            "OIDC (Google) enabled. redirect_uri=%s allowed_emails=%d",
            OIDC_REDIRECT_URI, len(ALLOWED_EMAILS),
        )
    except Exception as exc:  # pragma: no cover — misconfig/missing dep
        _oauth = None
        log.exception("Failed to initialise OIDC client — auth disabled: %s", exc)
else:
    log.warning(
        "OIDC disabled — OIDC_CLIENT_ID / OIDC_CLIENT_SECRET not set. "
        "Auth endpoints will return 503 until configured."
    )


# ── Session helpers ────────────────────────────────────────────────────────────
def get_session_user(request: Request) -> Optional[dict]:
    """Return the logged-in identity, or None. Enforces a server-side TTL on top
    of the cookie's own expiry (defence in depth / bounds soft-revocation)."""
    user = request.session.get("user")
    if not user:
        return None
    auth_at = request.session.get("auth_at", 0)
    if SESSION_MAX_AGE and (time.time() - auth_at) > SESSION_MAX_AGE:
        request.session.clear()
        return None
    return user


async def require_user(request: Request) -> dict:
    """FastAPI dependency — 401 unless there is a valid session.

    Defined here for Phase 2, which will attach it to the /api/* routes. Not
    wired to any route yet.
    """
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# ── App wiring ─────────────────────────────────────────────────────────────────
def install_auth(app) -> None:
    """Attach the signed-cookie SessionMiddleware and mount the /auth router."""
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site=SESSION_COOKIE_SAMESITE,
        https_only=SESSION_COOKIE_SECURE,
    )
    app.include_router(auth_router)


# ── Endpoints ──────────────────────────────────────────────────────────────────
auth_router = APIRouter(tags=["auth"])


def _require_oidc():
    if not (OIDC_ENABLED and _oauth is not None):
        raise HTTPException(
            status_code=503,
            detail="OIDC is not configured (set OIDC_CLIENT_ID / OIDC_CLIENT_SECRET).",
        )


@auth_router.get("/auth/login")
async def login(request: Request):
    """Kick off the OIDC Authorization Code + PKCE flow — redirects to Google."""
    _require_oidc()
    return await _oauth.google.authorize_redirect(request, OIDC_REDIRECT_URI)


@auth_router.get("/auth/callback", name="auth_callback")
async def callback(request: Request):
    """OIDC redirect target. Exchanges the code, validates the ID token, applies
    the email allow-list, then issues the session cookie and returns to the SPA."""
    _require_oidc()
    from authlib.integrations.starlette_client import OAuthError

    try:
        token = await _oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("OIDC callback error: %s", exc)
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error=oauth")

    # Authlib validates the id_token signature (JWKS) + nonce and parses claims.
    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            userinfo = await _oauth.google.userinfo(token=token)
        except Exception as exc:  # pragma: no cover
            log.warning("Failed to fetch userinfo: %s", exc)
            return RedirectResponse(f"{FRONTEND_URL}/?auth_error=userinfo")

    email          = (userinfo.get("email") or "").strip().lower()
    email_verified = bool(userinfo.get("email_verified"))

    if not email or not email_verified or email not in ALLOWED_EMAILS:
        # Authenticated by Google but not authorized for this app.
        request.session.clear()
        log.warning("Rejected login for unauthorized/unverified email: %r", email)
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error=unauthorized")

    request.session["user"] = {
        "sub":     userinfo.get("sub"),
        "email":   email,
        "name":    userinfo.get("name"),
        "picture": userinfo.get("picture"),
    }
    request.session["auth_at"] = int(time.time())
    log.info("Login OK: %s", email)
    return RedirectResponse(FRONTEND_URL)


@auth_router.post("/auth/logout")
async def logout(request: Request):
    """Clear the session (soft-revoke). CSRF protection is added in Phase 2."""
    request.session.clear()
    return {"status": "logged_out"}


@auth_router.get("/auth/me")
async def me(request: Request):
    """Return the current identity, or 401 if not logged in. The SPA uses this
    to decide whether to show the dashboard or the login screen."""
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

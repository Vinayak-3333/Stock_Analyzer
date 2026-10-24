"""Ad-hoc Phase 3 verification — run from backend/:  python _verify_phase3.py

Forges a SessionMiddleware cookie (same recipe Starlette uses) so the auth +
CSRF wiring can be exercised without a real Google round-trip. Not a test
suite; delete or keep alongside the other ad-hoc check_*.py scripts.
"""
import base64
import json
import os
import time

os.environ.setdefault("SESSION_SECRET", "verify-only-secret-not-for-prod")
os.environ.setdefault("ALLOWED_EMAILS", "owner@example.com")

import itsdangerous
from fastapi.testclient import TestClient

import api
import auth

SECRET = auth.SESSION_SECRET
PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(f"{name}: got {got}, want {want}")
    print(f"  {'ok  ' if got == want else 'FAIL'} {name}: {got} (want {want})")


def session_cookie(data: dict) -> str:
    """Reproduce starlette.middleware.sessions cookie encoding."""
    signer = itsdangerous.TimestampSigner(str(SECRET))
    payload = base64.b64encode(json.dumps(data).encode())
    return signer.sign(payload).decode()


CSRF = "test-csrf-token-abc123"
SESSION = {
    "user": {"sub": "1", "email": "owner@example.com", "name": "Owner", "picture": None},
    "auth_at": int(time.time()),
    "csrf": CSRF,
}

with TestClient(api.app) as c:
    print("\n-- unauthenticated --")
    check("GET  /api/health", c.get("/api/health").status_code, 200)
    check("GET  /auth/me", c.get("/auth/me").status_code, 401)
    check("GET  /api/status", c.get("/api/status").status_code, 401)
    check("GET  /api/latest", c.get("/api/latest").status_code, 401)
    check("POST /api/trigger", c.post("/api/trigger").status_code, 401)

    print("\n-- authenticated session --")
    c.cookies.set(auth.SESSION_COOKIE_NAME, session_cookie(SESSION))

    me = c.get("/auth/me")
    check("GET  /auth/me", me.status_code, 200)
    check("  /auth/me returns csrf", me.json().get("csrf"), CSRF)
    check("  /auth/me sets readable csrf cookie",
          me.cookies.get(auth.CSRF_COOKIE_NAME), CSRF)
    check("  csrf cookie is NOT httpOnly",
          "httponly" in me.headers.get("set-cookie", "").lower(), False)
    check("GET  /api/status", c.get("/api/status").status_code, 200)

    print("\n-- CSRF enforcement on state-changing routes --")
    check("POST /api/trigger  (no header)", c.post("/api/trigger").status_code, 403)
    check("POST /api/trigger  (wrong header)",
          c.post("/api/trigger", headers={"X-CSRF-Token": "wrong"}).status_code, 403)
    check("POST /auth/logout  (no header)", c.post("/auth/logout").status_code, 403)
    out = c.post("/auth/logout", headers={"X-CSRF-Token": CSRF})
    check("POST /auth/logout  (correct header)", out.status_code, 200)

    print("\n-- logout clears both cookies --")
    # Assert the Set-Cookie deletions directly. The jar can't be used here:
    # the session cookie was pinned with an empty domain, so the server's
    # domain-scoped deletion lands in a different jar entry.
    set_cookies = " | ".join(out.headers.get_list("set-cookie"))
    check("  session cookie expired",
          f"{auth.SESSION_COOKIE_NAME}=null" in set_cookies
          and "01 Jan 1970" in set_cookies, True)
    check("  csrf cookie expired",
          f'{auth.CSRF_COOKIE_NAME}=""' in set_cookies
          and "Max-Age=0" in set_cookies, True)

    print("\n-- no session cookie --")
    c.cookies.clear()
    check("GET  /auth/me", c.get("/auth/me").status_code, 401)
    check("POST /api/trigger", c.post("/api/trigger").status_code, 401)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)

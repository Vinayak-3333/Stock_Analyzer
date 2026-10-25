"""Ad-hoc security verification — run from backend/:  python _verify_security.py

Covers Phase 2 (auth on /api/*), Phase 3 (session + CSRF) and Phase 4
(headers, rate limiting, /docs lockdown, audit trail).

Forges a SessionMiddleware cookie using the same recipe Starlette uses, so the
whole surface can be exercised without a real Google round-trip. Not a pytest
suite; it lives alongside the other ad-hoc check_*.py scripts.
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
import security

SECRET = auth.SESSION_SECRET
ORIGIN = api.CORS_ORIGINS[0]
PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(f"{name}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}")


def session_cookie(data: dict) -> str:
    """Reproduce starlette.middleware.sessions cookie encoding."""
    signer = itsdangerous.TimestampSigner(str(SECRET))
    return signer.sign(base64.b64encode(json.dumps(data).encode())).decode()


CSRF = "test-csrf-token-abc123"
SESSION = {
    "user": {"sub": "1", "email": "owner@example.com", "name": "Owner", "picture": None},
    "auth_at": int(time.time()),
    "csrf": CSRF,
}

with TestClient(api.app) as c:
    # ── Phase 2: endpoint protection ──────────────────────────────────────────
    print("\n-- unauthenticated --")
    check("GET  /api/health", c.get("/api/health").status_code, 200)
    check("GET  /auth/me", c.get("/auth/me").status_code, 401)
    check("GET  /api/status", c.get("/api/status").status_code, 401)
    # 401 not 403: require_user must be evaluated before require_csrf, so an
    # anonymous caller is told to log in rather than blamed for a missing token.
    check("POST /api/trigger", c.post("/api/trigger").status_code, 401)

    # ── Phase 4: SR-8 docs lockdown ───────────────────────────────────────────
    print("\n-- SR-8: docs locked down --")
    check("GET  /docs          (anon)", c.get("/docs").status_code, 401)
    check("GET  /openapi.json  (anon)", c.get("/openapi.json").status_code, 401)
    check("GET  /redoc         (gone)", c.get("/redoc").status_code, 404)

    # ── Phase 4: SR-4 security headers ────────────────────────────────────────
    print("\n-- SR-4: security headers --")
    h = c.get("/api/health").headers
    check("  X-Content-Type-Options", h.get("x-content-type-options"), "nosniff")
    check("  X-Frame-Options", h.get("x-frame-options"), "DENY")
    check("  Referrer-Policy", h.get("referrer-policy"), "no-referrer")
    check("  Cross-Origin-Opener-Policy", h.get("cross-origin-opener-policy"), "same-origin")
    check("  Permissions-Policy present", bool(h.get("permissions-policy")), True)
    check("  CSP is the strict API one", h.get("content-security-policy"), security._API_CSP)
    check("  HSTS absent (disabled by default)", "strict-transport-security" in h, False)
    check("  headers present on 401 too",
          c.get("/api/status").headers.get("x-frame-options"), "DENY")

    # ── Phase 4: CORS must survive an error (the middleware-ordering fix) ─────
    print("\n-- CORS headers on an error response (ordering fix) --")
    r = c.get("/api/status", headers={"Origin": ORIGIN})
    check("  401 status", r.status_code, 401)
    check("  Access-Control-Allow-Origin", r.headers.get("access-control-allow-origin"), ORIGIN)
    check("  Access-Control-Allow-Credentials", r.headers.get("access-control-allow-credentials"), "true")

    print("\n-- CORS preflight accepts Cache-Control --")
    pre = c.options("/api/status", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "cache-control",
    })
    check("  preflight status", pre.status_code, 200)

    # ── Phase 3: authenticated session + CSRF ─────────────────────────────────
    print("\n-- authenticated session --")
    c.cookies.set(auth.SESSION_COOKIE_NAME, session_cookie(SESSION))
    me = c.get("/auth/me")
    check("GET  /auth/me", me.status_code, 200)
    check("  returns csrf", me.json().get("csrf"), CSRF)
    check("  sets readable csrf cookie", me.cookies.get(auth.CSRF_COOKIE_NAME), CSRF)
    check("GET  /api/status", c.get("/api/status").status_code, 200)

    print("\n-- SR-8: docs reachable once authenticated --")
    d = c.get("/docs")
    check("GET  /docs", d.status_code, 200)
    check("  is html", d.headers.get("content-type", "").startswith("text/html"), True)
    check("  uses the relaxed docs CSP",
          d.headers.get("content-security-policy"), security._DOCS_CSP)
    oa = c.get("/openapi.json")
    check("GET  /openapi.json", oa.status_code, 200)
    check("  is a schema", oa.json().get("info", {}).get("title"), api.app.title)

    print("\n-- CSRF enforcement --")
    check("POST /api/trigger  (no header)", c.post("/api/trigger").status_code, 403)
    check("POST /api/trigger  (wrong header)",
          c.post("/api/trigger", headers={"X-CSRF-Token": "wrong"}).status_code, 403)
    check("POST /auth/logout  (no header)", c.post("/auth/logout").status_code, 403)

    # ── Phase 4: SR-6 rate limiting ───────────────────────────────────────────
    # Done last: it deliberately exhausts a bucket. Reset shared state first so
    # the calls made above don't skew the counts.
    print("\n-- SR-6: rate limiting --")
    security._WINDOWS.clear()

    limit = security.RULES[1][1][0]          # the /auth/ rule
    codes = [c.get("/auth/me").status_code for _ in range(limit + 3)]
    check(f"  first {limit} /auth/me calls allowed", codes[:limit], [200] * limit)
    check("  next call throttled", codes[limit], 429)
    blocked = c.get("/auth/me")
    check("  429 carries Retry-After", bool(blocked.headers.get("retry-after")), True)
    check("  429 still carries security headers",
          blocked.headers.get("x-frame-options"), "DENY")

    security._WINDOWS.clear()
    print("  (health endpoint is exempt)")
    check("  60x /api/health all 200",
          {c.get("/api/health").status_code for _ in range(60)}, {200})

    # ── Phase 4: SR-9 audit trail ─────────────────────────────────────────────
    print("\n-- SR-9: audit trail --")
    security._WINDOWS.clear()
    audit_file = api.DATA_DIR / "audit.log"
    check("  audit.log created", audit_file.exists(), True)
    if audit_file.exists():
        body = audit_file.read_text(encoding="utf-8", errors="replace")
        check("  records csrf denials", "AUDIT csrf.blocked" in body, True)
        check("  records rate-limit blocks", "AUDIT ratelimit.blocked" in body, True)
        check("  lines carry ip=", "ip=" in body, True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)

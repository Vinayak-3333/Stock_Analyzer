# OIDC Integration & Security Hardening — Requirements

> Branch: `alt-approach`. Status: **complete for the current topology**
> (2026-10-25). Phases 1–5 done — auth verified against a real Google login,
> hardening in place, secrets closed. Phase 6 (merge to `master`) is
> deliberately deferred until a production backend exists; see the progress log
> for the reasoning and the checklist to resume.
>
> This is the design doc we execute step by step. Section 10 is the running
> record; read it before changing anything here.

---

## 1. Objective

Put the StockRadar IN backend and dashboard behind authentication using the
**OpenID Connect (OIDC)** protocol, and harden the surrounding transport /
API / secrets posture. Today the entire system is unauthenticated and
internet-reachable (Vercel frontend → backend); the goal is that only an
authenticated, authorized identity can read analysis data or trigger runs.

## 2. Current-state audit (baseline — what we are securing)

Audited on the `alt-approach` branch against `backend/api.py`,
`frontend/src/App.jsx`, and `requirements.txt`.

| # | Finding | Location | Severity |
|---|---------|----------|----------|
| 1 | All 10 API endpoints unauthenticated — no `Depends`/`Security`/API key/token | `backend/api.py` (all routes) | **Critical** |
| 2 | `POST /api/trigger?email=true` open → anyone can run the full pipeline and send Gmail | `api.py:354` | **Critical** (DoS + email abuse) |
| 3 | CORS fully open: `allow_origins/methods/headers=["*"]` | `api.py:228-233` | High |
| 4 | Frontend sends no `Authorization` header — bare `fetch` | `App.jsx:13` | High |
| 5 | Swagger `/docs` + `/openapi.json` publicly exposed | FastAPI default | Medium |
| 6 | Secrets in plaintext `.env`, now duplicated across two worktrees | project root | Medium |
| 7 | No security response headers (HSTS, X-Content-Type-Options, frame options, CSP) | `api.py` middleware | Medium |
| 8 | No rate limiting on any endpoint | backend-wide | Medium |
| 9 | No audit log of who triggered a run / accessed data | backend-wide | Low |

## 3. Threat model (what we protect against)

- **Unauthorized data access** — reading `/api/latest`, `/api/runs/{id}` (your
  proprietary scoring output) without credentials.
- **Unauthorized/abusive triggering** — hammering `/api/trigger` to burn
  compute (Yahoo/NSE quota, laptop CPU) and spam Gmail (risking the app
  password getting flagged).
- **Token/credential theft** — XSS in the SPA stealing access tokens; leaked
  `.env`.
- **CSRF** — a malicious site calling the backend using the user's ambient
  session.
- Out of scope for v1: multi-tenant isolation, per-record ACLs, WAF/DDoS at the
  edge (delegated to the hosting layer).

## 4. Functional requirements — OIDC

- **FR-1** Use OIDC **Authorization Code flow with PKCE** (never implicit flow).
- **FR-2** **BFF (Backend-for-Frontend) pattern**: the FastAPI backend performs
  the code↔token exchange, keeps OIDC tokens **server-side**, and hands the SPA
  an **httpOnly, Secure, SameSite** session cookie. Tokens never live in
  `localStorage`/JS (mitigates XSS token theft).
- **FR-3** Backend validates ID token: signature via the IdP's JWKS, plus
  `iss`, `aud`, `exp`, `nonce` claims.
- **FR-4** **Authorization allow-list**: only identities whose verified email is
  in an allowed set (e.g. `RECIPIENT_EMAIL` / an `ALLOWED_EMAILS` env var) get a
  session. Everyone else is rejected post-authentication.
- **FR-5** Login (`/auth/login` → IdP redirect), callback (`/auth/callback`),
  logout (`/auth/logout`, clears session), and `/auth/me` (current identity).
- **FR-6** Session lifecycle: expiry, sliding renewal or refresh-token use,
  server-side revocation on logout.
- **FR-7** Frontend gates the dashboard behind auth state; unauthenticated users
  see a login screen; 401s redirect to login.

## 5. Security-protocol (non-functional) requirements

- **SR-1 Endpoint protection** — every `/api/*` route requires a valid session
  (FastAPI dependency). `/api/trigger` additionally treated as privileged.
- **SR-2 CORS lockdown** — replace `["*"]` with an explicit origin allow-list
  (Vercel prod URL + `http://localhost:5173` dev) and `allow_credentials=True`
  (required for cookie-based sessions; `*` origin is illegal with credentials).
- **SR-3 CSRF protection** — SameSite=Lax/Strict cookie + a CSRF token on
  state-changing requests (`/api/trigger`, `/auth/logout`).
- **SR-4 Security headers** — HSTS, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, a minimal CSP.
- **SR-5 TLS everywhere** — cookies `Secure`; backend must be reachable only
  over HTTPS in prod (terminate TLS at nginx / the NAS reverse proxy).
- **SR-6 Rate limiting** — per-identity/IP limits, strictest on `/api/trigger`
  and `/auth/*`.
- **SR-7 Secrets hygiene** — OIDC client secret + session signing key out of
  source, in `.env` (and documented in `.env.nas.example` as placeholders);
  rotate the existing Gmail/API keys as part of this work (already a pending
  thread). Keep the two worktrees' `.env` in sync.
- **SR-8 Lock down `/docs`** — require auth or disable in prod.
- **SR-9 Audit trail** — log authenticated identity + action for triggers and
  logins.

## 6. Decisions (RESOLVED 2026-07-26)

- **D-1 Identity Provider → Google.** OIDC via Google (`accounts.google.com`
  discovery). Register an OAuth 2.0 Web client in Google Cloud Console; obtain
  client ID + secret; set the authorized redirect URI to the backend
  `/auth/callback`. Zero self-hosted IdP infra.
- **D-2 User model → single user.** Only the owner's verified Google email is
  authorized. Authorization = exact match against `ALLOWED_EMAILS`
  (defaults to `RECIPIENT_EMAIL`). Everyone else is rejected after login.
- **D-3 Session store → signed cookie.** Stateless, encrypted, httpOnly,
  Secure, SameSite session cookie (Authlib/Starlette `SessionMiddleware` or
  `itsdangerous`). No server-side store. Short TTL to bound the soft-revocation
  window; logout clears the cookie.
- **D-4 Deployment topology → to confirm, assumption below.** *Assumption:*
  prefer same-origin (frontend + `/api` behind the NAS nginx reverse proxy) so
  the session cookie is first-party (`SameSite=Lax`). If the Vercel frontend
  keeps calling the backend cross-origin, the cookie must be `SameSite=None;
  Secure` and we rely on the CSRF token (SR-3) + strict CORS origin allow-list.
  Cookie `SameSite`/domain will be env-driven so both topologies are supported.
  **Flag if the backend is directly internet-exposed without a reverse proxy.**

## 7. Proposed phased plan (execution order)

1. **Phase 0 — Decisions & scaffolding.** Resolve D-1..D-4. Add deps
   (`authlib`, `itsdangerous`/session lib, `slowapi` for rate limiting). Add
   OIDC + session config to `.env` / `.env.nas.example`.
2. **Phase 1 — Backend auth core.** OIDC discovery + PKCE code flow via Authlib;
   `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me`; session cookie
   issuance; ID-token validation + email allow-list.
3. **Phase 2 — Protect the API.** Session-required FastAPI dependency on all
   `/api/*`; mark `/api/trigger` privileged; lock CORS (SR-2); add CSRF (SR-3).
4. **Phase 3 — Frontend integration.** Auth context, login screen, 401→login
   redirect, send credentials with `fetch`, CSRF token wiring.
5. **Phase 4 — Hardening.** Security headers (SR-4), rate limiting (SR-6),
   `/docs` lockdown (SR-8), audit logging (SR-9).
6. **Phase 5 — Secrets & deploy.** Rotate existing secrets, sync worktree
   `.env`s, verify TLS + cookie flags in the NAS/Vercel topology, document.
   *Carried in from Phase 4:* turn on `SECURITY_HSTS_ENABLED`, and behind the
   NAS nginx set `TRUST_PROXY_HEADERS=true` — in that topology the rate limiter
   otherwise sees the proxy's IP for every unauthenticated caller, collapsing
   them into a single shared bucket.
7. **Phase 6 — Validate & merge.** End-to-end test on `alt-approach`; then merge
   to `master`.

## 8. Dependencies to add (tentative)

- `authlib` — OIDC/OAuth2 client, JWKS validation, PKCE.
- `itsdangerous` — signed session cookies (or `starlette` `SessionMiddleware`).
- ~~`slowapi` (or equivalent) — rate limiting.~~ **Not taken.** Phase 4 ships a
  ~90-line sliding-window limiter in `backend/security.py` instead; see the
  Phase 4 progress-log entry for the reasoning.
- Optional: `redis` (already present) for server-side sessions.

## 9. Out of scope (v1)

Multi-tenant data isolation, per-record ACLs, edge WAF/DDoS, SSO across other
apps, hardware MFA enrollment (delegated to the IdP).

## 10. Progress log

- **Phase 1 — DONE** (commit `1e20fee`). Google OIDC Auth Code + PKCE, BFF
  session cookie, ID-token validation, email allow-list. Verified live end to
  end: `/auth/login → Google → /auth/callback → /auth/me` returns identity;
  server log shows `Login OK: <email>`.
- **Phase 2 — DONE** (this commit). `require_user` attached to all 10 `/api/*`
  routes; added public `/api/health` and repointed both Docker healthchecks
  (`Dockerfile.backend`, `docker-compose.nas.yml`) to it. CORS locked to an
  explicit origin allow-list (`CORS_ALLOW_ORIGINS`, default
  `http://localhost:5173`) with `allow_credentials=True`. Verified: protected
  routes return 401 unauthenticated; `/api/health` stays 200.
  - **Sequencing change:** CSRF (SR-3) **moved from Phase 2 to Phase 3.** The
    double-submit token requires the SPA to echo it on state-changing calls, so
    it belongs with the frontend work. Interim protection: the session cookie is
    `SameSite=Lax`, which blocks cross-site POSTs. Revisit if we switch to
    `SameSite=None` for a cross-origin Vercel topology.
  - **Known interim effect:** the existing dashboard's `/api/*` calls now 401
    until Phase 3 wires credentials + login UI. Expected.
- **Phase 3 — DONE** (this commit). Frontend integration + CSRF.
  - **Backend** (`backend/auth.py`): CSRF token minted into the session at
    login (rotated per login), exposed to the SPA via `/auth/me` **and** a
    JS-readable `stockradar_csrf` cookie (re-issued on every `/auth/me` so
    pre-CSRF sessions heal). New `require_csrf` dependency compares the
    `X-CSRF-Token` header against the **session** copy with
    `secrets.compare_digest` — strictly stronger than cookie-vs-header
    double-submit, since an attacker who can only replay the ambient cookie
    still cannot read the token from our origin. Attached to
    `POST /api/trigger` (`backend/api.py`) and `POST /auth/logout`.
  - **Frontend** (`frontend/src/App.jsx`): a `withCredentials` axios instance
    replaces bare `axios`; a request interceptor adds `X-CSRF-Token` to every
    non-GET/HEAD/OPTIONS call. `AUTH` base is derived as `API` minus the
    `/api` suffix, so it resolves for both topologies. On load the app calls
    `/auth/me` and gates on four states — `checking` (splash), `in`
    (dashboard), `out` (Google login screen), `unreachable` (backend-down
    screen with a retry). A mid-poll 401 drops back to the login screen
    instead of flashing "backend offline". Header gained an avatar + sign-out
    button. `?auth_error=…` from the callback is surfaced on the login screen.
    Styles in `index.css` (`.auth-*`, `.user-chip`, `.logout-btn`).
  - **D-4 resolved — both topologies, no code branch needed.** Dev
    (`:5173`→`:8000`) is cross-*origin* but same-*site* (SameSite ignores
    port), so the default `SESSION_COOKIE_SAMESITE=lax` works as-is; CORS +
    `withCredentials` cover the origin difference. A cross-site Vercel
    deployment needs `SESSION_COOKIE_SAMESITE=none` +
    `SESSION_COOKIE_SECURE=true` in `.env` — already env-driven, and the CSRF
    token is the compensating control there.
  - **Verified** by `backend/_verify_phase3.py` (ad-hoc, forges a
    SessionMiddleware cookie so no Google round-trip is needed): 18/18 —
    unauthenticated 401s, `/api/health` public, authenticated 200s, CSRF 403
    on absent/mismatched header, logout expiring both cookies. Frontend
    `npm run lint` clean and `npm run build` succeeds.
  - **Verified live against a real Google login** (2026-10-24, Chrome,
    `localhost:5173` → `localhost:8000`): logged-out gate shows the login
    screen, `/auth/login` → Google → `/auth/callback` → `Login OK:
    <email>` → dashboard renders; `/auth/me` returns the identity + token;
    session survived a backend restart (persistent `SESSION_SECRET`); in-page
    checks confirmed the session cookie is invisible to JS while the CSRF
    cookie is readable; `POST /api/trigger` without the header returned 403
    **without starting a run**; sign-out returned to the login screen; a
    second login succeeded.
  - **Bug found and fixed during live testing — CORS preflight (latent since
    Phase 2).** The dashboard sends `Cache-Control: no-cache` on its three
    polling GETs, which makes them non-simple requests. `Cache-Control` was
    missing from `allow_headers`, so the preflight 400'd and the SPA showed an
    opaque "Network Error" / "Backend offline" banner *after* a successful
    login. Phase 2 never caught this because every `/api/*` call was 401ing
    before CORS mattered. `Cache-Control` added to `allow_headers`
    (`backend/api.py`).
  - **Carried forward:** sessions are stateless signed cookies (D-3), so
    logout is *soft* revocation — a copy of the cookie taken before logout
    stays valid until `SESSION_MAX_AGE` (8h). Bounding that further needs a
    server-side revocation store; out of scope for v1.
- **Phase 4 — DONE** (this commit). Hardening. Two new modules:
  `backend/audit.py` (no dependencies, so both auth.py and security.py can use
  it without a cycle) and `backend/security.py`.
  - **SR-4 security headers** — `SecurityHeadersMiddleware` sets
    `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
    `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`,
    a `Permissions-Policy` denying geolocation/mic/camera, and a CSP. The API
    serves JSON so it gets the strictest possible policy (`default-src 'none'`);
    the `/docs` routes get a looser one allowing the jsDelivr Swagger bundle.
    HSTS is **opt-in** via `SECURITY_HSTS_ENABLED` — it is ignored over plain
    http and would only be noise on a localhost dev box, so it belongs wherever
    TLS actually terminates.
  - **SR-6 rate limiting** — sliding-window limiter keyed by session identity
    when there is one, else client IP, so a logged-in user gets their own
    budget rather than sharing one per NAT. Rules are path-prefix based,
    longest match wins: `/api/trigger` 5/hour (a run is minutes of CPU, Yahoo
    and NSE quota, and an email), `/auth/*` 20/min, `/api/*` 240/min —
    comfortably above the dashboard's 6 req/min idle and 36 req/min while a run
    is in flight. `/api/health` is exempt so Docker healthchecks don't get
    throttled. Returns 429 with `Retry-After`. All four numbers are env-tunable.
    `X-Forwarded-For` is only honoured when `TRUST_PROXY_HEADERS` is set —
    otherwise anyone could forge their way past a per-IP limit.
    - **Not slowapi, deliberately.** The doc allowed "slowapi or equivalent".
      PyPI was unreachable from the dev box at implementation time, but the
      call stands on its own: this backend is a single uvicorn process, which
      is exactly the regime where slowapi's default in-memory backend works and
      no more, so the dependency would have bought nothing while forcing a NAS
      image rebuild. Path-prefix rules also avoid decorating ten routes. If
      this ever runs multiple workers, swap `_WINDOWS` for Redis (already a
      dependency) behind the same middleware boundary.
  - **SR-8 `/docs` lockdown** — FastAPI's built-in `docs_url`/`redoc_url`/
    `openapi_url` are set to `None` and `/docs` + `/openapi.json` re-served
    behind `require_user`. The schema is protected too: publishing the full
    route list unauthenticated would undo most of the point. `DOCS_ENABLED=false`
    removes them entirely.
  - **SR-9 audit trail** — `stockradar.audit` logger writing to the console and
    a rotating `backend/data/audit.log` (gitignored). Events: `login.ok`,
    `login.failed`, `login.rejected`, `logout`, `trigger.accepted`,
    `trigger.rejected`, `csrf.blocked`, `ratelimit.blocked`. Every line carries
    `identity=` and `ip=`. The one to watch is `login.rejected` — it means
    someone completed Google auth and came back with a real identity that is
    not on the allow-list.
  - **Bug fixed: middleware ordering.** CORS was added first, which made it
    *innermost*, so any 401/403/429 raised by an outer middleware reached the
    browser without CORS headers — an opaque "Network Error" with the real
    status invisible. The chain is now explicit and ordered
    `CORS → SecurityHeaders → NoCache → Session → RateLimit → routes`: the rate
    limiter must sit inside Session to read the identity, and CORS must be
    outermost so errors stay legible.
  - **Bug fixed: 401 vs 403 on `/api/trigger`.** Moving `require_user` into the
    handler signature made FastAPI evaluate the decorator-level `require_csrf`
    first, so an anonymous caller got a misleading 403 about a CSRF token
    instead of a 401 — which would also have stopped the SPA bouncing an
    expired session to the login screen. Both are back in `dependencies=[]` in
    the right order, and the verifier now asserts it.
  - **Verified** by `backend/_verify_security.py` — 40/40. This replaces
    `_verify_phase3.py` and now covers Phase 2, 3 and 4 in one script. Also
    checked live in Chrome: dashboard still loads through the reordered stack
    with no CSP violations, `/docs` renders for an authenticated user and 401s
    for anyone else.

- **Phase 5 — PARTIALLY DONE** (config shipped; secret rotation still open).
  Three things that would have broken auth anywhere but the dev box:
  - **nginx never proxied `/auth/`.** The OIDC endpoints live at the backend
    root, not under `/api`, so on the NAS they fell through to the SPA fallback
    and returned `index.html` with a **200**. Worse than failing: the dashboard
    would have read that as a successful `/auth/me` and treated an HTML page as
    an identity. Fixed in `frontend/nginx.nas.conf`.
  - **`docker-compose.nas.yml` passed no OIDC/session/hardening env**, so the
    container would have booted with auth disabled — `/auth/*` 503, every
    `/api/*` 401, no way in. Added, with `TRUST_PROXY_HEADERS` defaulting to
    `true` there since requests arrive via the nginx container.
  - **`api.py` backfilled missing keys from `.env.nas.example`** — a gitignored
    file holding real credentials, i.e. an "example" file silently supplying
    production config. Removed; everything it provided already had an identical
    code default, so the change is behaviour-neutral. `.env` is now the single
    source, documented by a committable `.env.example`.
  - **Secrets — CLOSED 2026-10-25.** The long-running "rotate the Gmail app
    password" thread was based on a wrong premise, corrected by auditing git
    rather than the notes:
    - The exposed credential was **hardcoded at `Analyzer.py:59`**, not in an
      env file. `.env.nas.example` only ever held placeholders. It is reachable
      in **13 commits on both `origin/master` and `origin/alt-approach`**.
    - The **live** password was never committed — `git log -S` finds no trace of
      it on either spacing variant. It had already been rotated at some earlier
      point, so there was nothing to rotate.
    - Resolution: the leaked app password was **revoked** in Google
      (2026-10-25); the live one was correctly left alone and still
      authenticates. Verified with `backend/_verify_gmail.py` — SMTP auth OK,
      both worktree `.env` files in sync, secret never printed.
    - The four market-data keys are moot — the dead-code sweep deleted the
      provider chain, so they were provably unused.
    - **Accepted residual risk:** the revoked string stays in those 13 public
      commits. Rewriting history (`git filter-repo` + force-push both branches)
      was considered and declined — a revoked credential in history is noise,
      and the rewrite would be disruptive for no security gain. Revisit only if
      the repo should be clean for its own sake.
- **Phase 6 — DEFERRED BY DECISION, not forgotten** (2026-10-24). The merge to
  `master` is deliberately **not** being done, because there is no production
  backend: everything runs on the laptop, and the NAS/Vercel deployments are
  aspirational. Merging would flip the deployed Vercel dashboard from "works,
  unauthenticated" to "login screen that can never complete", since no
  reachable backend is configured for OIDC. That trades a working dashboard for
  no security gain.
  - **`master` therefore remains unauthenticated by choice.** It is a public
    repo serving a dashboard with no secrets in the payload; the exposure is
    the analysis output, not credentials.
  - **Do this when real infrastructure exists** (in order):
    1. Stand up a reachable backend; note its public origin.
    2. Google Cloud Console → Credentials → add that origin's
       `/auth/callback` as an Authorized redirect URI. One per environment;
       it must match scheme, host, port and path exactly.
    3. Set on the backend: `OIDC_REDIRECT_URI`, `FRONTEND_URL`,
       `CORS_ALLOW_ORIGINS` (the Vercel origin), and a stable `SESSION_SECRET`.
    4. If the SPA and API are on different sites (Vercel → separate backend
       domain), set `SESSION_COOKIE_SAMESITE=none` **and**
       `SESSION_COOKIE_SECURE=true`. `none` without `Secure` is rejected by
       every modern browser. Behind the NAS nginx it is same-origin, so `lax`
       is correct and no change is needed.
    5. Turn on `SECURITY_HSTS_ENABLED` wherever TLS terminates.
    6. Set `VITE_API_URL` for the Vercel build, then merge and deploy.
    7. Re-run `backend/_verify_security.py` against the deployed host and
       confirm a real Google login end to end before announcing it done.

### New environment variables (Phase 4)

| Var | Default | Purpose |
|---|---|---|
| `SECURITY_HSTS_ENABLED` | `false` | Send HSTS. Turn on where TLS terminates. |
| `SECURITY_HSTS_MAX_AGE` | `31536000` | HSTS max-age seconds. |
| `TRUST_PROXY_HEADERS` | `false` | Honour `X-Forwarded-For`. **Only** behind a trusted proxy. |
| `RATE_LIMIT_ENABLED` | `true` | Master switch for SR-6. |
| `RATE_LIMIT_TRIGGER` | `5` | `/api/trigger` allowance per hour. |
| `RATE_LIMIT_AUTH` | `20` | `/auth/*` allowance per minute. |
| `RATE_LIMIT_API` | `240` | `/api/*` allowance per minute. |
| `DOCS_ENABLED` | `true` | Serve `/docs` + `/openapi.json` to an authenticated user. |
| `AUDIT_LOG_MAX_BYTES` | `5242880` | Audit log rotation size. |
| `AUDIT_LOG_BACKUPS` | `5` | Rotated audit files kept. |

# OIDC Integration & Security Hardening — Requirements

> Branch: `alt-approach`. Status: **requirements / planning** (no code changes yet).
> Author note: this is the design doc we execute step by step. Nothing here is
> merged to `master` until the approach is validated on this branch.

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
7. **Phase 6 — Validate & merge.** End-to-end test on `alt-approach`; then merge
   to `master`.

## 8. Dependencies to add (tentative)

- `authlib` — OIDC/OAuth2 client, JWKS validation, PKCE.
- `itsdangerous` — signed session cookies (or `starlette` `SessionMiddleware`).
- `slowapi` (or equivalent) — rate limiting.
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

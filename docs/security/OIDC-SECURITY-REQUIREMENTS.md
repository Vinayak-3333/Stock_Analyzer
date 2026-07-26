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

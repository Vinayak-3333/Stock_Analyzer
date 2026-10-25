# Daily Notes

Running record of what changed, day by day, and **why** — so a new session (or a
future you) can pick up without re-deriving context.

**Conventions**
- Newest entry first. One `##` heading per working day, ISO date.
- Each entry: **Shipped** (what changed), **Decisions** (choices worth
  remembering, with reasoning), **Found** (surprises/bugs), **Open** (carried
  forward).
- Decisions get a stable ID `DN-nn` and are indexed below. These are *project*
  decisions; the OIDC design doc keeps its own `D-1..D-4` for IdP/session/
  topology choices — different series, don't mix them.
- Record the reasoning, not just the outcome. A decision without its "why"
  gets silently reversed six weeks later.
- This file is the narrative. Durable architectural facts belong in
  `CLAUDE.md`; the security plan lives in `docs/security/`.

## Decision index

| ID | Date | Decision |
|----|------|----------|
| [DN-01](#dn-01) | 2026-10-24 | Regime payload reads the keys `regime.py` actually emits; trend normalised at the payload boundary |
| [DN-02](#dn-02) | 2026-10-24 | Market Pulse shows a 1-day NIFTY change; label corrected rather than the value changed |
| [DN-03](#dn-03) | 2026-10-24 | CSRF validated header-vs-**session**, not cookie-vs-header |
| [DN-04](#dn-04) | 2026-10-24 | No code branch for deployment topology — dev is cross-origin but same-site |
| [DN-05](#dn-05) | 2026-10-24 | Middleware order is load-bearing: CORS outermost, RateLimit inside Session |
| [DN-06](#dn-06) | 2026-10-24 | `require_user` must precede `require_csrf` in `dependencies=[]` |
| [DN-07](#dn-07) | 2026-10-24 | Hand-rolled rate limiter instead of slowapi |
| [DN-08](#dn-08) | 2026-10-24 | Delete all unreachable code, including built-but-unwired features |
| [DN-09](#dn-09) | 2026-10-24 | Keep `httpx` and the ML stack in requirements despite appearing unused |
| [DN-10](#dn-10) | 2026-10-24 | Cherry-pick the regime fix to `master` instead of waiting for the Phase 6 merge |
| [DN-11](#dn-11) | 2026-10-24 | `.env` is the single config source; no backfill from any example file |
| [DN-12](#dn-12) | 2026-10-24 | Defer Phase 6 — `master` stays unauthenticated **by choice** |
| [DN-13](#dn-13) | 2026-10-25 | Secrets: revoke the leaked credential, don't rotate the live one; decline the history rewrite |
| [DN-14](#dn-14) | 2026-10-25 | Keep a daily notes file in the repo, with a decision index |
| [DN-15](#dn-15) | 2026-10-25 | Deploy to Oracle Always Free, not AWS — AWS micro RAM and 12-month expiry don't fit |
| [DN-16](#dn-16) | 2026-10-25 | One HTTPS origin behind Caddy; drop the split-Vercel idea |
| [DN-17](#dn-17) | 2026-10-25 | Build images on the instance instead of pulling from ghcr |
| [DN-18](#dn-18) | 2026-10-25 | DuckDNS for the hostname; reserve the Oracle public IP |

---

## 2026-10-25

### Shipped
- `e7d2237` — closed the secrets thread; design doc marked Phases 1–5 complete.
- `backend/_verify_gmail.py` (committed `b6e8178` late on the 24th) used to
  confirm SMTP auth and that both worktree `.env` files match, without printing
  the secret.

### Found
- **The July "rotate the Gmail password" item rested on a wrong premise.**
  Auditing git instead of the notes showed the exposed credential was
  **hardcoded at `Analyzer.py:59`**, never in an env file — `.env.nas.example`
  only ever held placeholders. It is reachable in **13 commits on both
  `origin/master` and `origin/alt-approach`**.
- **The live password was never committed.** `git log -S` finds no trace on
  either spacing variant, so it had already been rotated at some earlier point.
  There was nothing to rotate.

### <a id="dn-13"></a>DN-13 — Revoke, don't rotate; decline the history rewrite
Revoked the leaked app password in Google and left the live one alone, since the
live one was never exposed. Verified afterwards that the revocation didn't
disturb it.

Declined rewriting history (`git filter-repo` + force-push both branches) to
scrub the dead string. A **revoked** credential in history is noise, not
exposure, and the rewrite is disruptive for no security gain. Revisit only if
the repo should be clean for its own sake.

### <a id="dn-14"></a>DN-14 — Keep daily notes in the repo
Started this file. It lives in the repo rather than in assistant memory so it
is version-controlled and travels with the project; `CLAUDE.md` tells future
sessions to read it at the start and append at the end. Motivated by two cases
*that same day* where stale notes actively misled the work: the regime
collector was recorded as a failing VIX fetch when it was key drift, and the
Gmail item rested on a wrong premise about where the credential leaked.

---

## 2026-10-25 (later) — deployment setup

Started deploying frontend + backend. Nothing is deployed yet; this is the
repo-side groundwork plus the architecture decisions.

### Shipped
- `539349c` — `docker-compose.cloud.yml`, `Caddyfile`, `docs/DEPLOYMENT.md`,
  cloud section in `.env.example`, and a `Dockerfile.backend` fix.

### Found
- **The container would have crashed on first boot.** `Dockerfile.backend` ran
  `uvicorn backend.api:app` from `/app`, but `api.py` imports its siblings bare
  (`from auth import ...`), which only resolves with `backend/` on `sys.path`.
  Reproduced it — `ModuleNotFoundError: No module named 'auth'` — and fixed it
  by launching from `backend/`, as the local run and `CLAUDE.md` already
  describe. **Broken since Phase 1**; never noticed because the NAS deployment
  was never run with the auth code. Would have surfaced on the instance as an
  instant crashloop, easily misattributed to Oracle networking.
- **Google will not accept the obvious deployment.** Redirect URIs must be
  HTTPS and must not be raw IPs — localhost is the only exemption. So a
  LAN-only NAS deployment cannot use Google OIDC at all, and a bare cloud IP
  is equally unusable. TLS on a real hostname is mandatory, which drove
  DN-16 and DN-18.
- **The published images are unusable for this.** CI builds **amd64 only** and
  triggers only on `master` — so the ghcr images are both the wrong
  architecture for Ampere ARM64 and contain none of the OIDC code.

### <a id="dn-15"></a>DN-15 — Oracle Always Free over AWS Free Tier
AWS's EC2 free tier is 12 months and its micro instances are ~1 GB RAM; a
2,000-symbol pandas/yfinance run would very likely exhaust that, and the clock
runs out regardless. Oracle's Always Free Ampere allowance (up to 4 OCPU /
24 GB) is indefinite and comfortably sized. Caveat recorded in the runbook: A1
capacity is frequently exhausted, and the AMD micro fallback is 1 GB and not a
substitute.

### <a id="dn-16"></a>DN-16 — One HTTPS origin behind Caddy; no split Vercel frontend
nginx already serves the SPA and proxies `/api` and `/auth`, so putting Caddy
in front for TLS keeps everything on a single origin: `SameSite=lax`, no CORS,
one redirect URI. A Vercel frontend would be a *different site* from the
backend, forcing `SameSite=none` + `Secure` plus a CORS allow-list — more
moving parts and a weaker cookie posture for no benefit. **This makes Vercel
redundant for this project.** Caddy over nginx+certbot purely for automatic
ACME including renewal.

### <a id="dn-17"></a>DN-17 — Build on the instance, don't pull from ghcr
Sidesteps both CI problems at once (amd64-only, master-only) with no workflow
changes and no QEMU. `python:3.11-slim` is multi-arch and numpy/pandas/duckdb
all ship aarch64 wheels, so nothing compiles. Publishing multi-arch images
from CI is a sensible follow-up but a slow one — emulated ARM builds of a
pandas image are expensive — so it should not block the first deploy.

### <a id="dn-18"></a>DN-18 — DuckDNS for the hostname; reserve the Oracle IP
No domain owned, and the deployment needs one. `yourname.duckdns.org` is free
and satisfies Google's public-suffix rule either way: if `duckdns.org` is a PSL
entry that is the suffix, otherwise the suffix is `.org`. Confirmed by
homelab setups running Google OAuth against DuckDNS in practice; the failures
reported there are `redirect_uri_mismatch`, an exact-string issue, not domain
rejection.

Paired requirement: **reserve the Oracle public IP** (free). The default is
ephemeral and changes on stop/start, which would silently break DNS *and* the
registered redirect URI.

### Open
- Nothing is deployed yet. Instance, firewall (both layers), DuckDNS record,
  redirect URI and `.env` are all still to do — see `docs/DEPLOYMENT.md`.
- **Phase 6 becomes unblocked once this is proven.** DN-12 deferred the merge
  to `master` for want of a production backend; this deployment is that
  backend.
- Deploying moots the scheduler sleep-freeze problem — an always-on VM does not
  sleep, so the July nudge fix stops being load-bearing.
- Scheduler on the laptop is armed for **2026-10-26 09:15 IST** — still the one
  genuinely unverified thing in the project, and only meaningful if the laptop
  is awake then.

---

## 2026-10-24

A long session: fixed a months-old data bug, finished the OIDC workstream
through Phase 5, and swept ~3,240 lines of dead code.

### Shipped
| Commit | What |
|---|---|
| `f8dd8bc` | Regime collector fix |
| `eb3a056` | Phase 3 — frontend login, CSRF, CORS preflight fix |
| `9a758f1` | Phase 4 — security headers, rate limiting, `/docs` lockdown, audit trail |
| `f98a83c` | Dead-code sweep, −3,238 lines |
| `dce693e` | Phase 5 config — made the OIDC setup actually deployable |
| `22016a2` | Concluded the workstream for the current topology |
| `871686b` | Regime fix cherry-picked to `master` (deployed: Vercel + ghcr build #18, green) |

### Found
- **The regime collector was never a fetch failure.** The 2026-07-23 audit
  recorded it as a silently failing VIX/NIFTY fetch. The upstream Yahoo tickers
  were healthy the whole time: `_market_payload` read `india_vix`,
  `nifty_change_1d` and `regime_label`, **none of which
  `compute_regime_features()` emits**. Both lookups missed and fell back to
  defaults, hard-coding the payload to `NEUTRAL / None / 0.0` regardless of
  upstream health. Dead for months because nothing cross-checks producer and
  consumer.
- **A CORS preflight bug that only appeared once login worked.** The dashboard
  sends `Cache-Control: no-cache` on its polling GETs, making them non-simple
  requests; that header wasn't in `allow_headers`, so the preflight 400'd and
  the SPA showed an opaque "Network Error" *after* a successful login. Phase 2
  never caught it because every `/api/*` call was 401ing before CORS mattered —
  **auth bugs mask CORS bugs.**
- **nginx never proxied `/auth/`.** The OIDC endpoints live at the backend root,
  not under `/api`, so on the NAS they fell through to the SPA fallback and
  returned `index.html` with a **200**. Worse than failing: the dashboard would
  have read that as a successful `/auth/me` and treated an HTML page as an
  identity.
- **`docker-compose.nas.yml` passed no OIDC env at all** — the container would
  have booted with auth disabled, 503 on `/auth/*` and 401 everywhere, with no
  way in.
- **An "example" file was supplying production config.** `api.py` backfilled any
  key missing from `.env` out of `.env.nas.example` — gitignored, and holding
  real credentials.

### Decisions

#### <a id="dn-01"></a>DN-01 — Fix the regime payload at the consumer, and normalise the trend label
Pointed `_market_payload` at the keys `regime.py` actually emits, added the
missing `nifty_change_1d`, and mapped `BULL`/`BEAR` → `BULLISH`/`BEARISH` at the
payload boundary because the `runs` table, email template and dashboard badges
all speak the long form. Added warnings so a genuinely empty regime is no longer
silent. **If you add a key to the regime payload, add it on both sides.**

#### <a id="dn-02"></a>DN-02 — Market Pulse shows a 1-day change
The panel was labelled "NIFTY 50 · 5d" but fed a value intended to be 1-day.
Corrected the label rather than the value: a per-run snapshot wants a day change,
and the 5d/20d figures remain available in the nested `regime` dict.

#### <a id="dn-03"></a>DN-03 — CSRF compares the header against the session
Classic double-submit compares a cookie to a header. We compare the
`X-CSRF-Token` header against the **session** copy with
`secrets.compare_digest`. Strictly stronger: an attacker who can replay the
ambient cookie still cannot read the token from our origin, so they cannot
produce the header. The readable cookie is kept so the SPA can self-heal.

#### <a id="dn-04"></a>DN-04 — One code path for both topologies
Resolved the long-open D-4 question with no branch needed. Dev
(`:5173` → `:8000`) is cross-**origin** but same-**site** — SameSite ignores
port — so the default `SESSION_COOKIE_SAMESITE=lax` works as-is; CORS plus
`withCredentials` cover the origin difference. Only a genuinely cross-site
deployment needs `none` + `Secure`, and that is already env-driven.

#### <a id="dn-05"></a>DN-05 — Middleware order is load-bearing
Chain is `CORS → SecurityHeaders → NoCache → Session → RateLimit → routes`.
RateLimit must sit **inside** Session (it reads `request.session` to give a
logged-in user their own budget instead of one shared per NAT). CORS must be
**outermost** so a 401/403/429 from an inner middleware still carries CORS
headers — previously CORS was added first, making it innermost, so such errors
reached the browser as opaque network errors with the real status invisible.

#### <a id="dn-06"></a>DN-06 — `require_user` before `require_csrf`
FastAPI evaluates decorator-level `dependencies=[]` before parameter-level ones.
Moving `require_user` into the handler signature made anonymous callers get a
misleading 403 about a CSRF token instead of a 401 — which would also have
stopped the SPA redirecting an expired session to the login screen. Both stay in
`dependencies=[]`, in that order, and the verifier asserts it.

#### <a id="dn-07"></a>DN-07 — Hand-rolled rate limiter, not slowapi
The design doc allowed "slowapi **or equivalent**". PyPI was unreachable at the
time, but the call stands on its own: this is a single uvicorn process, exactly
the regime where slowapi's default in-memory backend works and no more. It would
have bought nothing while forcing a NAS image rebuild, and path-prefix rules
avoid decorating ten routes. Swap `_WINDOWS` for Redis behind the same
middleware boundary if this ever runs multiple workers.

#### <a id="dn-08"></a>DN-08 — Delete everything unreachable
Built an import graph from the real entry point and deleted all of it, including
`core/alerts/` and `backtest/engine.py` — fully built but never wired to
anything. Recoverable from git if ever wanted. **Kept** `check_status.py` and
`test_new_apis.py` (documented ad-hoc tools), every `__init__.py`, and all of
`index.css`: its classes are composed dynamically
(`badge ${trend.toLowerCase()}`), so static pruning would produce confident
false positives and break styling silently.

#### <a id="dn-09"></a>DN-09 — Keep `httpx` and the ML stack
`httpx` is a **runtime** dependency of Authlib's Starlette integration and is
never imported directly — a static scan flags it as unused, and removing it
would break login at runtime with lint and tests still green. Annotated it in
`requirements.txt` so the next sweep doesn't repeat the mistake. The
`redis`/`scikit-learn`/`xgboost`/`shap` block is kept deliberately: it is what
the queued factor re-weighting work needs.

#### <a id="dn-10"></a>DN-10 — Cherry-pick the regime fix to `master`
The fix is independent of the security work, so it shipped immediately rather
than waiting for the Phase 6 merge. Verified byte-identical by blob hash, not
just a clean apply.

#### <a id="dn-11"></a>DN-11 — `.env` is the single config source
Removed the `.env.nas.example` backfill. Everything it actually supplied
(`DEEP_ANALYSIS_COUNT`, `FUNDAMENTALS_CACHE_TTL_DAYS`) already had an identical
code default, so the change is behaviour-neutral. Added a committable,
placeholder-only `.env.example` with a tight `.gitignore` negation — verified
that `.env` and `.env.nas.example` are still refused by `git add`.

#### <a id="dn-12"></a>DN-12 — Defer Phase 6; `master` stays unauthenticated by choice
There is no production backend — everything runs on the laptop; NAS and Vercel
are aspirational. Merging would flip the deployed dashboard from "works,
unauthenticated" to "login screen that can never complete", trading a working
dashboard for no security gain. The design doc carries an ordered checklist to
resume once a reachable backend exists.

### Verification that day
Live Google login end to end in Chrome; `_verify_security.py` 40/40; all 31
remaining modules import; frontend lint clean and build 10.0s → 4.1s;
`build_email_body` reproduced a correct 137-line email from real run-47 data.

### Also
Runs 47 and 48 were the first to persist real regime data
(`BEARISH / 16.86 / −0.39`), confirming DN-01 end to end. Both were triggered
manually from the dashboard — the second was identified from the new audit log
within hours of it being built.

---

## Before 2026-10-24

Not reconstructed here — see `docs/security/OIDC-SECURITY-REQUIREMENTS.md`
section 10 for the Phase 1–2 record (2026-07-26/27), `CLAUDE.md` for the
data-source freshness audit (2026-07-23), and `git log` for the July pipeline
work.

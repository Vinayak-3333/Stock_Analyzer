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
| [DN-19](#dn-19) | 2026-10-25 | Oracle signup blocked → Tailscale Funnel now, cloud VM later |
| [DN-20](#dn-20) | 2026-10-25 | Trust `X-Forwarded-For` behind Funnel — verified unspoofable, not assumed |
| [DN-21](#dn-21) | 2026-10-25 | GCP `e2-micro` + Funnel (not Caddy) as the always-on host |
| [DN-22](#dn-22) | 2026-10-25 | Pull CI images rather than build on 1 GB; `TIER2_WORKERS=4` + 2 GB swap |
| [DN-23](#dn-23) | 2026-10-25 | Phase 6 — merged `alt-approach` to `master`; `master` is no longer unauthenticated |

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

### <a id="dn-19"></a>DN-19 — Oracle signup blocked; Tailscale Funnel as the near-term host
Oracle rejected the account creation (a common failure for Indian signups,
usually at card verification). Reviewed the alternatives with current terms
checked rather than from memory:

- **GCP always-free `e2-micro`** — confirmed still free indefinitely, but 1 GB
  RAM and free *only* in `us-west1`/`us-central1`/`us-east1`; the free disk
  allowance is `pd-standard`, while new VMs often default to the billable
  `pd-balanced`. It is **x86_64**, which means the existing amd64 CI images
  would work — dispatching the workflow against `alt-approach` would produce
  usable images and avoid an `npm`/Vite build on a 1 GB box.
- **Hetzner** — my earlier €3.79 figure was **stale**; prices rose through 2026
  and CX22 is being retired in favour of CX23 at roughly €4.29/mo ex-VAT.
- **Tailscale Funnel** — free, and the only option that needs **no payment
  method at all**, which matters because the thing that blocked Oracle would
  likely block GCP/AWS/Hetzner too.

Chose Funnel now, cloud later. It also *simplifies* the stack: Funnel
terminates TLS with a real `*.ts.net` certificate, so Caddy, ACME, open
firewall ports and the DNS record all drop out, and it works behind CGNAT —
the exact problem that made the home-IP idea unworkable.

**Accepted trade-off, flagged and confirmed:** no always-on hardware is
available, so this runs on the laptop. The site is down while the machine
sleeps, and APScheduler timers freeze with it — so scheduled 09:15/15:30 runs
stay best-effort until a cloud host exists. That is the same limitation
`ec4c043` exists to mitigate.

`docker-compose.funnel.yml` binds the frontend to **127.0.0.1 only**, so
nothing is reachable until `tailscale funnel` is explicitly started, and the
dashboard is never exposed to whatever local network the laptop joins next.
`TRUST_PROXY_HEADERS` defaults to **off** here because Funnel's
`X-Forwarded-For` behaviour is unverified for this project; the runbook says to
check `audit.log` after a login and flip it on only if a real client IP appears.

---

## 2026-10-25 (evening) — first live deployment

**StockRadar is deployed and publicly reachable** at
`https://laptop-svtk11i1.tail7679a0.ts.net`, behind Google login, over TLS.
First time the project has run anywhere but localhost.

### Verified end to end
Public `/api/health` → `{"status":"ok"}` from an external vantage point (not
just locally, which would have proved nothing — MagicDNS resolves the hostname
to the tailnet IP from this machine, so a local curl never tests the public
path). Valid Tailscale certificate. `/auth/me` → 401 publicly. Real Google
login through Funnel → dashboard. CSRF-protected `/api/trigger` → a full
400-stock run in 203 s, email sent, `BEARISH / 16.86 / −0.39` persisted.
Security headers including HSTS survived the Funnel → nginx → backend chain.

### Found
- **No `.dockerignore` existed.** `Dockerfile.backend` builds with the project
  root as context and does `COPY . .`, so the real `.env` — OIDC client
  secret, Gmail app password, `SESSION_SECRET` — plus the full `.git` history
  would have been baked into an image layer, where `docker history` exposes it
  and any push carries it. Caught before the first build. Context 257 → 108 MB.
  The published ghcr images are unaffected by the `.env` part (CI has no
  `.env`) but do contain `.git`.
- **Port 8080 was occupied by `TNSLSNR`** — the local Oracle Database listener,
  which serves an HTTP endpoint there by default. Moved to 8088. The first
  `up` silently created the frontend with *no* port mapping rather than
  failing, so an early health check was actually hitting Oracle's 404 page and
  looked like a working stack.
- **Rate-limit settings never reached the container.** `RATE_LIMIT_*` are
  documented as tunable in `.env.example`, but neither compose file passed them
  through — editing `.env` silently did nothing and the defaults always
  applied. Now wired in both.

### <a id="dn-20"></a>DN-20 — Trust `X-Forwarded-For` behind Funnel, on evidence
Shipped with `TRUST_PROXY_HEADERS=false` because the behaviour was unverified.
Tested it rather than leaving it to assumption:

| Condition | `ip=` recorded |
|---|---|
| trust off | `172.19.0.3` — the nginx container; every caller in one bucket |
| trust on | a real public client IPv6 — so Funnel *does* forward the header |
| trust on + forged `X-Forwarded-For: 203.0.113.99` | the **real** address, not the forged one |

The third row is the one that matters. nginx appends with
`$proxy_add_x_forwarded_for`, so a client-supplied header would normally end up
left-most and attacker-controlled — which is exactly why the limiter does not
trust it by default. But Tailscale **overwrites** the header rather than
appending, so the forged value never survives. Combined with the backend
publishing no port — the only path in is Funnel → nginx → backend — trusting
it is safe, and it buys genuine per-client rate limiting instead of one shared
anonymous bucket.

Caveat recorded honestly: the spoofing attempt was made over the tailnet route,
not from a genuinely external host. The same Tailscale proxy terminates both,
so it very likely holds publicly, but that specific case is untested.

### Open at the time
Uptime was laptop-bound; the GCP move (below) fixed it the same evening.

---

## 2026-10-25 (night) — moved to GCP, laptop decommissioned

**Now running always-on at `https://stockradar.tail7679a0.ts.net`** on a GCP
`e2-micro`. The laptop deployment is shut down; one scheduler, one host.

### <a id="dn-21"></a>DN-21 — GCP `e2-micro` + Funnel, not Caddy
Oracle signup stayed blocked, so GCP always-free became the host. Kept
**Tailscale Funnel on the VM** rather than switching to the Caddy/Let's Encrypt
path built earlier: no certificates to issue, no DNS record, no inbound
firewall ports, and `docker-compose.funnel.yml` works unchanged. Only
`APP_DOMAIN` differed, plus one more redirect URI. The Caddy path stays in the
repo as Part 3, now worth using only for a custom domain — the one thing
Funnel cannot do (it serves `*.ts.net` exclusively; the tailnet name can be
renamed but only to Tailscale-generated word pairs, not chosen text).

Free-tier settings that silently bill if wrong, all documented in the runbook:
region (free only in three US regions), `pd-standard` not the default
`pd-balanced`, snapshot backups off. Measured egress too — `/api/latest` is
568 KB raw but **70 KB gzipped**, so a dashboard poll is ~80 KB and the 1 GB
monthly free egress covers ~100 hours of open dashboard.

### <a id="dn-22"></a>DN-22 — Pull images; `TIER2_WORKERS=4` + 2 GB swap
`free -h` on the idle VM showed only **~467 Mi available** — Ubuntu, Docker and
the GCP agents take half the gigabyte before anything of ours runs. `npm ci`
plus a Vite build wants 500 MB–1 GB, and an OOM there surfaces as a bare
`killed` with nothing mentioning memory. So `docker-compose.pull.yml` pulls the
amd64 CI images instead (`e2-micro` is x86_64, so they fit) — published from
`alt-approach` via `workflow_dispatch`, since CI only builds on `master` where
no auth code lives.

Tuning validated against a real run rather than guessed:

| Metric | Laptop (16 workers) | GCP (4 workers) |
|---|---|---|
| Tier 2, 400 symbols | 203 s | **643 s** (1.61 s/symbol) |
| Deadline used | — | ~24% of the 45 min budget |
| Peak swap | — | **361 Mi** of 2 GB |

No OOM, no truncation, email sent. Swap absorbed the overshoot — without it
this would have been an OOM kill. Keeping 4 workers: the run is already fast
enough, so more workers would only add memory pressure.

### Also
- **The sleep-freeze problem is now moot.** `ec4c043` exists because a sleeping
  laptop froze APScheduler's timers. A VM does not sleep, and the container
  runs `TZ=Asia/Kolkata`, so 09:15/15:30 IST mean what they say. Tomorrow's
  09:15 is the first genuinely reliable scheduled run this project has had.
- Verified the handover from outside: the old laptop hostname now 404s and the
  GCP one serves. The laptop's `backend_data` volume was **retained**, not
  deleted.

### <a id="dn-23"></a>DN-23 — Phase 6: merged to `master`
Done the same night, once the always-on host existed. DN-12 had deferred this
for want of a production backend; that reason was gone.

The merge was clean despite the regime fix existing as **two** commits — the
original `f8dd8bc` on `alt-approach` and the cherry-pick `871686b` on `master`
(DN-10). Git's three-way merge resolved the duplicate without conflict, and
the merged tree hashes **identical** to `alt-approach` (`1b54661`), so both
branches now carry the same content via different history. Security suite
40/40 on `master` afterwards.

*(Lint "failed" on `master` only because that worktree has no `node_modules` —
`eslint` was not installed there. Identical tree to the branch where it passes,
so it was not worth installing 300 packages to re-prove.)*

**What this changes:** `master`'s ghcr images now contain the auth code. Until
now `:latest` was the *unauthenticated* build, which made
`docker-compose.pull.yml` depend on a manual `workflow_dispatch` against
`alt-approach`. It can now pull from `master` normally.

**What it does not fix:** the Vercel deployment. It rebuilds and will serve the
login-gated SPA, but its `VITE_API_URL` points at a placeholder backend, so it
shows the login or backend-unreachable screen. It was already non-functional
before — pointing at a backend that did not exist — so this is neither a
regression nor a fix. Making it work would mean pointing it at the GCP host,
which is cross-site and would need `SameSite=none` plus a CORS entry. Not
something to do incidentally; see DN-16 for why a split frontend is the worse
topology anyway.

### Open
- History on the VM starts at run 1 (fresh volume). The laptop's runs are in
  its retained Docker volume and the local `backend/data/stockradar.db`.
- CI still builds amd64-only and only from `master`. Fine for GCP; would need
  `platforms: linux/arm64` if a future host is ARM.
- DuckDNS record (`stockradar-vk.duckdns.org`) is unused on the Funnel path.
  Keep it for the cloud route; it currently points at a home Airtel IP.
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

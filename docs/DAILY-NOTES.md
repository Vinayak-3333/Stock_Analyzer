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
| [DN-24](#dn-24) | 2026-10-26 | Download each symbol's Yahoo history once; quotes come from the 1y frame |
| [DN-25](#dn-25) | 2026-10-26 | Store FII/DII every run, keyed by NSE's trading date, not `date.today()` |
| [DN-26](#dn-26) | 2026-10-27 | Kubernetes on a separate k3s lab VM; the live VM is not touched until cutover |
| [DN-27](#dn-27) | 2026-10-27 | Lab backend strips the scheduler at startup and has no Gmail secret, rather than a code flag or a custom image |
| [DN-28](#dn-28) | 2026-10-28 | Keep the live VM on `e2-micro` — ~27 min per run is acceptable for two runs a day |
| [DN-29](#dn-29) | 2026-10-29 | Date the live bar by NSE's timestamp, never the wall clock; keep it out of `raw_ohlcv` |
| [DN-30](#dn-30) | 2026-10-29 | Intraday refresh re-scores only price-driven factors; the rest stay from the deep run |
| [DN-31](#dn-31) | 2026-10-29 | Refresh writes a one-row `live_snapshot`, never `runs`; route 404s while the token is unset |

---

## 2026-10-29

Made the dashboard live during market hours. Before today, scores and prices
were a snapshot from the 09:15 / 15:30 deep runs, and ~1,240 of the ~2,000
stocks were priced from the last row of a Yahoo daily history, which can be
a session old. Four commits on `alt-approach`.

### Shipped
- **Live NSE prices for the whole universe** (`c76c65f`). NIFTY 500 and
  NIFTY SMALLCAP 500 added to the index list (353 → 763 stocks with full
  index quotes). Every other stock is now priced from NSE's
  `live-analysis-stocksTraded` feed: one call, ~2,900 EQ/BE rows. Its
  volume arrives in **lakhs** and is converted to shares. Yahoo still
  supplies the 1y histories, and its quote is the fallback when NSE has no
  row. Local check with Yahoo stubbed: universe 2,000 = `nse_live` 763 +
  `nse_traded` 1,237, none priced from Yahoo.
- **Today's live bar in every indicator, plus a live regime** (`f696d38`).
  New `core/features/live_bar.py`: `with_live_bar()` replaces or appends the
  session's row in a daily history from NSE's live quote, so indicator
  windows keep their meaning and the last bar is "now". It is applied to
  Tier 1/Tier 2 histories and the NIFTY relative-strength frame. The regime
  gets NIFTY and India VIX live bars from NSE `allIndices` on top of Yahoo
  daily history, and sectors come live from NSE; its cache dropped from 1 h
  to 60 s and it carries NSE's `as_of`. Checked against NSE: NIFTY 1d/5d,
  VIX and stock closes match exactly.
- **5-minute intraday live refresh** (`ec6b9d0`). `core/live_refresh.py`
  re-scores the latest deep run's ~400 stocks on live data: ~30 NSE calls,
  no per-stock Yahoo calls, **48 s for 400/400** on a real run. Served
  through `POST /internal/refresh` (same bearer token as `/internal/run`),
  and `/api/latest` returns it with `live.as_of`. It is skipped while a deep
  run is active, and only one refresh runs at a time. Lab CronJob
  `stockradar-live-refresh` runs `*/5` from 09:00 to 15:55 IST, Mon–Fri.
  `combine_factor_scores()` and `_price_and_technical_fields()` are now
  shared by the deep run and the refresh; both were checked identical to the
  old code on 500 random inputs. Results gain `is_disqualified` and
  `price_as_of`. The dashboard adds a "Live · NSE <time>" badge, the deep
  run's own time, per-stock "Price as of", and labels delivery % as
  previous session. `_verify_security.py` 62/62 (12 new).
- **CLAUDE.md data-freshness re-audit** (`554aa78`): describes the two
  speeds, how the live bar is dated, the three NSE live sources and what
  stays EOD-only. Audit defect #5 (NSE timestamps discarded) marked fixed.

### <a id="dn-29"></a>DN-29 — Date the live bar by NSE's timestamp; keep it out of `raw_ohlcv`
The bar is dated by NSE's own `lastUpdateTime` / response timestamp, not the
server clock. A wall-clock date would append a phantom bar after the close
or on a holiday, adding a session that never traded to every indicator
window. Spliced frames carry `attrs["live_bar"]`, and `_store_ohlcv` drops
that row because it is an unfinished session: storing it would feed a
partial close into the forward-return labels behind `/api/factor-ic`.

### <a id="dn-30"></a>DN-30 — Refresh re-scores only the price-driven factors
The refresh recomputes price, every technical indicator, the technical
factor, the regime multiplier, final score/signal and stop-loss/target. It
keeps the fundamental, institutional, sentiment, sector and risk factors
from the deep run. Those inputs don't change intraday (fundamentals are
quarterly, FII/DII and delivery are EOD), and re-fetching them would mean
per-stock Yahoo / Screener / news calls every 5 minutes — the thing that
makes a deep run take 8–27 minutes. Sharing `combine_factor_scores()`
between both paths keeps them from drifting apart, so a refreshed score is
comparable to a deep-run score.

### <a id="dn-31"></a>DN-31 — Separate `live_snapshot`, never `runs`; 404 while untokened
The refresh overwrites a single-row `live_snapshot` table and never writes
`runs` or `raw_ohlcv`. That keeps run history one row per deep run (84 a
day would swamp `/api/history` and the score-history labels). The route
reuses `INTERNAL_TRIGGER_TOKEN` and 404s while it is unset, so the live VM,
which doesn't set it, is unaffected until the k8s cutover. Only the lab
runs the refresh.

### Verified on the lab (checked 2026-10-30)
Lab runs `ec6b9d0` (pin committed in `499b805`). First full market day of
the refresh CronJob: **82 of 84** slots from 09:00 to 15:55 refreshed, typically
in 31–36 s, with 397–400/400 stocks re-scored (348/400 at 09:15, right after
the open). The two missing slots, 09:20 and 15:35, fell inside deep runs and
were skipped by design. The endpoint returns `skipped` without logging, so
those slots show up only as gaps. `live_snapshot` holds `run_id` 6 (the 15:30
deep run), `as_of` 15:55:02, NEUTRAL / VIX 15.2 / NIFTY +1.05%.

### Found
- **The refresh and the deep run race at 09:15 and 15:30.** Both CronJobs
  fire in the same second. The refresh was accepted ~0.1 s before the run
  started, so the "deep run active" guard passed and both ran together
  against NSE: the 15:30 refresh took **234 s** instead of ~32 s. The result
  is still correct, because the snapshot is tagged with the run it refreshed
  and `/api/latest` ignores a snapshot of an older run. The cost is
  duplicated NSE load at the two busiest moments. **Fixed 2026-10-30:** the
  refresh schedule moved to `2-57/5` (09:02 … 15:57), so it always arrives
  after a deep run has started and gets `skipped`. Chosen over a code change
  ("deep run cancels a running refresh") as the smallest fix. Remaining
  edge: a deep-run Job that starts more than 2 min late
  (`startingDeadlineSeconds: 600`) could still coincide with a refresh.

### Open
- New refresh schedule applied to the lab 2026-10-30 (only
  `stockradar-live-refresh` changed). Confirm on the next market day that
  no `internal_refresh.accepted` appears at 09:17 or 15:32 (a skip is not
  logged, only a gap) and the first refresh after each deep run is of the
  new run.
- Audit defects #2 (`company_name == symbol`, `industry == "Unknown"`) and
  #3 (`raw_ohlcv` 100% Yahoo) are still open.
- Carried over: region move + billing upgrade before 2027-01-23; k8s plan
  next step is a shared run lock; lab VM `stockradar-k8s` still running.

---

## 2026-10-28

Measured the live `e2-micro` with the DN-24 fix across all four scheduled
runs since the 01:58 IST deploy on 27 Oct, closing the decision deferred
yesterday. Read-only: `docker logs` on the VM, nothing changed there.

### Measured
Stage boundaries from the backend log markers (`Fetching Yahoo quotes for N
supplementary symbols` → `Yahoo bulk history` → `Tier-1 histories` → `Tier 1:
screened` → `Tier 2 finished` → `Analysis job done`).

| Stage | 27 Oct 09:15 | 27 Oct 15:30 | 28 Oct 09:15 | 28 Oct 15:30 | Lab 27 Oct |
|---|---|---|---|---|---|
| Yahoo 1y histories, supplement | 7m56s | 7m18s | 10m12s | 9m11s | 3m25s |
| Yahoo 1y histories, NSE-live symbols | 1m45s | 4m18s | 2m36s | 2m25s | 0m47s |
| Tier 1 scoring (CPU-bound) | 5m13s | 4m58s | 5m27s | 4m44s | 0m33s |
| Tier 2, top 400 | 5m55s | 7m42s | 7m22s | 7m09s | 2m16s |
| **Total** | **22m47s** | **26m42s** | **28m30s** | **26m43s** | **7m40s** |
| Universe | 1,807 | 1,809 | 1,803 | 1,793 | 1,806 |

All four fired on time, Tier 2 got 400/400 every time, and the universe is
back at ~1,800 (vs 357 with the NaN-Close bug). Down from 38m55s on the old
code. Tier 1 is ~9× slower than the lab, the network-bound stages 2–3×,
matching the burst-credit explanation from yesterday.

### Found
- **FII/DII accumulating as designed (DN-25):** the window went from
  `1 of 3` on 27 Oct to `2 of 3` on 28 Oct.
- VM at rest: 451 Mi RAM used of 953, **421 Mi in swap**. Tight but stable.
- Log noise: a pandas warning at `quotes.sort_index(inplace=True)` repeats
  many times per run, and yfinance logs a `TzCache` folder warning
  (`File exists`). Both harmless; they bury the stage markers. Not fixed.

### <a id="dn-28"></a>DN-28 — Keep the live VM on `e2-micro`
~27 min (22–29 min range) is well inside the gap between two runs a day, and
nothing reads results mid-run. `e2-small` would roughly halve Tier 1 for
~₹1,250/mo; `e2-medium` gets 7m40s for ~₹2,400/mo. Neither buys anything the
user needs, while the micro can be free after the planned move to a US region
before the trial ends (~2027-01-23). Revisit if `DEEP_ANALYSIS_COUNT` or the
universe grows, or if the analysis moves to the k8s cluster.

### Shipped
- **"Email sent" now reflects the real outcome** (the 2026-10-27 finding).
  `send_gmail` returns `True` only when Gmail accepts the message, logs
  failures through `logging` instead of `print` (which the container
  buffered), and skips the attempt with a warning when the credentials are
  missing — the lab pod's case. `analysis_job` stores that result, so the
  `runs.email_sent` column was also wrong before: it was `True` for every run
  that tried to email. Tested with mocked SMTP: no credentials, auth failure
  and network error give `False`; success gives `True`.
- **yfinance log noise silenced** (`core/pipeline.py`), both from yfinance
  1.7.0, not our code:
  - `sort_index(inplace=True)` deprecation (pandas 3.1, `yfinance/utils.py`):
    **8,086** copies in the live container's log since 27 Oct. Ignored by a
    filter scoped to `module="yfinance"` on that message only — the same
    deprecation raised from our code still shows (tested both ways).
  - `TzCache ... File exists` was **not just noise**: yfinance creates its
    cache folder with a non-idempotent `makedirs`, parallel download threads
    race on it on the first run after a container start, and the loser
    **disables the tz cache for the rest of the process** — extra Yahoo
    lookups every run until restart. Fixed by creating the folder and calling
    `yf.set_tz_cache_location` at import, before any thread. Tested with a
    fresh folder and 16 concurrent threads: all got the real cache.
- **`SCHEDULER_ENABLED`** (`backend/api.py`, default `true`, so the live VM
  is unchanged). `false` skips the 09:15/15:30 jobs; the scheduler still
  starts so `/api/status` keeps its shape and manual runs still work. The lab
  now sets it instead of DN-27's `add_job` monkeypatch, and runs the image's
  own CMD. Startup log: `Scheduler started with no jobs — SCHEDULER_ENABLED=false`.
- **CI builds `alt-approach` too, SHA tag only.** Correction to DN-27: CI
  already tagged `:<sha>` on every `master` build (24 tags in ghcr); the real
  gap was that branch commits never got an image. Now `master` → `:latest` +
  `:<sha>`, `alt-approach` → `:<sha>` only. Docs, `*.md` and `deploy/k8s/`
  changes no longer trigger a build. First branch build `672b285`, run
  `40725373944`; `:latest` verified unmoved (backend still `26785ec5`, what
  the live VM runs).
- **Lab pinned to `672b285`** via kustomize `images:`, `IfNotPresent`
  instead of `Always`. So the lab now has today's email and yfinance fixes.
  The 894 MB backend image took **5 min** to pull on the lab VM — the
  rollout's default timeouts are too short for a fresh SHA; allow ~6 min.

- **Kubernetes plan written down** — [`docs/k8s/PLAN.md`](k8s/PLAN.md)
  (`ee7e00e`). The cluster is a **learning lab** for now; production is
  revisited before 2027-01-23.
- **Plan step 1 done: CronJobs start the lab's scheduled runs** (`7f424e6`,
  lab pinned in `a8d44ce`). New `POST /internal/run`, bearer token
  `INTERNAL_TRIGGER_TOKEN`, outside `/api/` so nginx never forwards it, and
  **404 while the token is unset — the live VM's case**. Two CronJobs
  (09:15, 15:30 IST) POST to it with `email=false`; the token is in its own
  Secret `stockradar-internal`. `_verify_security.py` 50/50 (10 new).
  On the lab: public URL → nginx 405, in-cluster without token → 401, a Job
  from the 09:15 CronJob → accepted, run 3 saved with `email_sent=0` in 8m15s (universe 1,806, Tier 2 400/400). The lab's run 2 from 27 Oct reads `email_sent=1` with no Gmail credentials — the bug fixed in `da1f469`, caught in the data. Design and the reasons
  behind each choice are in the plan.

### Found
- Git Bash `curl` on this laptop returns `000` (exit 23/43) whenever `-o` or
  `-w` is used, although the request succeeds. Probe with `curl -sI` instead.

### Open
- Region move + billing upgrade before 2027-01-23.
- Kubernetes plan now written down: [`docs/k8s/PLAN.md`](k8s/PLAN.md). The
  cluster is a **learning lab** for now (production revisited before
  2027-01-23). Step 1 (CronJob) done; next: a shared run lock, then lab
  hardening.
- Lab VM `stockradar-k8s` left **running** (~₹80/day).

---

## 2026-10-27

Started the Kubernetes track on a new VM, ran the app there, and the first lab
run exposed a bug in yesterday's DN-24 fix that would have shrunk the live
09:15 run to ~350 stocks. Fixed and deployed before the run. Work was done
between ~00:30 and ~02:05 IST.

### Shipped
| Commit | What |
|---|---|
| — | **New VM `stockradar-k8s`** (`e2-medium`, 2 vCPU / 4 GB, 40 GB `pd-balanced`, Ubuntu 24.04, `asia-south1-c`), on trial credit. Tailscale `100.78.101.61` / `stockradar-k8s.tail7679a0.ts.net`; k3s `v1.36.5+k3s1` (single node, Traefik + local-path) |
| `7731acd` | `deploy/k8s/lab/` — Kustomize manifests: backend (1 replica, `Recreate`, 5 Gi local-path PVC), frontend (2 replicas), Traefik ingress, namespace `stockradar` |
| `cff2b5f` | Lenient backend probes (liveness 10 × 30 s) so a CPU-bound run isn't restarted mid-way |
| `8d5ede0` | **Fix:** quote supplementary symbols from the last row that has a Close (see Found) |
| `8916899` | Merged to `master`; CI run `40394708363` green; **live VM backend updated ~01:58 IST** (image `26785ec5`) — fix confirmed in the container, 09:15/15:30 jobs armed, public health OK, `/auth/me` 401. Frontend not restarted |

Lab access: `https://stockradar-k8s.tail7679a0.ts.net` (tailnet only, via
`tailscale serve` → Traefik :80). Laptop `kubectl` v1.37.1 (winget) with
`~/.kube/config` context `stockradar-k8s`; Docker Desktop's v1.34 is shadowed
by `~/bin/kubectl` (Git Bash) and an alias in the PowerShell profile. Google
login: added the lab redirect URI to the OAuth client, which lives in GCP
project **`sigma-sector-481906-p8`**, not `stock-radar-512711` (a client ID
starts with its owning project's number).

### Found
- **DN-24 bug: the Yahoo supplement collapsed to 4 symbols.** Multi-ticker
  `yf.download` now ends each frame with a row that has Volume but a **NaN
  Close** (here: 26 Oct, fetched at 01:30 IST). `_quote_from_history` read that
  row and returned `None`, so 1,450 downloaded histories gave 4 quotes and the
  universe was 357 instead of 2,000. DN-24's 100-symbol check passed because
  it ran in daytime, when the last row was complete. Fix drops NaN-Close rows
  first: 10-symbol live check 0/10 → 10/10. Side effect: when the newest row
  is blank, the quote is one session old for supplement symbols.
- **The live VM never ran with the bug.** It was deployed 22:03 on 26 Oct and
  fixed ~01:58 on 27 Oct, with no scheduled run in between.
- **"Email sent" is logged unconditionally.** `send_gmail` swallows SMTP
  errors and reports via `print` (buffered in the container), so the log
  claimed an email for the lab run although the pod has no Gmail credentials.
  Checked inside the pod: no `GMAIL_*` vars, no `/app/.env`. Not fixed.
- Lab run (01:30–01:37 IST, 357-symbol universe): 7m17s, peak 345 Mi, no
  restarts. **Not comparable** with the VM's 39 min because of the bug above.
- **Full lab run with the fix (02:07–02:15 IST) confirms it end to end** on
  the image the live VM now runs: Yahoo quotes 1,453/1,647, universe 1,806,
  Tier 1 1,797, Tier 2 400/400, `FII/DII window has 1 of 3`. **7m40s**, peak
  325 Mi, no restarts. 1,806 rather than 2,000 is DN-24's < 60-row skip, not a
  loss: Tier 2 still gets its full 400. One `database is locked` for
  `SHRADDHA.NS` came from yfinance's own sqlite cache; the run carried on.
- NSE (live quotes, bhavcopy, FII/DII) answers from the GCP VM's address —
  it wasn't certain a cloud IP would be allowed.
- k3s's API (6443) listens on all interfaces. The GCP default firewall blocks
  it publicly (checked: timeout on the external IP; `401` over Tailscale), but
  `default-allow-internal` lets other VMs in the VPC reach it. Accepted for a lab.
- Traefik's Helm install fails twice on first boot, then succeeds — normal.

### `e2-micro` (live) vs `e2-medium` (lab)
Not like-for-like yet: the live number is the old code (duplicate download),
at market open, from an IP Yahoo already sees twice a day; the lab ran at
2 AM from a fresh IP. Today's 09:15 run gives the fair micro number.

| | `e2-micro` | `e2-medium` |
|---|---|---|
| Sustained CPU | 0.25 vCPU (bursts to 2, briefly) | 1 vCPU (bursts to 2) |
| Memory | 1 GB + 2 GB swap | 4 GB |
| ~Cost, Mumbai | ~$7–8/mo (~₹650) | ~$27–30/mo (~₹2,400) |
| Free after the trial | yes, in a US region | no |

| Stage | Live 26 Oct (old code) | Lab 27 Oct (fixed) | Speed-up |
|---|---|---|---|
| Yahoo 5d quotes (duplicate, now removed) | 10m08s | — | removed by DN-24 |
| Yahoo 1y histories | 13m48s | 3m25s + 0m47s | ~3× |
| Tier 1 scoring (CPU-bound) | 4m34s | 0m33s | **~8×** |
| Tier 2, top 400 | 7m58s | 2m16s | ~3.5× |
| **Total** | **38m55s** | **7m40s** | ~5× |

The 40-minute run outlasts the micro's burst credits, after which it is held to
0.25 vCPU. That's why CPU-bound Tier 1 shows the largest gap, while
the network-bound stages gain less. Expected micro time with DN-24: **~25–30
min** (estimate). Peak memory 325 Mi, so 4 GB is far more than needed.
Options if speed matters: `e2-small` (0.5 vCPU, 2 GB, ~₹1,250/mo) as a middle
step, or run the analysis on the k8s cluster. Decision deferred until the
09:15 number is in. Keep the micro if 25–30 min is acceptable, since it can be
free after the region move.

### <a id="dn-26"></a>DN-26 — Kubernetes on a separate k3s lab VM
The user's rule: **don't disturb the live deployment, its scheduler or its
analysis** until the Kubernetes setup is complete. So the cluster gets its own
VM, nothing project-wide changes (no firewall, network or IAM edits), and it is
reachable only over the tailnet (`tailscale serve`, never Funnel).

k3s on one `e2-medium` rather than GKE Autopilot: the same manifests, no
control-plane or load-balancer charge, and 4 GB is enough room for a run
without swap. About ₹2,300/month of trial credit; stopping the VM leaves only
the disk charge. Scaling the backend still waits on Phase 0 of the plan: move
the scheduler out to a CronJob and the shared tables to Postgres. DuckDB's
single writer and the in-process APScheduler both forbid a second replica.

### <a id="dn-27"></a>DN-27 — Lab backend: scheduler stripped, no Gmail secret
`api.py` has no switch for the scheduler. The lab overrides the container
command to replace `BaseScheduler.add_job` with a no-op before importing the
app, so it starts with `jobs: []` (checked in the log after every restart).
This avoids a code change and, more importantly, a custom image: CI tags only
`:latest`, the tag the live VM pulls. The secret has OIDC + session + the
allow-list only, so even a manual lab run can't email. Lab-only; the real fix
is a `SCHEDULER_ENABLED` flag or the CronJob split.

### Open
- **Check 09:15 on the live VM** — the first run with all three fixes:
  `Yahoo quotes from history` should be ~1,400+/1,647, universe ~2,000; run
  time vs the ~29 min estimate; `FII/DII window has N of 3`.
- Compare the 09:15 micro time with the lab's 7m40s, then decide on the
  machine size (keep micro / `e2-small` / analysis on k8s).
- **Tailscale key expiry** for `stockradar-k8s` (expires 2027-04-24) — disable
  in the admin console. The user's action.
- `alt-approach` notes commit for this entry needs pushing; `master` lags by it.
- Phase 0 of the Kubernetes plan (scheduler → CronJob, shared tables →
  Postgres, run lock) when the user wants to continue.
- Make "Email sent" reflect the real outcome (have `send_gmail` return a bool).

---

## 2026-10-26

First weekday on the GCP VM. Verified the scheduler, found the VM is billed
(but covered by trial credit), sped up the run, and fixed a silent
institutional-score bug that turned up while mapping the pipeline.

### Shipped
| Commit | What |
|---|---|
| `0634c41` | Single Yahoo history download per run (DN-24) |
| `2edeac6` | FII/DII stored each run so the 3-day window is real (DN-25) |
| `726bb3d` | Merged to `master`; CI build green; **deployed to the VM 22:03 IST** (image `589df4c4`) — both fixes confirmed in the container, 09:15/15:30 jobs re-armed, public health OK, `/auth/me` 401 |
| `9d4317d` | Architecture diagram added as [`docs/ARCHITECTURE.html`](ARCHITECTURE.html) |
| `48c6fb2` | Merged `alt-approach` → `master` (docs only: today's notes + the diagram) and pushed. CI rebuilt the images (run `40378066400`) with no code change, so the VM was left as is |

Also: an architecture map of the pipeline (sources → stages → stores →
feedback loop, with measured timings). The repo copy is
[`docs/ARCHITECTURE.html`](ARCHITECTURE.html): one self-contained file, so open
it in a browser (GitHub shows the source). Both fixes are marked deployed. The
same page is also published as a private claude.ai page:
https://claude.ai/artifact/1ruFdmZBtBH8hDbp6m5dmi. Keep the two in step when
the pipeline changes.

### Found
- **Both scheduled runs fired on time on the VM** — 09:15:02 (finished
  09:53:57) and 15:30:02 (finished 16:05:37), 400/400 Tier-2, email sent.
  **The July sleep-freeze thread is closed**: a VM doesn't sleep, so
  `ec4c043`'s nudge is no longer load-bearing.
- **The VM is in `asia-south1-c` (Mumbai), outside the always-free regions.**
  `e2-micro` and `pd-standard` are correct, but free only in three US regions.
  Billing console: **free-trial account**, ₹34.11 spent in ~1.5 days, all
  covered by credit (₹28,768 left, 89 days). When the trial ends (~**23 Jan
  2027**) Google *stops every trial resource* unless the account is upgraded —
  so the site goes down that day regardless of region. Also: Google requires
  account verification by **22 Feb 2027**.
- **Where the 39 minutes went** (09:15 run): Yahoo 5d quotes 10m08s, Tier-1
  1y download 13m48s, Tier-1 scoring 4m34s, Tier 2 7m58s, rest ~2 min. The 5d
  pass fetched the same 1,698 symbols Tier 1 then fetched again for a year.
- **On the VM each 100-symbol Yahoo batch takes 70–80 s; on the laptop ~5 s.**
  The `e2-micro` (shared 0.25 vCPU sustained) is now the dominant cost, not
  the code. Memory is fine: 426 Mi of 2 GB swap in use.
- **The "3-day" FII/DII flow was one day.** `raw_fii_dii` is written only by
  `run_full_collection()`, which the modular pipeline never calls. On the VM
  the table was empty, so the sum fell back to today alone — the 09:15 log
  shows FII net 888 and FII_3d 888. Same class of bug as DN-01: a producer
  and consumer that nothing cross-checks.
- **NSE's FII/DII endpoint serves the previous session in the morning**; each
  record carries its own `date`. Labelling with `date.today()` would have
  shifted the window by a day.
- **Eight lake tables are created but never written**: `raw_options`,
  `raw_options_summary`, `raw_news`, `raw_macro`, `raw_fundamentals`,
  `feature_snapshots`, `backtest_runs`, `alert_history`. Harmless at runtime,
  misleading to read. Not acted on.
- Access notes: `gcloud` auth had expired (re-login needed); the project is
  `stock-radar-512711` (default config points at `mern-378315`); the SSH user
  isn't in the `docker` group, so VM docker commands need `sudo`.

### <a id="dn-24"></a>DN-24 — One Yahoo history download per run
Symbol resolution now fetches the **1y** history for the supplementary symbols
and derives each quote from the last two rows; Tier 1 reuses those frames and
only downloads the ~300 NSE-live symbols. ~37 bulk batches → ~21, ~45% less
Yahoo volume — which also matters because Yahoo throttling is cumulative.
Estimated run time ~39 → ~29 min, **unverified until a real VM run**.

Dropped with it: the `fast_info` enrichment (it only ran for symbols missing
from the bulk download, which Tier 1 can't screen anyway). Symbols with < 60
rows of history now get no quote instead of being dropped at Tier 1 — same
analysed set, smaller `known_symbols`. The 30 s-cooldown throttling retry moved
into `_bulk_download_histories` so it isn't lost.

Verified: 100 random symbols, old 5d path vs new — 0 mismatches across 7 quote
fields; the 13 the new path skips all have < 60 rows. Reduced end-to-end run
(500-symbol universe, 20 deep) on a scratch lake.

### <a id="dn-25"></a>DN-25 — Store FII/DII every run, keyed by trading date
`_resolve_fii_dii_3d` now fetches the latest flows once per run, upserts them,
then sums the last 3 stored days. Rows are keyed by the date NSE reports. The
`fii_net` comparison heuristic is gone; the date primary key makes reruns
idempotent. Tested on a scratch lake: rolling sums over 4 simulated days,
same-day rerun not double-counted, NSE outage falls back to stored days, live
fetch stored under 26 Oct.

**Scores will shift.** The institutional factor now sees three days, and the
FII-selling/DII-buying divergence flag becomes meaningful (true on today's
live data). The window fills over the first 3 trading days after deploy.

### Open
- **Check Tuesday 09:15**, the first run with both fixes: run time vs the
  ~29 min estimate, and the "FII/DII window has N of 3" log line.
- VM deploy gotcha: the compose dir is under `/home/vinnu_smath333`, which
  the gcloud SSH user can't enter — run compose via `sudo bash -c "cd … && …"`.
- **Before ~23 Jan 2027**: upgrade the billing account *and* move the VM to a
  US free region in the same step (keep the `stockradar` tailnet name so the
  URL survives). Doing it earlier gains nothing while credit pays.
- **Account verification by 22 Feb 2027** — the user's action, in the console.
- The 8 never-written lake tables: delete from the schema or wire them up.

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
- **DN-20 confirmed on GCP too** (not just assumed): `audit.log` records real
  client addresses — the laptop's tailnet IP for tailnet-routed logins and the
  home public IP for one that came through Funnel's public path. No
  Docker-internal `172.x.x.x`, so `TRUST_PROXY_HEADERS=true` is right here.
- **The audit trail paid for itself on day one.** Four
  `login.rejected reason=not_allowed` entries preceded the first successful
  login — Google authenticated fine but the email was not on the allow-list,
  because the first `.env` written on the VM had `ALLOWED_EMAILS` /
  `RECIPIENT_EMAIL` wrong. Diagnosed retroactively from a log written before
  anyone knew to look. Worth remembering that this same line, with an email
  that is *not* ours, is the signal that someone is probing.

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

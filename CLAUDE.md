# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

StockRadar IN — an Indian stock market analyzer. A Python backend scans up to ~2,000 NSE stocks in two tiers — Tier 1 screens the full universe (live quotes from 26 NSE indices, supplemented by bhavcopy / NSE equity-listing symbols via Yahoo, DuckDB-cached symbols as last-resort fallback) with technical scoring only, then Tier 2 runs deep analysis (fundamentals/news/delivery) on the top `DEEP_ANALYSIS_COUNT` (default 400), scores them with a multi-factor engine, persists results, and emails alerts on a schedule; a React dashboard displays the results.

## Daily notes — keep these current

`docs/DAILY-NOTES.md` is the running day-by-day record of what changed and
**why**, with a decision index (`DN-nn`) at the top.

- **Read it at the start of a session** to pick up context without re-deriving it.
- **Append to it at the end of any session that changed something**: a dated
  entry with Shipped / Decisions / Found / Open. Record the reasoning, not just
  the outcome — a decision without its "why" gets silently reversed later.
- Non-obvious trade-offs, rejected alternatives and "this looks wrong but is
  deliberate" notes belong there. Durable architectural facts belong in this
  file; the security plan lives in `docs/security/`.

## Commands

```bash
# Install Python deps (project root)
pip install -r requirements.txt

# Run backend (must be launched from backend/ — it adds the project root to sys.path itself)
cd backend
python -m uvicorn api:app --host 0.0.0.0 --port 8000

# Frontend (frontend/)
npm install
npm run dev        # Vite dev server on http://localhost:5173
npm run build
npm run lint       # ESLint (frontend only; no Python linter is configured)

# Start both at once (Windows)
start.bat

# Full self-hosted deployment (NAS/Docker)
docker compose -f docker-compose.nas.yml up -d

# Optional Kafka + Zookeeper + Redis streaming stack
docker compose up -d
```

There is no test framework configured. `test_new_apis.py` and `check_status.py` are ad-hoc scripts run directly with `python`, not pytest suites. To exercise the pipeline end-to-end, start the backend and `POST /api/trigger`, or hit `/docs` for Swagger.

## Architecture

### One pipeline, plus an email layer

- **`core/`** — the modular pipeline, orchestrated by **`core/pipeline.py`** (`run_modular_analysis`, `get_modular_market_conditions`). Flow: **collectors → features → scoring → risk → lake/API**. All analysis work belongs here.
- **`Analyzer.py`** (root) — *only* the email layer now: `build_email_body`, `build_subject`, `send_gmail`, which is exactly what `backend/api.py` imports. The original monolithic analysis engine that used to live here (16 functions, ~1,150 lines) was superseded by `core/` and deleted in the 2026-10-24 dead-code sweep; it's in git history if ever needed. The filename is a historical artifact — treat it as `email.py`.

### Backend (`backend/api.py`)

FastAPI + APScheduler. Cron-fires the modular pipeline at 09:15 and 15:30 IST on weekdays, persists each run to SQLite (`backend/data/stockradar.db`, `runs` table with results as JSON), and serves the REST API the React dashboard consumes (`/api/latest`, `/api/history`, `/api/runs/{id}`, `/api/trigger`, `/api/market`, `/api/status`). All API responses pass through `sanitize_floats` — NaN/Inf must never reach JSON.

**Auth & hardening (`alt-approach` branch only — `master` is still unauthenticated).** `backend/auth.py` adds Google OIDC behind a BFF session cookie; every `/api/*` route except `/api/health` requires `require_user`, and state-changing routes also require `require_csrf`. `backend/security.py` adds security headers, a sliding-window rate limiter and the authenticated `/docs`; `backend/audit.py` writes the audit trail to `backend/data/audit.log`. **The middleware order in `api.py` is load-bearing** — `CORS → SecurityHeaders → NoCache → Session → RateLimit → routes`. The rate limiter must stay inside `SessionMiddleware` (it reads `request.session` for the identity) and CORS must stay outermost (otherwise 401/403/429 responses reach the browser without CORS headers, as an opaque "Network Error"). Likewise `require_user` must precede `require_csrf` in a route's `dependencies=[]`, or anonymous callers get 403 instead of 401. Run `python backend/_verify_security.py` after touching any of it.

### core/ module layout

- `collectors/` — external data: `nse.py` (NSE live quotes from 26 indices, delivery %, FII/DII, options, bhavcopy — NSE's Akamai gate TLS-fingerprints clients, so `_make_session()` needs curl_cffi Chrome impersonation with a two-step warm-up, plain requests gets a cookieless 403; quotes use the `equity-stock-indices` endpoint and option chains use `option-chain-v3` + `option-chain-contract-info`, the pre-2026 endpoint names are dead; also provides `fetch_equity_symbols_from_bhavcopy()`, `fetch_cached_symbols_from_lake()`, and `save_known_symbols_to_lake()` for dynamic symbol resolution). **`nse.py` is the only collector** — `market_data.py`, `global_data.py`, `fundamentals.py` and `news.py` were deleted in the 2026-10-24 sweep after the import graph showed nothing referenced them; fundamentals and news are fetched by `features/fundamental.py` and `features/sentiment.py` directly through `core/fetch`
- `fetch/` — central outbound-HTTP engine (`engine.py`): every collector call goes through a per-host policy (concurrency semaphore, request pacing, timeouts, retries with jittered backoff, circuit breaker, metrics). Hosts: `yahoo`, `yahoo_bulk`, `nse`, `nse_archives`, `google_news`, `et_rss`, `screener`. Tunable via `FETCH_<HOST>_CONCURRENCY` / `_MIN_INTERVAL_MS` / `_TIMEOUT` / `_RETRIES` env vars; stats at `GET /api/fetch-stats`. Route any new outbound HTTP through `get_engine()`, never raw `requests`.
- `features/` — per-dimension feature computation (`technical`, `fundamental`, `institutional`, `sentiment`, `regime`), aggregated by `features/store.py`. Institutional features fetch option chains only for F&O symbols (`fetch_fno_symbols`) and cache market-wide FII/DII for 30 min.
- `scoring/hybrid.py` — weighted 6-dimension score (fundamental 30%, technical 25%, institutional 15%, sentiment 10%, sector 10%, risk 10%) with a bull/bear regime multiplier; score maps to BUY (≥75) / WATCH (60–74) / HOLD (40–59) / SELL (<40)
- `risk/engine.py` — ATR stops, Kelly sizing, applied to scored results
- `lake/` — DuckDB data lake; `manager.py` is a thread-safe connection manager (use `get_lake()` / `close_lake()`, never open connections directly), `schema.py` holds the DDL
- `events/` — pipeline observability: `LocalEventBus` (in-process, DuckDB-persisted) by default, `KafkaEventBus` optional; stages publish `StockEvent`s consumed by `/api` event endpoints
- `backtest/` — **`labels.py` only**: `backfill_labels()` fills forward-return labels on `score_history` after each run, feeding `GET /api/factor-ic`. The VectorBT backtesting engine and the whole `alerts/` package (Telegram/email) were deleted in the 2026-10-24 sweep — both were fully built but never wired to anything

### Data Sources & Freshness

*Re-audited 2026-10-29 (first audit 2026-07-23) against the live NSE API and lab runs. Re-verify before trusting — upstream endpoints change.*

**Two speeds.** A **deep run** (Tier 1 + Tier 2, `pipeline.run_modular_analysis`) is a point-in-time snapshot at 09:15 / 15:30 IST. In between, the **intraday live refresh** (`core/live_refresh.py`, `POST /internal/refresh`, lab CronJob every 5 min 09:00–15:55) re-scores the latest run's ~400 stocks on live data into the one-row `live_snapshot` table, which `/api/latest` serves with `live.as_of`. The refresh recomputes price, every technical indicator and the technical factor (today's live bar), the regime multiplier, final score/signal and stop-loss/target; fundamental, institutional, sentiment, sector and risk factors stay from the deep run. It never writes `runs` or `raw_ohlcv`. On the live VM `INTERNAL_TRIGGER_TOKEN` is unset, so the route is 404 and only the twice-daily deep runs exist.

**Today's live bar** (`core/features/live_bar.py`, `with_live_bar`): every daily history — each stock's, NIFTY's for relative strength, and the regime's NIFTY/VIX — gets the current session's row replaced or appended from NSE's live quote, dated by **NSE's own timestamp** (never the wall clock, so no phantom bar after the close or on a holiday). Indicator windows keep their meaning; the last bar is "now". Spliced frames carry `attrs["live_bar"]` and `_store_ohlcv` drops that unfinished row so the forward-return labels only see completed sessions.

| Source | What it supplies | Real-time? |
|---|---|---|
| **NSE `equity-stock-indices`** (`nse.py` `collect_all_quotes`) | full quotes (LTP/OHLC/volume/52w) for ~763 stocks across 29 indices incl. NIFTY 500 + SMALLCAP 500 | **Yes** — `as_of` = row `lastUpdateTime`. TOTAL MARKET, MICROCAP 250 and most strategy indices return no rows on this endpoint |
| **NSE `live-analysis-stocksTraded`** (`fetch_traded_quotes`) | live price / change / volume for every traded EQ+BE stock (~2,900) in one call; volume arrives in **lakhs** (×100,000) | **Yes** — prices the ~1,050–1,240 non-index stocks. No OHLC / 52w / name / industry |
| **NSE `allIndices`** (`fetch_live_indices`) | every index live: NIFTY, India VIX, sectors, 1w/1m/1y-ago values, advances/declines | **Yes** — drives the regime's live layer (cached 60 s) |
| Yahoo bulk 1y daily history (`_bulk_download_histories`) | Tier-1/Tier-2 technical history | Daily bars + the NSE live bar on top. Price only as a fallback when NSE has no row (`index: "YFINANCE"`, `price_as_of: null`) |
| Yahoo regime tickers (`features/regime.py`) | `^NSEI` (200-SMA), `^INDIAVIX`, `CL=F`, `USDINR=X`, `^GSPC`, `^TNX` | Daily, cached 1 h; NIFTY / VIX get the NSE live bar |
| NSE `fiidiiTradeReact` | FII/DII net flow | **EOD only** — NSE publishes after the close; keyed by NSE's trading date (DN-25) |
| NSE `quote-equity` + bhavcopy | delivery % | **Previous session** — labelled so in the dashboard |
| NSE bhavcopy CSV (`nsearchives`) | full-market EOD dump, symbol universe | Previous trading day |
| NSE `option-chain-v3` | PCR / max pain per F&O stock (Tier 2) | Live at deep-run time |
| Screener.in + Yahoo | fundamentals, analyst ratings | Up to 7 days (`FUNDAMENTALS_CACHE_TTL_DAYS`); quarterly by nature |
| Yahoo / Google News / ET RSS | headlines, sentiment | At deep-run time |

Every result row carries `price_as_of` (ISO, NSE's clock; `null` = Yahoo fallback); the regime carries `as_of`. The dashboard shows "Live · NSE <time>" for the refresh and the deep run's time separately.

**Known defects found in this audit:**

1. ~~**Keyed providers are dead code.**~~ **Resolved 2026-10-24 by deletion.** `collect_keyed_quotes` was imported at `pipeline.py:41` and never called, so `core/collectors/market_data.py` and the whole Alpha Vantage / Finnhub / Twelve Data / FMP / Groww chain went. `MARKET_DATA_PROVIDER_ORDER` and those four API keys are gone from `docker-compose.nas.yml` and the README. **Useful side effect:** those keys are now provably unused, so the pending rotation for them is a revoke, not a rotate — only the Gmail app password is still live.
2. **NSE dropped the `meta` object** from `equity-stock-indices` (verified live — row keys contain no `meta`). `nse.py:262-263` therefore always falls back to `companyName = symbol` and `industry = ""`. Confirmed: 400/400 results in each of the last 6 runs have `company_name == symbol` and `industry == "Unknown"`, *including* the NSE-sourced large caps. Prices are unaffected.
3. **`raw_ohlcv` is 100% Yahoo.** All 362,127 rows carry `source = 'yfinance'`; there is not a single `nse_live` row. The `nse_live` write only happens in `run_full_collection()` (`nse.py:660`), which the modular pipeline never calls — `_resolve_symbols_and_quotes` calls `collect_all_quotes` directly.
4. ~~**Market-conditions collector still blind**~~ — **fixed 2026-10-24.** Root cause was a key mismatch, not a fetch failure: `_market_payload` (`pipeline.py`) read `india_vix` / `nifty_change_1d` / `regime_label`, none of which `compute_regime_features()` emits — it emits `vix_value` / `market_regime` and had no 1-day NIFTY change at all. The payload therefore hard-coded itself to `None` / `0.0` / `NEUTRAL` while the upstream Yahoo fetches were healthy the whole time. Fix: added `nifty_change_1d` to `regime.py`, pointed `_market_payload` at the real keys, mapped `BULL`/`BEAR` → `BULLISH`/`BEARISH` (what the `runs` table, email template and dashboard badges expect), and added warnings so a genuinely empty regime is no longer silent. **If you add a key to the regime payload, add it on both sides** — this failed undetected for months because nothing cross-checks them.
5. ~~NSE's own `timestamp` / `lastUpdateTime` are discarded~~ — **fixed 2026-10-29.** Quotes keep NSE's timestamp as `as_of`; results expose it as `price_as_of`.

### Optional heavy dependencies degrade gracefully

FinBERT (`transformers`/`torch`) is commented out in `requirements.txt`, and `kafka-python` is optional. The code must keep working without them — sentiment falls back to VADER, and `core/events/bus.py` falls back to the DuckDB-persisted `LocalEventBus`. Preserve these fallback paths when editing. (The VectorBT fallback that used to be documented here went with `backtest/engine.py`.)

### Configuration

`.env` at the project root is loaded by both `backend/api.py` and `core/pipeline.py` (works regardless of launch CWD); missing keys are backfilled from `.env.nas.example`. **No market-data API keys are required** — NSE and Yahoo need no credentials, and the keyed-provider vars were removed with the provider chain. Key vars: `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` / `RECIPIENT_EMAIL`, `DEEP_ANALYSIS_COUNT` (Tier-2 shortlist size, default 400), `FUNDAMENTALS_CACHE_TTL_DAYS` (DuckDB fundamentals cache TTL, default 7), `TIER2_WORKERS` (Tier-2 thread pool, default 32), `TIER2_DEADLINE_MINUTES` (hard Tier-2 wall-clock budget, default 20 — run continues with partial results), `BULK_PARALLEL_CHUNKS` (parallel bulk yf.download chunks, default 2), `ANALYSIS_STALE_MINUTES` (wedged-run guard in the backend, default 90).

### Frontend (`frontend/`)

React 19 + Vite, essentially a single-component app in `src/App.jsx` with the dark theme in `src/index.css`. Talks to the backend via `VITE_API_URL` (Vercel: full backend URL; Docker/NAS: `/api`, proxied by nginx).

### Deployment

Pushing to `master` triggers both a Vercel frontend rebuild and a GitHub Actions workflow (`.github/workflows/docker-images.yml`) that publishes backend + frontend images to ghcr.io. The Vercel deployment and the NAS Docker deployment (`docker-compose.nas.yml`) are independent — don't mix their configs.

## Platform Notes

Development happens on Windows — timezone-sensitive code uses `pytz` with `Asia/Kolkata`, and the backend sets `PYTHONUTF8=1` and filters harmless `WinError 10054` asyncio noise.

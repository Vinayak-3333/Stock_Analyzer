# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

StockRadar IN — an Indian stock market analyzer. A Python backend scans up to ~2,000 NSE stocks in two tiers — Tier 1 screens the full universe (live quotes from 26 NSE indices, supplemented by bhavcopy / NSE equity-listing symbols via Yahoo, DuckDB-cached symbols as last-resort fallback) with technical scoring only, then Tier 2 runs deep analysis (fundamentals/news/delivery) on the top `DEEP_ANALYSIS_COUNT` (default 400), scores them with a multi-factor engine, persists results, and emails alerts on a schedule; a React dashboard displays the results.

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

### Two generations of analysis code coexist

- **`Analyzer.py`** (root) — the original monolithic engine. The backend still imports its email functions (`build_email_body`, `build_subject`, `send_gmail`). It has a scheduler guard so importing it does not auto-run analysis.
- **`core/`** — the modular v2 pipeline, orchestrated by **`core/pipeline.py`** (`run_modular_analysis`, `get_modular_market_conditions`). Flow: **collectors → features → scoring → risk → lake/API**. This is what the backend actually runs for analysis. New analysis work belongs in `core/`, not `Analyzer.py`.

### Backend (`backend/api.py`)

FastAPI + APScheduler. Cron-fires the modular pipeline at 09:15 and 15:30 IST on weekdays, persists each run to SQLite (`backend/data/stockradar.db`, `runs` table with results as JSON), and serves the REST API the React dashboard consumes (`/api/latest`, `/api/history`, `/api/runs/{id}`, `/api/trigger`, `/api/market`, `/api/status`). All API responses pass through `sanitize_floats` — NaN/Inf must never reach JSON.

### core/ module layout

- `collectors/` — external data: `nse.py` (NSE live quotes from 26 indices, delivery %, FII/DII, options, bhavcopy — NSE's Akamai gate TLS-fingerprints clients, so `_make_session()` needs curl_cffi Chrome impersonation with a two-step warm-up, plain requests gets a cookieless 403; quotes use the `equity-stock-indices` endpoint and option chains use `option-chain-v3` + `option-chain-contract-info`, the pre-2026 endpoint names are dead; also provides `fetch_equity_symbols_from_bhavcopy()`, `fetch_cached_symbols_from_lake()`, and `save_known_symbols_to_lake()` for dynamic symbol resolution), `market_data.py` (multi-provider quote chain controlled by `MARKET_DATA_PROVIDER_ORDER` — Groww, Twelve Data, Alpha Vantage, FMP, Finnhub — **currently dead code**, see Data Sources & Freshness below), `global_data.py`, `fundamentals.py`, `news.py`
- `fetch/` — central outbound-HTTP engine (`engine.py`): every collector call goes through a per-host policy (concurrency semaphore, request pacing, timeouts, retries with jittered backoff, circuit breaker, metrics). Hosts: `yahoo`, `yahoo_bulk`, `nse`, `nse_archives`, `google_news`, `et_rss`, `screener`. Tunable via `FETCH_<HOST>_CONCURRENCY` / `_MIN_INTERVAL_MS` / `_TIMEOUT` / `_RETRIES` env vars; stats at `GET /api/fetch-stats`. Route any new outbound HTTP through `get_engine()`, never raw `requests`.
- `features/` — per-dimension feature computation (`technical`, `fundamental`, `institutional`, `sentiment`, `regime`), aggregated by `features/store.py`. Institutional features fetch option chains only for F&O symbols (`fetch_fno_symbols`) and cache market-wide FII/DII for 30 min.
- `scoring/hybrid.py` — weighted 6-dimension score (fundamental 30%, technical 25%, institutional 15%, sentiment 10%, sector 10%, risk 10%) with a bull/bear regime multiplier; score maps to BUY (≥75) / WATCH (60–74) / HOLD (40–59) / SELL (<40)
- `risk/engine.py` — ATR stops, Kelly sizing, applied to scored results
- `lake/` — DuckDB data lake; `manager.py` is a thread-safe connection manager (use `get_lake()` / `close_lake()`, never open connections directly), `schema.py` holds the DDL
- `events/` — pipeline observability: `LocalEventBus` (in-process, DuckDB-persisted) by default, `KafkaEventBus` optional; stages publish `StockEvent`s consumed by `/api` event endpoints
- `alerts/`, `backtest/` — Telegram/email alerting and VectorBT backtesting (with a manual fallback engine)

### Data Sources & Freshness

*Audited 2026-07-23 against the live NSE API, the DuckDB lake, and the last 6 rows of the `runs` table. Re-verify before trusting — upstream endpoints change.*

**Nothing here is streaming.** A run is a point-in-time snapshot taken at 09:15 / 15:30 IST. Within a run, freshness splits by symbol:

| Source | What it supplies | Real-time? |
|---|---|---|
| **NSE `equity-stock-indices`** (`nse.py:225`) | live LTP/OHLC/volume for the ~350 index constituents | **Yes** — live during market hours; response carries `marketStatus` + per-row `lastUpdateTime` |
| **Yahoo bulk** `yf.download(period="5d", interval="1d")` (`pipeline.py:217`) | price/volume for the ~1,650 supplementary symbols | **No** — daily bars; price is the last daily *Close*. Yahoo's NSE feed is ~15 min delayed even intraday |
| Yahoo `fast_info.last_price` (`pipeline.py:288`) | closer-to-live price | Effectively unused — `enrich_targets` at `pipeline.py:333` only covers symbols the bulk download *missed* |
| Yahoo 1y daily history (`pipeline.py:104`) | all Tier-1 + Tier-2 technicals | Daily |
| Yahoo regime tickers (`features/regime.py:194`) | `^NSEI`, `^INDIAVIX`, `CL=F`, `USDINR=X`, `^GSPC`, `^TNX` | Daily |
| NSE `fiidiiTradeReact` | FII/DII net flow | Daily / EOD |
| NSE `quote-equity` + bhavcopy | delivery % | T+1 |
| NSE bhavcopy CSV (`nsearchives`) | full-market EOD dump, symbol universe | Previous trading day |
| NSE `option-chain-v3` | PCR / max pain | Live-ish, but **off** — `collect_options_sample=False` (`nse.py:647`) |
| Screener.in + Yahoo | fundamentals, analyst ratings | Up to 7 days stale (`FUNDAMENTALS_CACHE_TTL_DAYS`) |
| Yahoo / Google News / ET RSS | headlines, sentiment | Varies |

**Consequence:** `price` and `intraday_change` mean different things per row. For the ~350 NSE names they are a live LTP and a live `pChange`; for the rest they are a prior daily close and a close-over-close day change. Nothing in the result payload records which.

**Known defects found in this audit:**

1. **Keyed providers are dead code.** `collect_keyed_quotes` is imported at `pipeline.py:41` and never called. Alpha Vantage / Finnhub / Twelve Data / FMP / Groww fetch nothing — the keys in `.env` are inert. `MARKET_DATA_PROVIDER_ORDER` has no effect on the modular pipeline.
2. **NSE dropped the `meta` object** from `equity-stock-indices` (verified live — row keys contain no `meta`). `nse.py:262-263` therefore always falls back to `companyName = symbol` and `industry = ""`. Confirmed: 400/400 results in each of the last 6 runs have `company_name == symbol` and `industry == "Unknown"`, *including* the NSE-sourced large caps. Prices are unaffected.
3. **`raw_ohlcv` is 100% Yahoo.** All 362,127 rows carry `source = 'yfinance'`; there is not a single `nse_live` row. The `nse_live` write only happens in `run_full_collection()` (`nse.py:660`), which the modular pipeline never calls — `_resolve_symbols_and_quotes` calls `collect_all_quotes` directly.
4. ~~**Market-conditions collector still blind**~~ — **fixed 2026-10-24.** Root cause was a key mismatch, not a fetch failure: `_market_payload` (`pipeline.py`) read `india_vix` / `nifty_change_1d` / `regime_label`, none of which `compute_regime_features()` emits — it emits `vix_value` / `market_regime` and had no 1-day NIFTY change at all. The payload therefore hard-coded itself to `None` / `0.0` / `NEUTRAL` while the upstream Yahoo fetches were healthy the whole time. Fix: added `nifty_change_1d` to `regime.py`, pointed `_market_payload` at the real keys, mapped `BULL`/`BEAR` → `BULLISH`/`BEARISH` (what the `runs` table, email template and dashboard badges expect), and added warnings so a genuinely empty regime is no longer silent. **If you add a key to the regime payload, add it on both sides** — this failed undetected for months because nothing cross-checks them.
5. NSE's own `timestamp` / `lastUpdateTime` are discarded; quote payloads stamp `fetched_at = datetime.now()` instead, so genuine upstream staleness is unobservable.

### Optional heavy dependencies degrade gracefully

FinBERT (`transformers`/`torch`) and VectorBT are commented out in `requirements.txt`. The code must keep working without them — sentiment falls back to VADER, backtesting falls back to the manual engine. Preserve these fallback paths when editing.

### Configuration

`.env` at the project root is loaded by both `backend/api.py` and `core/pipeline.py` (works regardless of launch CWD); missing keys are backfilled from `.env.nas.example`. Key vars: `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` / `RECIPIENT_EMAIL`, market-data API keys (`FINNHUB_API_KEY`, `TWELVE_DATA_API_KEY`, `FMP_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `NEWSAPI_KEY`), `MARKET_DATA_PROVIDER_ORDER`, `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, `DEEP_ANALYSIS_COUNT` (Tier-2 shortlist size, default 400), `FUNDAMENTALS_CACHE_TTL_DAYS` (DuckDB fundamentals cache TTL, default 7), `TIER2_WORKERS` (Tier-2 thread pool, default 32), `TIER2_DEADLINE_MINUTES` (hard Tier-2 wall-clock budget, default 20 — run continues with partial results), `BULK_PARALLEL_CHUNKS` (parallel bulk yf.download chunks, default 2), `ANALYSIS_STALE_MINUTES` (wedged-run guard in the backend, default 90).

### Frontend (`frontend/`)

React 19 + Vite, essentially a single-component app in `src/App.jsx` with the dark theme in `src/index.css`. Talks to the backend via `VITE_API_URL` (Vercel: full backend URL; Docker/NAS: `/api`, proxied by nginx).

### Deployment

Pushing to `master` triggers both a Vercel frontend rebuild and a GitHub Actions workflow (`.github/workflows/docker-images.yml`) that publishes backend + frontend images to ghcr.io. The Vercel deployment and the NAS Docker deployment (`docker-compose.nas.yml`) are independent — don't mix their configs.

## Platform Notes

Development happens on Windows — timezone-sensitive code uses `pytz` with `Asia/Kolkata`, and the backend sets `PYTHONUTF8=1` and filters harmless `WinError 10054` asyncio noise.

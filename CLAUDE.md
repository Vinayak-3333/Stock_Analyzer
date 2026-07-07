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

- `collectors/` — external data: `nse.py` (NSE live quotes from 26 indices, delivery %, FII/DII, options, bhavcopy — requires a warmed-up session with browser headers; also provides `fetch_equity_symbols_from_bhavcopy()`, `fetch_cached_symbols_from_lake()`, and `save_known_symbols_to_lake()` for dynamic symbol resolution), `market_data.py` (multi-provider fallback chain controlled by `MARKET_DATA_PROVIDER_ORDER` — includes Groww, Twelve Data, Alpha Vantage, FMP, Finnhub), `global_data.py`, `fundamentals.py`, `news.py`
- `fetch/` — central outbound-HTTP engine (`engine.py`): every collector call goes through a per-host policy (concurrency semaphore, request pacing, timeouts, retries with jittered backoff, circuit breaker, metrics). Hosts: `yahoo`, `yahoo_bulk`, `nse`, `nse_archives`, `google_news`, `et_rss`, `screener`. Tunable via `FETCH_<HOST>_CONCURRENCY` / `_MIN_INTERVAL_MS` / `_TIMEOUT` / `_RETRIES` env vars; stats at `GET /api/fetch-stats`. Route any new outbound HTTP through `get_engine()`, never raw `requests`.
- `features/` — per-dimension feature computation (`technical`, `fundamental`, `institutional`, `sentiment`, `regime`), aggregated by `features/store.py`. Institutional features fetch option chains only for F&O symbols (`fetch_fno_symbols`) and cache market-wide FII/DII for 30 min.
- `scoring/hybrid.py` — weighted 6-dimension score (fundamental 30%, technical 25%, institutional 15%, sentiment 10%, sector 10%, risk 10%) with a bull/bear regime multiplier; score maps to BUY (≥75) / WATCH (60–74) / HOLD (40–59) / SELL (<40)
- `risk/engine.py` — ATR stops, Kelly sizing, applied to scored results
- `lake/` — DuckDB data lake; `manager.py` is a thread-safe connection manager (use `get_lake()` / `close_lake()`, never open connections directly), `schema.py` holds the DDL
- `events/` — pipeline observability: `LocalEventBus` (in-process, DuckDB-persisted) by default, `KafkaEventBus` optional; stages publish `StockEvent`s consumed by `/api` event endpoints
- `alerts/`, `backtest/` — Telegram/email alerting and VectorBT backtesting (with a manual fallback engine)

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

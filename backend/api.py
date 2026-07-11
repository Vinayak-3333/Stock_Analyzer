"""
StockRadar IN — FastAPI Backend
================================
• Imports the modular pipeline from core/pipeline.py
• APScheduler: fires modular analysis at 09:15 & 15:30 IST on weekdays
• SQLite: persists every run (market conditions + all stock results as JSON)
• REST API consumed by the React dashboard
"""

import sys, os, json, sqlite3, logging, math
from datetime import datetime
from pathlib import Path
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from fastapi.responses import JSONResponse

# ── allow importing parent Analyzer.py ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

# ── Load .env from project root (backend is launched from backend/ subdir) ─────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv, dotenv_values
    _env_file    = _PROJECT_ROOT / ".env"
    _env_example = _PROJECT_ROOT / ".env.nas.example"
    if _env_file.exists():
        load_dotenv(dotenv_path=str(_env_file), override=False)
    # Backfill any key that is still empty from .env.nas.example
    if _env_example.exists():
        for _k, _v in dotenv_values(str(_env_example)).items():
            if _v and not os.environ.get(_k):
                os.environ[_k] = _v
    logging.getLogger("stockradar").info(
        "Env loaded from %s (AV key present: %s, TD key present: %s)",
        _env_file,
        bool(os.environ.get("ALPHA_VANTAGE_API_KEY")),
        bool(os.environ.get("TWELVE_DATA_API_KEY")),
    )
except Exception as _e:
    pass

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Import analysis functions (scheduler guard in Analyzer.py ensures no auto-run)
from Analyzer import (
    build_email_body,
    build_subject,
    send_gmail,
)
from core.pipeline import get_modular_market_conditions, run_modular_analysis
from core.lake.manager import close_lake
from core.events.store import get_event_stats, get_recent_events

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stockradar")

# ── Suppress harmless Windows asyncio WinError 10054 noise ────────────────────
# This fires when a browser tab refreshes/closes mid-response — not a real error.
class _SuppressWinError10054(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        msg = record.getMessage()
        return "WinError 10054" not in msg and "connection_lost" not in msg.lower()

for _noisy in ("asyncio", "uvicorn.error", "uvicorn.access"):
    _logger = logging.getLogger(_noisy)
    _logger.addFilter(_SuppressWinError10054())

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH  = DATA_DIR / "stockradar.db"

# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time         TEXT    NOT NULL,
                market_trend     TEXT,
                vix_value        REAL,
                nifty_change     REAL,
                sector_changes   TEXT,
                results_json     TEXT,
                email_sent       INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    log.info("DB initialised at %s", DB_PATH)


def sanitize_floats(obj):
    """
    Recursively walk obj (dict/list/scalar) and replace any float NaN or Inf
    with None so the result is always valid JSON.
    This prevents 'ValueError: Out of range float values are not JSON compliant'.
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_floats(i) for i in obj]
    return obj


def normalize_result_scores(results: list) -> list:
    """
    Keep older saved runs compatible after score-field changes.
    Earlier rows stored news_score as 0 even when factor_scores.sentiment existed.
    """
    for result in results or []:
        if not isinstance(result, dict):
            continue
        news_score = result.get("news_score")
        factor_sentiment = (result.get("factor_scores") or {}).get("sentiment")
        if (news_score is None or news_score == 0) and factor_sentiment is not None:
            result["news_score"] = factor_sentiment
    return results


def save_run(market: dict, results: list, email_sent: bool):
    # Sanitize before storing so DB never contains NaN/Inf strings
    clean_market  = sanitize_floats(market)
    clean_results = sanitize_floats(normalize_result_scores(results))
    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs
               (run_time, market_trend, vix_value, nifty_change, sector_changes, results_json, email_sent)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                clean_market.get("market_trend"),
                clean_market.get("vix_value"),
                clean_market.get("nifty_50_change"),
                json.dumps(clean_market.get("sector_changes", {})),
                json.dumps(clean_results),
                int(email_sent),
            ),
        )
        conn.commit()


# ── Analysis job ───────────────────────────────────────────────────────────────
_running = False        # prevent overlapping runs
_running_since = None   # when the in-flight run started
# A run older than this is considered wedged; the guard stops protecting it so
# the next scheduled run isn't silently skipped forever.
ANALYSIS_STALE_MINUTES = int(os.environ.get("ANALYSIS_STALE_MINUTES", "90"))


def _run_is_active() -> bool:
    """True if a run is in flight and hasn't exceeded the stale budget."""
    if not _running:
        return False
    if _running_since is None:
        return True
    age_min = (datetime.now() - _running_since).total_seconds() / 60
    if age_min > ANALYSIS_STALE_MINUTES:
        log.error(
            "Analysis run started %.0f min ago looks wedged (budget %d min) — "
            "allowing a new run", age_min, ANALYSIS_STALE_MINUTES,
        )
        return False
    return True


def analysis_job(send_email: bool = True):
    global _running, _running_since
    if _run_is_active():
        log.warning("Analysis already running — skipping trigger")
        return
    _running = True
    _running_since = datetime.now()
    log.info("=== Analysis job started ===")
    try:
        results = run_modular_analysis()
        market  = get_modular_market_conditions()
        emailed = False

        if send_email and results:
            ts      = datetime.now().strftime("%d %b %Y %H:%M IST")
            subject = build_subject(results)
            body    = build_email_body(results, ts)
            send_gmail(subject, body)
            emailed = True
            log.info("Email sent")

        save_run(market, results, emailed)
        log.info("=== Analysis job done — %d stocks ===", len(results))
    except Exception as e:
        log.exception("Analysis job failed: %s", e)
    finally:
        _running = False
        _running_since = None


# ── Scheduler ──────────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")
scheduler = BackgroundScheduler(timezone=IST)

# 09:15 IST and 15:30 IST, Mon–Fri.  coalesce + misfire_grace_time let a
# delayed job still fire once instead of being dropped silently.  Grace is
# 4h because this backend runs on a laptop that sleeps: waking at 11:00
# should still produce the morning run (live market data is still useful),
# not silently skip to 15:30.
scheduler.add_job(analysis_job, CronTrigger(day_of_week="mon-fri", hour=9,  minute=15, timezone=IST),
                  coalesce=True, misfire_grace_time=14400, max_instances=1)
scheduler.add_job(analysis_job, CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=IST),
                  coalesce=True, misfire_grace_time=14400, max_instances=1)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="StockRadar IN API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Force no-cache on every API response — prevents browser from serving stale stock data
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"]         = "no-cache"
        response.headers["Expires"]        = "0"
        return response

app.add_middleware(NoCacheMiddleware)


@app.on_event("startup")
def on_startup():
    init_db()
    scheduler.start()
    log.info("Scheduler started — jobs: %s", [str(j) for j in scheduler.get_jobs()])


@app.on_event("startup")
async def start_scheduler_nudge():
    """
    Keep APScheduler honest across system sleeps.

    Windows kernel waits don't elapse while the machine is suspended, so
    APScheduler's timer thread can sit frozen for days after a wake — jobs
    show a next_run in the past and never fire (observed 9–11 Jul 2026).
    scheduler.wakeup() interrupts that wait and forces the queue to be
    re-evaluated; this task fires it every 60s, so within a minute of any
    resume the scheduler either runs an overdue job (inside its misfire
    grace) or reschedules it properly.
    """
    import asyncio

    async def _nudge_loop():
        while True:
            await asyncio.sleep(60)
            try:
                if scheduler.running:
                    scheduler.wakeup()
            except Exception as exc:
                log.debug("Scheduler nudge failed: %s", exc)

    asyncio.get_running_loop().create_task(_nudge_loop())
    log.info("Scheduler sleep-wake nudge task started (60s interval)")


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown(wait=False)
    close_lake()


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    """Scheduler status, running flag, next scheduled runs."""
    jobs = []
    for j in scheduler.get_jobs():
        next_run = j.next_run_time
        jobs.append({
            "id":       j.id,
            "next_run": next_run.isoformat() if next_run else None,
        })
    with get_db() as conn:
        last = conn.execute("SELECT run_time, email_sent FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "scheduler_running": scheduler.running,
        "analysis_running":  _run_is_active(),
        "analysis_started":  _running_since.isoformat() if _running_since else None,
        "scheduled_jobs":    jobs,
        "last_run":          dict(last) if last else None,
        "ist_now":           datetime.now(IST).isoformat(),
    }


@app.get("/api/latest")
def latest():
    """Most recent analysis run with full stock results."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return JSONResponse({"run": None, "results": []})
    d = dict(row)
    d["results_json"]   = normalize_result_scores(json.loads(d["results_json"] or "[]"))
    d["sector_changes"] = json.loads(d["sector_changes"] or "{}")
    payload = sanitize_floats({"run": d, "results": d["results_json"]})
    return JSONResponse(payload)


@app.get("/api/history")
def history(limit: int = 20):
    """List of past runs (without full results for performance)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, run_time, market_trend, vix_value, nifty_change, email_sent,
                      json_array_length(results_json) AS stock_count
               FROM runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return JSONResponse(sanitize_floats([dict(r) for r in rows]))


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    """Full results for a specific historical run."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    d = dict(row)
    d["results_json"]   = normalize_result_scores(json.loads(d["results_json"] or "[]"))
    d["sector_changes"] = json.loads(d["sector_changes"] or "{}")
    return JSONResponse(sanitize_floats(d))


@app.post("/api/trigger")
def trigger(background_tasks: BackgroundTasks, email: bool = True):
    """Manually trigger analysis (runs in background)."""
    if _run_is_active():
        return {"status": "already_running", "message": "Analysis already in progress"}
    background_tasks.add_task(analysis_job, send_email=email)
    return {"status": "triggered", "message": "Analysis started in background"}


@app.get("/api/market")
def market():
    """Live market conditions (fetched fresh on demand)."""
    try:
        cond = get_modular_market_conditions()
        return cond
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
def events(limit: int = 100, stage: Optional[str] = None, symbol: Optional[str] = None):
    """Recent event-driven pipeline events."""
    return JSONResponse(sanitize_floats(get_recent_events(limit=limit, stage=stage, symbol=symbol)))


@app.get("/api/factor-ic")
def factor_ic(days: int = 90):
    """
    Factor information-coefficient report: how well each scoring factor
    (and the composite score) predicted realised 5/10/20-day forward
    returns, plus per-signal hit rates. Backfills labels first.
    """
    try:
        from core.backtest.labels import backfill_labels, compute_factor_ic
        backfill_labels()
        return JSONResponse(sanitize_floats(compute_factor_ic(days=days)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fetch-stats")
def fetch_stats():
    """Per-host fetch-engine metrics: calls, errors, retries, breaker state."""
    from core.fetch import get_engine
    return JSONResponse(sanitize_floats(get_engine().stats()))


@app.get("/api/events/status")
def events_status():
    """Event bus/lake health and per-stage counts."""
    stats = get_event_stats()
    return JSONResponse(
        sanitize_floats(
            {
                "event_pipeline": "enabled",
                "stages": [
                    "stage_1_ingestion",
                    "stage_2_feature_store",
                    "stage_3_event_engine",
                    "stage_4_scoring",
                    "stage_5_backtest_log",
                    "stage_6_ml_labeling",
                    "stage_7_deployment",
                    "stage_8_portfolio_risk",
                ],
                **stats,
            }
        )
    )


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)

"""
StockRadar IN — FastAPI Backend
================================
• Imports analysis engine from parent Analyzer.py
• APScheduler: fires run_analysis() at 09:15 & 15:30 IST on weekdays
• SQLite: persists every run (market conditions + all stock results as JSON)
• REST API consumed by the React dashboard
"""

import sys, os, json, sqlite3, logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

# ── allow importing parent Analyzer.py ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Import analysis functions (scheduler guard in Analyzer.py ensures no auto-run)
from Analyzer import (
    run_analysis,
    get_market_conditions,
    fetch_promoter_signals,
    build_email_body,
    build_subject,
    send_gmail,
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stockradar")

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


def save_run(market: dict, results: list, email_sent: bool):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs
               (run_time, market_trend, vix_value, nifty_change, sector_changes, results_json, email_sent)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                market.get("market_trend"),
                market.get("vix_value"),
                market.get("nifty_50_change"),
                json.dumps(market.get("sector_changes", {})),
                json.dumps(results, default=str),
                int(email_sent),
            ),
        )
        conn.commit()


# ── Analysis job ───────────────────────────────────────────────────────────────
_running = False   # prevent overlapping runs


def analysis_job(send_email: bool = True):
    global _running
    if _running:
        log.warning("Analysis already running — skipping trigger")
        return
    _running = True
    log.info("=== Analysis job started ===")
    try:
        results = run_analysis()
        market  = get_market_conditions()
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


# ── Scheduler ──────────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")
scheduler = BackgroundScheduler(timezone=IST)

# 09:15 IST and 15:30 IST, Mon–Fri
scheduler.add_job(analysis_job, CronTrigger(day_of_week="mon-fri", hour=9,  minute=15, timezone=IST))
scheduler.add_job(analysis_job, CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=IST))

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


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown(wait=False)


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
        "analysis_running":  _running,
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
        return {"run": None, "results": []}
    d = dict(row)
    d["results_json"]   = json.loads(d["results_json"] or "[]")
    d["sector_changes"] = json.loads(d["sector_changes"] or "{}")
    return {"run": d, "results": d["results_json"]}


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
    return [dict(r) for r in rows]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    """Full results for a specific historical run."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    d = dict(row)
    d["results_json"]   = json.loads(d["results_json"] or "[]")
    d["sector_changes"] = json.loads(d["sector_changes"] or "{}")
    return d


@app.post("/api/trigger")
def trigger(background_tasks: BackgroundTasks, email: bool = True):
    """Manually trigger analysis (runs in background)."""
    if _running:
        return {"status": "already_running", "message": "Analysis already in progress"}
    background_tasks.add_task(analysis_job, send_email=email)
    return {"status": "triggered", "message": "Analysis started in background"}


@app.get("/api/market")
def market():
    """Live market conditions (fetched fresh on demand)."""
    try:
        cond = get_market_conditions()
        return cond
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)

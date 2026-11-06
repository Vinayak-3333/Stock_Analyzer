"""
DuckDB Data Lake — Schema Initialisation
==========================================
Creates all tables on first run. Idempotent (IF NOT EXISTS).
Call init_schema() once at application startup.

Seven tables that nothing ever wrote (raw_options, raw_news, raw_macro,
raw_fundamentals, feature_snapshots, backtest_runs, alert_history) were
removed from the DDL on 2026-11-06. There is no DROP: lakes created
earlier keep them, empty, until someone drops them by hand.
"""

from .manager import get_lake
import logging

log = logging.getLogger("stockradar.lake")


DDL_STATEMENTS = [

    # ── Raw OHLCV (daily, from yfinance + NSE bhavcopy) ─────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_ohlcv (
        symbol      VARCHAR NOT NULL,
        date        DATE    NOT NULL,
        open        DOUBLE,
        high        DOUBLE,
        low         DOUBLE,
        close       DOUBLE,
        volume      BIGINT,
        source      VARCHAR DEFAULT 'yfinance',
        PRIMARY KEY (symbol, date)
    )
    """,

    # ── NSE Bhavcopy (full market end-of-day dump) ───────────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_bhavcopy (
        symbol          VARCHAR NOT NULL,
        date            DATE    NOT NULL,
        series          VARCHAR,
        open            DOUBLE,
        high            DOUBLE,
        low             DOUBLE,
        close           DOUBLE,
        prev_close      DOUBLE,
        traded_qty      BIGINT,
        traded_value    DOUBLE,
        total_trades    BIGINT,
        isin            VARCHAR,
        PRIMARY KEY (symbol, date)
    )
    """,

    # ── Delivery data (from bhavcopy CM + NSE delivery endpoint) ────────────
    """
    CREATE TABLE IF NOT EXISTS raw_delivery (
        symbol          VARCHAR NOT NULL,
        date            DATE    NOT NULL,
        traded_qty      BIGINT,
        delivered_qty   BIGINT,
        delivery_pct    DOUBLE,
        PRIMARY KEY (symbol, date)
    )
    """,

    # ── FII / DII daily net flow ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_fii_dii (
        date            DATE    PRIMARY KEY,
        fii_buy         DOUBLE,
        fii_sell        DOUBLE,
        fii_net         DOUBLE,
        dii_buy         DOUBLE,
        dii_sell        DOUBLE,
        dii_net         DOUBLE,
        source          VARCHAR DEFAULT 'nse'
    )
    """,

    # ── Option chain aggregate per symbol (PCR, max pain) ────────────────────
    # Written only by nse.run_full_collection(), the module's manual CLI
    # (`python -m core.collectors.nse`); scheduled runs don't fill it.
    """
    CREATE TABLE IF NOT EXISTS raw_options_summary (
        symbol          VARCHAR NOT NULL,
        snapshot_ts     TIMESTAMP NOT NULL,
        expiry          DATE,
        total_ce_oi     BIGINT,
        total_pe_oi     BIGINT,
        pcr             DOUBLE,   -- total PE OI / total CE OI
        max_pain_strike DOUBLE,
        atm_strike      DOUBLE,
        PRIMARY KEY (symbol, snapshot_ts, expiry)
    )
    """,

    # Event-driven pipeline audit log.
    """
    CREATE TABLE IF NOT EXISTS event_log (
        event_id        VARCHAR PRIMARY KEY,
        topic           VARCHAR,
        stage           VARCHAR,
        event_type      VARCHAR,
        symbol          VARCHAR,
        severity        VARCHAR,
        source          VARCHAR,
        created_at      TIMESTAMP,
        payload_json    VARCHAR
    )
    """,

    # Score history for momentum, backtesting replay, and future ML labels.
    """
    CREATE TABLE IF NOT EXISTS score_history (
        id                  UBIGINT DEFAULT hash(uuid()) PRIMARY KEY,
        run_id              VARCHAR,
        symbol              VARCHAR NOT NULL,
        scored_at           TIMESTAMP DEFAULT current_timestamp,
        score               DOUBLE,
        signal              VARCHAR,
        confidence          DOUBLE,
        price               DOUBLE,
        volume              BIGINT,
        factor_scores_json  VARCHAR,
        reasons_json        VARCHAR,
        label_5d_return     DOUBLE,
        label_10d_return    DOUBLE,
        label_20d_return    DOUBLE
    )
    """,

    # ── Fundamentals cache (full feature dict, TTL-refreshed weekly) ─────────
    """
    CREATE TABLE IF NOT EXISTS fundamentals_cache (
        symbol          VARCHAR PRIMARY KEY,
        as_of           DATE,
        features_json   VARCHAR
    )
    """,

    # ── Known symbols registry (dynamic stock universe cache) ───────────────
    """
    CREATE TABLE IF NOT EXISTS known_symbols (
        symbol      VARCHAR PRIMARY KEY,
        exchange    VARCHAR DEFAULT 'NSE',
        series      VARCHAR DEFAULT 'EQ',
        last_seen   DATE,
        source      VARCHAR   -- 'nse_index', 'bhavcopy', 'manual'
    )
    """,

    # ── Company name + NSE industry classification (getSymbolData cache) ──────
    """
    CREATE TABLE IF NOT EXISTS symbol_meta (
        symbol          VARCHAR PRIMARY KEY,
        company_name    VARCHAR,
        macro           VARCHAR,   -- e.g. 'Financial Services'
        sector          VARCHAR,   -- e.g. 'Financial Services'
        industry        VARCHAR,   -- e.g. 'Banks'
        basic_industry  VARCHAR,   -- e.g. 'Private Sector Bank'
        sector_index    VARCHAR,   -- e.g. 'NIFTY BANK'
        as_of           DATE
    )
    """,

    # ── Indexes for common query patterns ─────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_date     ON raw_ohlcv(symbol, date)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_sym_date  ON raw_delivery(symbol, date)",
    "CREATE INDEX IF NOT EXISTS idx_events_stage_time  ON event_log(stage, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_symbol_time ON event_log(symbol, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_scores_sym_time    ON score_history(symbol, scored_at)",
]


def init_schema():
    """Create all lake tables. Idempotent — safe to call on every startup."""
    conn = get_lake()
    for ddl in DDL_STATEMENTS:
        conn.execute(ddl)
    conn.commit()
    log.info("DuckDB lake schema initialised successfully")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_schema()
    print("Schema created successfully.")

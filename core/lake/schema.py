"""
DuckDB Data Lake — Schema Initialisation
==========================================
Creates all tables on first run. Idempotent (IF NOT EXISTS).
Call init_schema() once at application startup.
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

    # ── Options chain snapshots (key strikes around ATM) ────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_options (
        symbol          VARCHAR NOT NULL,
        snapshot_ts     TIMESTAMP NOT NULL,
        expiry          DATE,
        strike          DOUBLE,
        ce_oi           BIGINT,
        pe_oi           BIGINT,
        ce_volume       BIGINT,
        pe_volume       BIGINT,
        ce_ltp          DOUBLE,
        pe_ltp          DOUBLE,
        pcr_strike      DOUBLE,   -- PE OI / CE OI at this strike
        PRIMARY KEY (symbol, snapshot_ts, expiry, strike)
    )
    """,

    # ── Option chain aggregate per symbol (PCR, max pain) ────────────────────
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

    # ── News articles + sentiment ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_news (
        id              VARCHAR PRIMARY KEY,
        symbol          VARCHAR,
        headline        VARCHAR,
        summary         VARCHAR,
        source          VARCHAR,
        url             VARCHAR,
        published_at    TIMESTAMP,
        raw_sentiment   DOUBLE,   -- VADER compound (-1 to +1)
        finbert_score   DOUBLE,   -- FinBERT score (-1 to +1, NULL until processed)
        event_type      VARCHAR,  -- earnings / ma / regulatory / fraud / None
        fetched_at      TIMESTAMP DEFAULT current_timestamp
    )
    """,

    # ── Macro global data ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_macro (
        date            DATE    PRIMARY KEY,
        crude_usd       DOUBLE,
        usdinr          DOUBLE,
        sp500_change    DOUBLE,
        dow_change      DOUBLE,
        nasdaq_change   DOUBLE,
        india_vix       DOUBLE,
        us_10y_yield    DOUBLE,
        fetched_at      TIMESTAMP DEFAULT current_timestamp
    )
    """,

    # ── Fundamental data (from Screener.in + yfinance) ───────────────────────
    """
    CREATE TABLE IF NOT EXISTS raw_fundamentals (
        symbol              VARCHAR NOT NULL,
        as_of_date          DATE    NOT NULL,
        pe_ratio            DOUBLE,
        pb_ratio            DOUBLE,
        roe                 DOUBLE,
        roce                DOUBLE,
        debt_to_equity      DOUBLE,
        current_ratio       DOUBLE,
        fcf_yield           DOUBLE,
        eps_growth_1y       DOUBLE,
        eps_growth_3y       DOUBLE,
        revenue_growth_1y   DOUBLE,
        revenue_growth_3y   DOUBLE,
        promoter_holding    DOUBLE,
        pledged_pct         DOUBLE,
        market_cap_cr       DOUBLE,
        div_yield           DOUBLE,
        source              VARCHAR DEFAULT 'screener',
        PRIMARY KEY (symbol, as_of_date)
    )
    """,

    # ── Feature snapshots (point-in-time — safe for backtesting) ─────────────
    """
    CREATE TABLE IF NOT EXISTS feature_snapshots (
        id                  VARCHAR PRIMARY KEY,
        symbol              VARCHAR NOT NULL,
        snapshot_date       DATE    NOT NULL,
        -- Technical (25%)
        rsi                 DOUBLE,
        macd_hist           DOUBLE,
        adx                 DOUBLE,
        bb_pct              DOUBLE,
        stoch_k             DOUBLE,
        roc_5d              DOUBLE,
        roc_10d             DOUBLE,
        sma_ratio_50_200    DOUBLE,
        volume_ratio        DOUBLE,
        atr_pct             DOUBLE,
        rs_vs_nifty         DOUBLE,   -- Relative Strength vs NIFTY (1yr)
        is_52w_breakout     BOOLEAN,  -- nearWKH < 3% AND volume > 2x
        hh_hl_count         INTEGER,  -- Higher High / Higher Low weekly count
        -- Fundamental (30%)
        pe_ratio            DOUBLE,
        roe                 DOUBLE,
        roce                DOUBLE,
        debt_to_equity      DOUBLE,
        fcf_yield           DOUBLE,
        eps_growth_1y       DOUBLE,
        revenue_growth_1y   DOUBLE,
        promoter_holding    DOUBLE,
        pledged_pct         DOUBLE,
        delivery_pct_5d     DOUBLE,   -- 5-day avg delivery %
        delivery_spike      BOOLEAN,  -- today delivery > 2x 5d avg
        -- Institutional (15%)
        fii_3d_net          DOUBLE,   -- FII net crores last 3 days
        dii_3d_net          DOUBLE,
        fii_trend           INTEGER,  -- +1 buying, -1 selling, 0 neutral
        pcr                 DOUBLE,   -- Put/Call ratio
        max_pain_dist       DOUBLE,   -- % distance from max pain
        oi_buildup          BOOLEAN,  -- OI up + price up
        -- Sentiment (10%)
        news_score          DOUBLE,   -- FinBERT avg last 3 days
        news_momentum       DOUBLE,   -- 3d trend in sentiment
        event_flag          VARCHAR,  -- earnings / ma / regulatory / None
        gdelt_tone          DOUBLE,
        -- Regime / Macro (in sector + regime factor)
        market_regime       INTEGER,  -- 0=Bear 1=Neutral 2=Bull
        vix_level           INTEGER,  -- 0=Low 1=Med 2=High
        sector_momentum     DOUBLE,
        crude_trend         INTEGER,  -- +1 up, -1 down
        usdinr_trend        INTEGER,
        -- Labels (filled in after forward period)
        label_5d_return     DOUBLE,
        label_10d_return    DOUBLE,
        label_20d_return    DOUBLE,
        label_binary_10d    BOOLEAN   -- 1 if 10d return > +5%
    )
    """,

    # ── Backtest runs ─────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS backtest_runs (
        id              VARCHAR PRIMARY KEY,
        run_date        TIMESTAMP DEFAULT current_timestamp,
        strategy_name   VARCHAR,
        lookback_days   INTEGER,
        hold_days       INTEGER,
        min_score       DOUBLE,
        cagr            DOUBLE,
        sharpe          DOUBLE,
        sortino         DOUBLE,
        max_drawdown    DOUBLE,
        win_rate        DOUBLE,
        alpha_vs_nifty  DOUBLE,
        total_trades    INTEGER,
        profit_factor   DOUBLE,
        avg_hold_days   DOUBLE,
        config_json     VARCHAR   -- full strategy config as JSON
    )
    """,

    # ── Alert history ─────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS alert_history (
        id              VARCHAR PRIMARY KEY,
        symbol          VARCHAR,
        alert_type      VARCHAR,  -- breakout / volume_spike / fii_buying / news / score_change
        message         VARCHAR,
        triggered_at    TIMESTAMP DEFAULT current_timestamp,
        sent_email      BOOLEAN DEFAULT false,
        sent_telegram   BOOLEAN DEFAULT false
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

    # ── Indexes for common query patterns ─────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_date     ON raw_ohlcv(symbol, date)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_sym_date  ON raw_delivery(symbol, date)",
    "CREATE INDEX IF NOT EXISTS idx_news_symbol        ON raw_news(symbol, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_features_sym_date  ON feature_snapshots(symbol, snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_sym         ON alert_history(symbol, triggered_at)",
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

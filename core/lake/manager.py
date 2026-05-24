"""
DuckDB Data Lake — Connection Manager
======================================
Provides a thread-safe DuckDB connection for the analytical data lake.
All raw ingested data, feature snapshots, and backtest results live here.
"""

import duckdb
import threading
from pathlib import Path

# Lake file location (sibling to SQLite runs DB)
_LAKE_PATH = Path(__file__).parent.parent.parent / "backend" / "data" / "lake.duckdb"
_LAKE_PATH.parent.mkdir(parents=True, exist_ok=True)

_local = threading.local()


def get_lake() -> duckdb.DuckDBPyConnection:
    """
    Returns a thread-local DuckDB connection to the lake.
    DuckDB connections are not thread-safe; use one per thread.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = duckdb.connect(str(_LAKE_PATH))
        # Performance pragmas
        _local.conn.execute("PRAGMA threads=4")
        _local.conn.execute("PRAGMA memory_limit='1GB'")
    return _local.conn


def close_lake():
    """Close the thread-local connection (call at thread shutdown)."""
    if hasattr(_local, "conn") and _local.conn:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def execute(sql: str, params=None):
    """Convenience: execute SQL on the lake, return results."""
    conn = get_lake()
    if params:
        return conn.execute(sql, params)
    return conn.execute(sql)


def query_df(sql: str, params=None):
    """Execute SQL and return a Pandas DataFrame."""
    conn = get_lake()
    if params:
        return conn.execute(sql, params).df()
    return conn.execute(sql).df()

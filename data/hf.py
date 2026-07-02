"""
hf — DuckDB connection, source switch, path resolver, and the VERIFIED column registry.

Every SQL string in this package references only columns in COLUMNS below, which were confirmed
against the live dataset with `DESCRIBE` on 2026-07-01 (see the module test that diffs the registry
against a live DESCRIBE so a dataset schema drift fails loudly instead of silently returning wrong
prices).

Dataset facts (verified, not assumed):
  * ALL scalar columns are stored as VARCHAR — uint256 amounts, timestamps, and asset ids included.
    Cast explicitly: amounts/price via DOUBLE (matches the TS `Number()` in indexer/src/lib.ts),
    timestamps via BIGINT (epoch SECONDS).
  * Column names are camelCase (makerAssetId, payoutNumerators, ...), NOT snake_case.
  * Event tables are Hive-partitioned by `year` → `<table>/**/*.parquet`.
    State tables are a single file → `<table>.parquet` (no partition dir; a /**/ glob 404s).
  * The CLOB cash leg is assetId == '0'.
"""
from __future__ import annotations

import functools
import os

HF_DATASET = os.environ.get("HF_DATASET", "moose-code/polymarket-onchain-v1")
DATA_SOURCE = os.environ.get("DATA_SOURCE", "hf")            # "hf" | "graphql"
CACHE_DIR = os.environ.get("DATA_CACHE_DIR", ".data_cache")

# table -> physical layout on the Hub ("partitioned" = year-partitioned dir, "single" = one file)
TABLE_LAYOUT: dict[str, str] = {
    "order_filled": "partitioned",
    "orders_matched": "partitioned",
    "redemption": "partitioned",
    "split": "partitioned",
    "merge": "partitioned",
    "fee_refunded": "partitioned",
    "condition": "single",
    "market_data": "single",
    "user_position": "single",
}

# Verified column registry (from live DESCRIBE, 2026-07-01). All VARCHAR unless noted.
COLUMNS: dict[str, list[str]] = {
    "order_filled": ["id", "takerAmountFilled", "fee", "transactionHash", "timestamp",
                     "orderHash", "maker", "taker", "makerAssetId", "takerAssetId",
                     "makerAmountFilled", "year"],  # year BIGINT
    "condition": ["id", "positionIds", "payoutNumerators", "payoutDenominator"],  # []-typed cols
    "market_data": ["id", "endDate", "condition", "outcomeIndex", "marketName", "marketSlug",
                    "outcomes", "description", "image", "startDate"],
    "redemption": ["id", "timestamp", "redeemer", "condition", "indexSets", "payout", "year"],
    "user_position": ["id", "user", "tokenId", "amount", "avgPrice", "realizedPnl", "totalBought"],
    "orders_matched": ["id", "timestamp", "makerAssetID", "takerAssetID", "makerAmountFilled",
                       "takerAmountFilled", "year"],
    "split": ["id", "timestamp", "stakeholder", "condition", "amount", "year"],
    "merge": ["id", "timestamp", "stakeholder", "condition", "amount", "year"],
}


def table_path(table: str, *, prefer_cache: bool = True) -> str:
    """Resolve a table name to a DuckDB-readable path.

    Local materialized cache (`$DATA_CACHE_DIR/<table>/`) wins when present — this is the
    transparent hook for data.cache.materialize_replay_slice(): callers never change.
    Otherwise resolve to the Hub with the correct partitioned-vs-single suffix.
    """
    local_dir = os.path.join(CACHE_DIR, table)
    if prefer_cache and os.path.isdir(local_dir):
        return os.path.join(local_dir, "**", "*.parquet")
    layout = TABLE_LAYOUT.get(table, "partitioned")
    if layout == "single":
        return f"hf://datasets/{HF_DATASET}/{table}.parquet"
    return f"hf://datasets/{HF_DATASET}/{table}/**/*.parquet"


@functools.lru_cache(maxsize=1)
def connect():
    """A cached DuckDB connection with httpfs loaded (+ optional HF token for higher rate limits)."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    token = os.environ.get("HF_TOKEN")
    if token:
        con.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{token}');")
    return con


def reset_connection() -> None:
    """Drop the cached connection so the next call reconnects (recover from a poisoned/dropped conn)."""
    connect.cache_clear()


_TRANSIENT = ("ZSTD", "TProtocol", "Could not connect", "Connection", "HTTP", "timed out", "reset")


def with_retry(fn, *, attempts: int = 4):
    """Run fn(); on a TRANSIENT remote-read error (flaky hf:// reads) reset the connection and retry.

    Remote parquet scans over hf:// occasionally fail mid-scan (ZSTD/TProtocol/connection resets) in
    constrained networks. These are not logic errors — a fresh connection + retry usually succeeds.
    """
    import time as _t

    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - we re-raise non-transient below
            msg = str(e)
            if not any(t in msg for t in _TRANSIENT):
                raise
            last = e
            reset_connection()
            _t.sleep(2 * (i + 1))
    raise last


def query(sql: str, params: list | None = None):
    """Execute and return all rows (list of tuples), retrying transient remote-read failures."""
    return with_retry(lambda: connect().execute(sql, params or []).fetchall())


def query_df(sql: str, params: list | None = None):
    """Execute and return a pandas DataFrame (for dossier/analysis use)."""
    return connect().execute(sql, params or []).fetchdf()


def live_columns(table: str) -> list[str]:
    """The dataset's CURRENT columns for `table`, via DESCRIBE — used to guard registry drift."""
    rows = query(f"DESCRIBE SELECT * FROM '{table_path(table, prefer_cache=False)}' LIMIT 1")
    return [r[0] for r in rows]

"""
fills — the CLOB fill tape from HF `order_filled`, shaped as sigma.fetch_fills expects.

Exact SQL port of indexer/src/lib.ts:deriveFill (parity-tested in tests/test_data_fills.py):
  * cash leg is assetId == '0'
  * BUY  = maker paid collateral (makerAssetId == '0'); traded token = takerAssetId
  * SELL = maker sold the token   (makerAssetId != '0'); traded token = makerAssetId
  * price = collateral / outcome  (in (0,1));  size = outcome / 1e6  (6-decimal tokens); /0 → 0.0

Two adaptations vs the old GraphQL fetch_fills, both grounded in the VERIFIED dataset:
  1. Single-axis normalization. A binary market has two outcome tokens; fills land on either, so a
     naive tape is bimodal (p and 1-p interleaved), which inflates sigma. market_data.outcomeIndex
     is NULL in this dataset, so we can't label YES/NO — but we don't need to: pick a deterministic
     canonical leg (min tokenId) and map the other to 1-price. sigma of the log-odds is invariant to
     which leg is canonical, so this is correct and uses BOTH legs' liquidity. Callers that need a
     YES-semantic axis (the replay) can pass `canonical_token` explicitly.
  2. Prune by `year` (the Hive partition, tape spans 2022-2026): one market's tape is otherwise a
     full-table scan of 1.17B rows.

Output dict shape (drop-in for estimators.sigma): {price, size, side, maker, taker, timestamp}.
"""
from __future__ import annotations

from .hf import query, table_path

# deriveFill projection over an aliased order_filled row `o` → is_buy, tok, price, size, side.
DERIVE_FILL_SQL = """
    (o.makerAssetId = '0')                                                     AS is_buy,
    CASE WHEN o.makerAssetId = '0' THEN o.takerAssetId ELSE o.makerAssetId END AS tok,
    CASE
        WHEN (CASE WHEN o.makerAssetId='0' THEN o.takerAmountFilled ELSE o.makerAmountFilled END)::DOUBLE = 0
        THEN 0.0
        ELSE (CASE WHEN o.makerAssetId='0' THEN o.makerAmountFilled ELSE o.takerAmountFilled END)::DOUBLE
           / (CASE WHEN o.makerAssetId='0' THEN o.takerAmountFilled ELSE o.makerAmountFilled END)::DOUBLE
    END                                                                        AS price,
    (CASE WHEN o.makerAssetId='0' THEN o.takerAmountFilled ELSE o.makerAmountFilled END)::DOUBLE / 1e6 AS size,
    CASE WHEN o.makerAssetId='0' THEN 'BUY' ELSE 'SELL' END                    AS side
"""


def _year_bounds(condition_id: str) -> tuple[int, int] | None:
    """Active-year window from market_data.startDate/endDate (ISO strings) → prune the scan."""
    sql = f"""
        SELECT min(try_cast(substr(startDate,1,4) AS INTEGER)),
               max(try_cast(substr(coalesce(endDate,startDate),1,4) AS INTEGER))
        FROM '{table_path("market_data")}'
        WHERE condition = ?
    """
    r = query(sql, [condition_id])
    if not r or r[0][0] is None:
        return None
    lo, hi = r[0]
    return max(2022, int(lo) - 1), min(2026, int(hi or lo) + 1)


def fetch_fills_hf(condition_id: str, *, limit: int = 5000,
                   years: tuple[int, int] | None = None,
                   canonical_token: str | None = None) -> list[dict]:
    """A market's single-axis fill tape as {price,size,side,maker,taker,timestamp} dicts.

    `canonical_token`: token id whose price is kept as-is (others → 1-price). Defaults to the
    market's min tokenId (axis is deterministic but not necessarily semantic-YES).
    `years`: override the auto-derived year-prune window.
    """
    yb = years or _year_bounds(condition_id)
    year_filter = f"AND o.year BETWEEN {yb[0]} AND {yb[1]}" if yb else ""
    ref = f"'{canonical_token}'" if canonical_token else "(SELECT min(tok) FROM toks)"
    sql = f"""
        WITH toks AS (
            SELECT DISTINCT id AS tok FROM '{table_path("market_data")}' WHERE condition = ?
        ),
        f AS (
            SELECT o.maker, o.taker, o.timestamp, {DERIVE_FILL_SQL}
            FROM '{table_path("order_filled")}' o
            WHERE (o.makerAssetId IN (SELECT tok FROM toks) OR o.takerAssetId IN (SELECT tok FROM toks))
            {year_filter}
        )
        SELECT
            CASE WHEN f.tok = {ref} THEN f.price ELSE 1.0 - f.price END AS price,
            f.size, f.side, lower(f.maker) AS maker, lower(f.taker) AS taker,
            CAST(f.timestamp AS BIGINT) AS timestamp
        FROM f
        ORDER BY timestamp ASC
        LIMIT {int(limit)}
    """
    cols = ("price", "size", "side", "maker", "taker", "timestamp")
    return [dict(zip(cols, r)) for r in query(sql, [condition_id])]

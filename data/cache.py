"""
cache — materialize a small slice of the 127GB dataset to local parquet for fast iteration.

Why: order_filled has no token index, only a `year` partition, so one market's tape is a
multi-hundred-million-row remote scan (~minutes). The replay re-reads the same fills per arm x
lambda*-grid, and the sigma-prior corpus touches many markets — both are unusable remotely. So we
pull the relevant fills (+ their condition/market_data rows) ONCE into `$DATA_CACHE_DIR`, after
which data.hf.table_path() transparently resolves those tables locally and every downstream query
runs in milliseconds. Nothing else changes — callers never learn whether a table is local or remote.

Cache only the SLICE (disputed markets + matched controls), never the whole dataset.
"""
from __future__ import annotations

import os

from .hf import CACHE_DIR, connect, table_path


def _in_list(ids: list[str]) -> str:
    return ",".join("'" + i.replace("'", "") + "'" for i in ids)


def prefetch_state_tables(tables=("condition", "market_data"), *, log=print) -> dict:
    """Download the small single-file state tables to the local cache once (via huggingface_hub).

    Remote single-file reads over hf:// are occasionally flaky (ZSTD decompression resets); pulling
    these once makes every condition/market_data query local, reliable, and fast. order_filled (huge,
    partitioned) stays remote / materialized-by-slice.
    """
    import shutil

    from huggingface_hub import hf_hub_download

    from .hf import HF_DATASET

    out = {}
    for t in tables:
        dst_dir = os.path.join(CACHE_DIR, t)
        os.makedirs(dst_dir, exist_ok=True)
        path = hf_hub_download(repo_id=HF_DATASET, filename=f"{t}.parquet", repo_type="dataset")
        shutil.copy(path, os.path.join(dst_dir, "part.parquet"))
        out[t] = os.path.join(dst_dir, "part.parquet")
        if log:
            log(f"  prefetched {t} -> {out[t]}")
    return out


def materialize_slice(condition_ids: list[str], *, overwrite: bool = True,
                      years: tuple[int, int] | None = None) -> dict:
    """Copy order_filled (+ condition + market_data) rows for `condition_ids` into the local cache.

    `years`: optional (lo, hi) to prune the order_filled scan to those Hive partitions — a big speedup
    (and less exposure to transient remote drops) when the slice's markets are known to a period.
    Returns row counts written per table. After this, table_path('order_filled') etc. resolve locally.
    """
    if not condition_ids:
        raise ValueError("condition_ids is empty")
    con = connect()
    cids = _in_list(condition_ids)
    year_filter = f"AND o.year BETWEEN {years[0]} AND {years[1]}" if years else ""
    os.makedirs(CACHE_DIR, exist_ok=True)
    out: dict[str, int] = {}

    # State tables (condition, market_data) are handled by prefetch_state_tables() — the FULL tables,
    # so the slice's token lookups and any full-table queries stay correct. Only order_filled (the
    # 1.17B partitioned table) is sliced here. Prefetch first for reliable local reads.

    # order_filled — the heavy one. Filter to the slice's outcome tokens; keep the year partition.
    # Wrapped in with_retry: the big remote scan occasionally trips a transient hf:// read error.
    from .hf import connect as _connect
    from .hf import with_retry

    toks_cids = _in_list(condition_ids)
    dst = os.path.join(CACHE_DIR, "order_filled")
    copy_sql = f"""
        COPY (
            WITH toks AS (
                SELECT DISTINCT id AS tok FROM '{table_path("market_data")}'
                WHERE condition IN ({toks_cids})
            )
            SELECT o.* FROM '{table_path("order_filled", prefer_cache=False)}' o
            WHERE (o.makerAssetId IN (SELECT tok FROM toks)
                   OR o.takerAssetId IN (SELECT tok FROM toks))
                  {year_filter}
        ) TO '{dst}' (FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE)
    """
    with_retry(lambda: _connect().execute(copy_sql))
    out["order_filled"] = con.execute(f"SELECT count(*) FROM '{table_path('order_filled')}'").fetchone()[0]
    return out


def clear() -> None:
    """Remove the local cache (fall back to remote hf:// for everything)."""
    import shutil

    if os.path.isdir(CACHE_DIR):
        shutil.rmtree(CACHE_DIR)

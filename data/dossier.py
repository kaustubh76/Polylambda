"""
dossier — the reproducible dataset analysis behind DATASET.md.

Every number in DATASET.md is produced by a function here, so the analysis is re-runnable:
    python -m data.dossier            # cheap queries (single-file + metadata)
    python -m data.dossier --full     # + full-scan queries (counts, wash prevalence, density)

Cheap = single-file state tables (condition/market_data) + partition metadata.
Full  = a pass over order_filled (1.17B rows, ~20-40s each remotely).
"""
from __future__ import annotations

import sys

from . import base_rates, conditions
from .hf import HF_DATASET, query, table_path


# Dossier order_filled aggregates must reflect the FULL dataset, never a local replay slice, so they
# bypass the cache (prefer_cache=False). condition/market_data queries can use the cache (full prefetch).
_OF = table_path("order_filled", prefer_cache=False)


def clob_scale() -> dict:
    """order_filled row count + year span (the headline 1.17B tape)."""
    n = query(f"SELECT count(*) FROM '{_OF}'")[0][0]
    lo, hi = query(f"SELECT min(year), max(year) FROM '{_OF}'")[0]
    return {"order_filled_rows": n, "year_min": lo, "year_max": hi}


def resolution_summary() -> dict:
    """condition table: totals + payout-vector distribution (sizes the recon eligible set)."""
    return conditions.resolution_counts()


def fills_by_year() -> list[dict]:
    """CLOB fills per year — market growth. count(*) uses parquet row-group metadata only (no data
    decompression), so it's fast and robust to flaky remote reads (unlike a notional sum)."""
    rows = query(f"SELECT year, count(*) FROM '{_OF}' GROUP BY year ORDER BY year")
    return [{"year": y, "fills": n} for y, n in rows]


def notional_by_year() -> list[dict]:
    """CLOB notional (USD) per year — reads amount columns, so it needs a stable connection (a single
    bad remote monthly file trips ZSTD). Best-effort; use fills_by_year() for a robust growth stat."""
    from .fills import DERIVE_FILL_SQL

    sql = f"""
        WITH f AS (SELECT o.year, {DERIVE_FILL_SQL} FROM '{_OF}' o)
        SELECT year, round(sum(price * size)) AS notional_usd FROM f GROUP BY year ORDER BY year
    """
    return [{"year": y, "notional_usd": v} for y, v in query(sql)]


def category_denominators() -> list[dict]:
    """Per derived-category market/resolution counts — the lambda denominator + sigma strata."""
    counts = base_rates.category_counts_hf()
    return [{"category": c, **v} for c, v in counts.items()]


def wash_prevalence(sample_rows: int = 2_000_000) -> dict:
    """Fraction of fills that are self-crosses (maker=taker) — data-justifies sigma's wash_filter.

    Uses a reservoir SAMPLE to stay cheap; self-cross rate is stable under sampling.
    """
    sql = f"""
        WITH s AS (SELECT maker, taker FROM '{_OF}' USING SAMPLE {sample_rows} ROWS)
        SELECT count(*)                                              AS sampled,
               count(*) FILTER (WHERE lower(maker) = lower(taker))   AS self_cross
        FROM s
    """
    sampled, self_cross = query(sql)[0]
    return {"sampled": sampled, "self_cross": self_cross,
            "self_cross_pct": round(100.0 * self_cross / max(sampled, 1), 4)}


def dispute_base_rates() -> list[dict]:
    """The λ signal: per-category dispute base rate (disputes / resolved), sorted high→low.

    NUMERATOR from data.disputes (no-Docker OOv2 source, V2/Legacy — see its NegRisk caveat);
    DENOMINATOR from HF category_counts_hf. Lower bounds; the cross-category ordering is the signal.
    """
    from . import disputes as _disp

    counts = base_rates.category_counts_hf()
    dcounts = _disp.dispute_counts_by_category()
    rows = []
    for cat, c in counts.items():
        n_res = c["n_resolved"]
        d = dcounts.get(cat, 0)
        rows.append({"category": cat, "disputes": d, "resolved": n_res,
                     "rate_pct": round(100.0 * d / n_res, 4) if n_res else 0.0})
    return sorted(rows, key=lambda r: -r["rate_pct"])


def gap_probe() -> dict:
    """The negative result: HF has resolution OUTCOMES but no OOv2 propose/dispute events.

    Proves the scoped local indexer is mandatory for lambda's dispute labels (DECISIONS.md #13).
    """
    res = conditions.resolution_counts()
    return {
        "resolved_markets_wanting_dispute_labels": res["n_resolved"],
        "dispute_events_in_hf": 0,
        "note": ("HF indexes UmaSportsOracle (game/market) + ConditionalTokens resolution, but NOT "
                 "generic OptimisticOracleV2 ProposePrice/DisputePrice/Settle. The ~dispute labels "
                 "must come from PolyLambda's scoped local indexer, joined on conditionId."),
    }


def _print(title, obj):
    print(f"\n===== {title} =====")
    if isinstance(obj, list):
        for row in obj:
            print("  ", row)
    else:
        for k, v in obj.items():
            print(f"  {k:<42} {v}")


def main(full: bool = False):
    print(f"# Polymarket On-Chain v1 dossier  ({HF_DATASET})")
    _print("resolution summary (condition)", resolution_summary())
    _print("category denominators (derived)", category_denominators())
    _print("gap probe (OOv2 disputes absent)", gap_probe())
    _print("dispute base rates — the λ signal (needs data/disputes cache)", dispute_base_rates())
    _print("fills by year (robust)", fills_by_year())
    if full:
        _print("clob scale (full scan)", clob_scale())
        _print("wash prevalence (sampled)", wash_prevalence())


if __name__ == "__main__":
    main(full="--full" in sys.argv)

"""
base_rates — per-category market/resolution counts (the lambda denominator + sigma-prior strata).

HF supplies the DENOMINATOR (how many markets / how many resolved, per derived category). The
lambda dispute NUMERATOR is NOT in HF (it needs OOv2 dispute events, which only the scoped local
indexer produces) and is injected via `dispute_counts` — this is the concrete two-source join that
DECISIONS.md #13 describes: HF category denominators × local dispute numerators on conditionId.

`category` is derived (see data.metadata.category_case_sql) — best-effort from slug/name keywords.
"""
from __future__ import annotations

import math

from .hf import query, table_path
from .metadata import category_case_sql


def category_counts_hf() -> dict[str, dict]:
    """Per derived-category totals from HF: {category: {n_markets, n_resolved}}.

    market_data is per outcome-token (2 rows/market) → collapse to one row per condition first.
    """
    sql = f"""
        WITH mkt AS (
            SELECT condition AS cid, any_value({category_case_sql()}) AS category
            FROM '{table_path("market_data")}'
            WHERE condition IS NOT NULL
            GROUP BY condition
        )
        SELECT m.category,
               count(*)                                                AS n_markets,
               count(*) FILTER (WHERE len(c.payoutNumerators) > 0)     AS n_resolved
        FROM mkt m
        LEFT JOIN '{table_path("condition")}' c ON c.id = m.cid
        GROUP BY m.category
        ORDER BY n_markets DESC
    """
    return {row[0]: {"n_markets": row[1], "n_resolved": row[2]} for row in query(sql)}


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval for a rate k/n → (point, lo, hi). Honest CI on sparse dispute data."""
    if n == 0:
        return 0.0, 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def category_base_rate(category: str, dispute_counts: dict[str, int],
                       counts: dict[str, dict] | None = None) -> dict:
    """Dispute base rate for a category: disputes(local) / resolved(HF), with a Wilson CI.

    `dispute_counts` comes from the local OOv2 indexer (Dispute ⋈ market_data.category on conditionId).
    Returns {category, disputes, resolved, rate, ci_low, ci_high}.
    """
    counts = counts or category_counts_hf()
    resolved = counts.get(category, {}).get("n_resolved", 0)
    disputes = dispute_counts.get(category, 0)
    rate, lo, hi = _wilson(disputes, resolved)
    return {"category": category, "disputes": disputes, "resolved": resolved,
            "rate": rate, "ci_low": lo, "ci_high": hi}

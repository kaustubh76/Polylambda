"""
prior_corpus — build the (category, price, sigma) observation corpus that
estimators.sigma.category_price_prior consumes.

The pure prior function already exists and is unit-tested; what was missing is a real corpus. This
computes per-market sigma over a set of markets and tags each with its derived category + a
representative (median) price, yielding the `observations` list category_price_prior expects.

Cost note: per-market fetch_fills over the remote 1.17B tape is minutes each, so DO NOT build this
against hf:// for thousands of markets. Intended flow: materialize a stratified sample slice first
(data.cache.materialize_slice), then run this — reads come from the local cache in milliseconds.
Stratified sampling is enough: the prior is a per-(category x price-bucket)-cell mean and saturates
at a few hundred obs/cell (config: prior_sample_per_category, prior_min_markets_per_cell).
"""
from __future__ import annotations

from .fills import fetch_fills_hf
from .metadata import market_meta


def build_sigma_observation_corpus(condition_ids: list[str], *, min_trades: int = 20,
                                   b: float = 0.94, fill_limit: int = 5000) -> list[dict]:
    """[{category, price, sigma}] over the given markets (skip those thinner than min_trades)."""
    from estimators.sigma import estimate_sigma_from_fills

    obs: list[dict] = []
    for cid in condition_ids:
        fills = fetch_fills_hf(cid, limit=fill_limit)
        if len(fills) < min_trades:
            continue
        prices = sorted(f["price"] for f in fills)
        med = prices[len(prices) // 2]
        meta = market_meta(cid)
        cat = meta["category"] if meta else "other"
        # prior arg is irrelevant here (n >= min_trades → shrink trusts the market); we want raw sigma
        sig = estimate_sigma_from_fills(fills, prior=0.5, b=b, min_trades=min_trades)
        obs.append({"category": cat, "price": med, "sigma": sig})
    return obs


def sampled_condition_ids(per_category: int = 2000) -> list[str]:
    """A stratified sample of resolved markets (per derived category) to seed the corpus.

    Runs against the single-file market_data/condition tables (cheap) — returns ids to hand to
    data.cache.materialize_slice before building the corpus.
    """
    from .hf import query, table_path
    from .metadata import category_case_sql

    sql = f"""
        WITH mkt AS (
            SELECT condition AS cid, any_value({category_case_sql()}) AS category
            FROM '{table_path("market_data")}'
            WHERE condition IS NOT NULL
            GROUP BY condition
        ),
        resolved AS (
            SELECT m.cid, m.category,
                   row_number() OVER (PARTITION BY m.category ORDER BY m.cid) AS rn
            FROM mkt m
            JOIN '{table_path("condition")}' c ON c.id = m.cid
            WHERE len(c.payoutNumerators) > 0
        )
        SELECT cid FROM resolved WHERE rn <= {int(per_category)}
    """
    return [r[0] for r in query(sql)]

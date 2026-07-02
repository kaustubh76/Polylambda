"""
conditions — resolved markets + on-chain payout vectors, the recon ground truth.

HF `condition` = ConditionalTokens.ConditionPreparation + ConditionResolution. A row exists once a
condition is prepared; `payoutNumerators` is populated only when ConditionResolution fires. So:
    RESOLVED  <=>  len(payoutNumerators) > 0
`payoutNumerators` is the exact vector the on-chain event emits and the same thing the local indexer
stores as Market.finalOutcome (joined with ","). recon compares the two.

Caveat (verified): the HF `condition` table carries NO resolution timestamp and NO oracle/adapter
address (only id, positionIds, payoutNumerators, payoutDenominator). "supported-adapter" bucketing
and resolution-time therefore come from elsewhere (local indexer / market_data.endDate / redemption).
"""
from __future__ import annotations

from .hf import query, table_path


def _finaloutcome_expr(col: str = "payoutNumerators") -> str:
    """SQL: render a VARCHAR[] payout vector as the comma-joined string finalOutcome uses ('1,0')."""
    return f"array_to_string({col}, ',')"


def resolved_conditions(limit: int | None = None) -> list[dict]:
    """All RESOLVED conditions: {condition_id, payout, payout_numerators, position_ids}.

    `payout` is the comma-joined string directly comparable to a local Market.finalOutcome.
    """
    lim = f"LIMIT {int(limit)}" if limit else ""
    sql = f"""
        SELECT id                              AS condition_id,
               {_finaloutcome_expr()}          AS payout,
               payoutNumerators                AS payout_numerators,
               positionIds                     AS position_ids
        FROM '{table_path("condition")}'
        WHERE len(payoutNumerators) > 0
        {lim}
    """
    cols = ("condition_id", "payout", "payout_numerators", "position_ids")
    return [dict(zip(cols, r)) for r in query(sql)]


def payout_for(condition_id: str) -> str | None:
    """The comma-joined payout vector for one conditionId, or None if unresolved/unknown."""
    sql = f"""
        SELECT {_finaloutcome_expr()}
        FROM '{table_path("condition")}'
        WHERE id = ? AND len(payoutNumerators) > 0
        LIMIT 1
    """
    rows = query(sql, [condition_id])
    return rows[0][0] if rows else None


def hf_payout_map() -> dict[str, str]:
    """{conditionId: comma-joined payout vector} for every RESOLVED condition (recon ground truth).

    One query loads all ~992k payouts so recon can compare in memory (vs a per-market round trip).
    """
    sql = f"""
        SELECT id, {_finaloutcome_expr()}
        FROM '{table_path("condition")}'
        WHERE len(payoutNumerators) > 0
    """
    return {cid: payout for cid, payout in query(sql)}


def resolution_counts() -> dict:
    """Dossier query #2: total conditions, resolved, and payout-vector distribution.

    Payout vectors are heterogeneously scaled (denominator 1 → [1,0]; some UMA rows → [0,1e18]).
    So classify by ARGMAX of the numerators, which is scale-independent, not by string match.
    Arrays are 1-indexed in DuckDB.
    """
    a, b = "payoutNumerators[1]::DOUBLE", "payoutNumerators[2]::DOUBLE"
    sql = f"""
        SELECT
            count(*)                                                              AS n_conditions,
            count(*) FILTER (WHERE len(payoutNumerators) > 0)                     AS n_resolved,
            count(*) FILTER (WHERE len(payoutNumerators)=2 AND {a} > {b})         AS n_yes,
            count(*) FILTER (WHERE len(payoutNumerators)=2 AND {b} > {a})         AS n_no,
            count(*) FILTER (WHERE len(payoutNumerators)=2 AND {a} = {b} AND {a} > 0) AS n_split_5050,
            count(*) FILTER (WHERE len(payoutNumerators) > 0 AND len(payoutNumerators) <> 2) AS n_nonbinary
        FROM '{table_path("condition")}'
    """
    r = query(sql)[0]
    keys = ("n_conditions", "n_resolved", "n_yes", "n_no", "n_split_5050", "n_nonbinary")
    return dict(zip(keys, r))

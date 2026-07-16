"""One-time cache builder for the dashboard. Idempotent; safe to re-run.

Writes small JSON into .data_cache/webapp/ so every request-time path is offline & fast:
  * base_rate_counts.json    — HF resolved-market denominators (the only NETWORK step; DuckDB/HF).
  * disputes_by_proposer.json — proposer dispute history (offline, released parquet).
  * dispute_names.json        — conditionId -> {marketName, marketSlug} (offline, market_data parquet).

Run:  python -m webapp.backend.precompute            (from the repo root, in the .venv)
Missing caches degrade gracefully to webapp/backend/constants.py — the app still runs.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from .cache import DATA_CACHE, DISPUTES_PARQUET, PROJECT_ROOT, WEBAPP_CACHE


def _write(name: str, obj) -> None:
    WEBAPP_CACHE.mkdir(parents=True, exist_ok=True)
    with open(WEBAPP_CACHE / name, "w") as f:
        json.dump(obj, f, indent=1)


def build_base_rate_counts(force: bool = False) -> str:
    out = WEBAPP_CACHE / "base_rate_counts.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    from data.base_rates import category_counts_hf
    counts = category_counts_hf()
    _write("base_rate_counts.json", counts)
    return f"wrote base_rate_counts.json ({len(counts)} categories)"


def build_disputes_by_proposer(force: bool = False) -> str:
    out = WEBAPP_CACHE / "disputes_by_proposer.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    from estimators.hazard import _disputes_by_proposer
    dbp = _disputes_by_proposer()
    _write("disputes_by_proposer.json", dbp)
    return f"wrote disputes_by_proposer.json ({len(dbp)} proposers)"


def build_dispute_names(force: bool = False) -> str:
    out = WEBAPP_CACHE / "dispute_names.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    md = DATA_CACHE / "market_data" / "part.parquet"
    if not (DISPUTES_PARQUET.exists() and md.exists()):
        return "skip: market_data/disputes parquet not present (explorer runs without names)"
    import duckdb
    rows = duckdb.sql(
        f"""
        WITH names AS (
            SELECT condition AS cid, any_value(marketName) AS name, any_value(marketSlug) AS slug
            FROM '{md.as_posix()}' WHERE condition IS NOT NULL GROUP BY condition
        )
        SELECT d.conditionId, n.name, n.slug
        FROM '{DISPUTES_PARQUET.as_posix()}' d LEFT JOIN names n ON n.cid = d.conditionId
        """
    ).fetchall()
    mapping = {c: {"marketName": nm, "marketSlug": sl} for c, nm, sl in rows if nm}
    _write("dispute_names.json", mapping)
    return f"wrote dispute_names.json ({len(mapping)}/{len(rows)} named)"


def build_kappa_by_category(force: bool = False) -> str:
    """Per-category E[|realized jump|] cache → .data_cache/webapp/kappa_by_category.json, read by
    estimators.lambda_engine to replace the single-scalar jump_drift/e_loss. Offline (released parquet)."""
    out = WEBAPP_CACHE / "kappa_by_category.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    if not DISPUTES_PARQUET.exists():
        return "skip: disputes parquet not present (engine uses the scalar kappa_loss fallback)"
    from data.calibrate import build_kappa_by_category as _build
    return _build(path=str(out))


# --- HF backbone caches (surfaced in the new HF UI sections) ---------------------------------
# argmax of the 1e18-scaled binary payout vector → the resolved outcome label.
_HF_OUTCOME_SQL = """CASE
    WHEN len(c.payoutNumerators)=0 THEN NULL
    WHEN len(c.payoutNumerators)=2 AND TRY_CAST(c.payoutNumerators[1] AS DOUBLE) > TRY_CAST(c.payoutNumerators[2] AS DOUBLE) THEN 'YES'
    WHEN len(c.payoutNumerators)=2 AND TRY_CAST(c.payoutNumerators[2] AS DOUBLE) > TRY_CAST(c.payoutNumerators[1] AS DOUBLE) THEN 'NO'
    WHEN len(c.payoutNumerators)=2 THEN 'TIE'
    ELSE 'MULTI' END"""

# HF's own documented full-tape total (order_filled = 1.17B rows; the full tape is NOT shipped
# locally, only the disputed-market slice, so this headline count is the dataset's published stat).
_HF_TOTAL_FILLS = 1_172_658_611


def build_hf_overview(force: bool = False) -> str:
    """HF backbone overview → .data_cache/webapp/hf_overview.json: resolution outcome mix, markets by
    creation year, per-category market/resolution counts, and coverage/provenance. Computed live from
    the local full condition/market_data parquet (falls back to Hub via data.hf.table_path)."""
    out = WEBAPP_CACHE / "hf_overview.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    from data.hf import HF_DATASET, query, table_path
    from data.metadata import category_case_sql
    from data.base_rates import category_counts_hf
    cp, mp = table_path("condition"), table_path("market_data")

    tot, resolved = query(f"SELECT count(*), count(*) FILTER (WHERE len(payoutNumerators)>0) FROM '{cp}'")[0]
    o0, o1, tie = query(f"""WITH c AS (SELECT payoutNumerators pn FROM '{cp}'
        WHERE len(payoutNumerators)=2 AND payoutDenominator!='0')
        SELECT sum(CASE WHEN TRY_CAST(pn[1] AS DOUBLE)>TRY_CAST(pn[2] AS DOUBLE) THEN 1 ELSE 0 END),
               sum(CASE WHEN TRY_CAST(pn[2] AS DOUBLE)>TRY_CAST(pn[1] AS DOUBLE) THEN 1 ELSE 0 END),
               sum(CASE WHEN TRY_CAST(pn[1] AS DOUBLE)=TRY_CAST(pn[2] AS DOUBLE) THEN 1 ELSE 0 END)
        FROM c""")[0]
    yrs = query(f"""SELECT y, count(*) n FROM (
        SELECT condition, substr(any_value(startDate),1,4) y FROM '{mp}'
        WHERE condition IS NOT NULL AND startDate IS NOT NULL GROUP BY condition)
        WHERE y >= '2019' AND y <= '2027' GROUP BY y ORDER BY y""")
    drange = query(f"SELECT min(substr(startDate,1,10)), max(substr(startDate,1,10)) FROM '{mp}' WHERE startDate IS NOT NULL")[0]
    cats = category_counts_hf()

    data = {
        "resolution": {"YES": int(o0 or 0), "NO": int(o1 or 0), "tie": int(tie or 0),
                       "resolved": int(resolved), "unresolved": int(tot - resolved), "total": int(tot)},
        "markets_by_year": [{"year": r[0], "n": int(r[1])} for r in yrs],
        "by_category": [{"category": k, "n_markets": v["n_markets"], "n_resolved": v["n_resolved"]}
                        for k, v in sorted(cats.items(), key=lambda kv: -kv[1]["n_markets"]) if k != "null"],
        "coverage": {"repo": HF_DATASET, "total_conditions": int(tot), "resolved_conditions": int(resolved),
                     "total_fills": _HF_TOTAL_FILLS, "market_date_min": drange[0], "market_date_max": drange[1],
                     "cutoff_block": int(os.environ.get("HF_CUTOFF_BLOCK", "85948287"))},
        "note": ("Computed live from the HF dataset (condition + market_data). Resolution outcomes are "
                 "the argmax of each market's on-chain payout vector; markets-by-year is by creation date. "
                 "Total fills is the dataset's documented full-tape count."),
    }
    _write("hf_overview.json", data)
    return f"wrote hf_overview.json (conditions={tot:,}, categories={len(data['by_category'])})"


def build_hf_markets(force: bool = False, limit: int = 800) -> str:
    """Top-N most-recently-created markets → .data_cache/webapp/hf_markets.json for the HF market
    browser: {conditionId, marketName, marketSlug, category, startDate, endDate, resolved, resolvedOutcome}."""
    out = WEBAPP_CACHE / "hf_markets.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    from data.hf import query, table_path
    from data.metadata import category_case_sql
    cp, mp = table_path("condition"), table_path("market_data")
    rows = query(f"""
        WITH mkt AS (
            SELECT condition cid, any_value(marketName) mname, any_value(marketSlug) mslug,
                   any_value({category_case_sql()}) cat,
                   any_value(startDate) sd, any_value(endDate) ed
            FROM '{mp}' WHERE condition IS NOT NULL AND marketName IS NOT NULL GROUP BY condition)
        SELECT m.cid, m.mname, m.mslug, m.cat, substr(m.sd,1,10), substr(m.ed,1,10),
               len(c.payoutNumerators)>0 AS resolved, {_HF_OUTCOME_SQL} AS outcome
        FROM mkt m LEFT JOIN '{cp}' c ON c.id = m.cid
        ORDER BY m.sd DESC NULLS LAST LIMIT {int(limit)}""")
    markets = [{"conditionId": r[0], "marketName": r[1], "marketSlug": r[2], "category": r[3],
                "startDate": r[4], "endDate": r[5], "resolved": bool(r[6]), "resolvedOutcome": r[7]}
               for r in rows]
    _write("hf_markets.json", {"markets": markets, "n": len(markets),
                               "note": "Most-recently-created Polymarket markets from the HF dataset "
                                       "(market_data ⋈ condition). Resolution is the on-chain payout argmax."})
    return f"wrote hf_markets.json ({len(markets)} markets)"


def build_dispute_market_context(force: bool = False) -> str:
    """HF market context for the ~1.5k disputed conditions → .data_cache/webapp/dispute_market_context.json,
    keyed by conditionId: {category, startDate, endDate, resolved, resolvedOutcome}. Enriches the disputes
    explorer detail. Offline: released disputes.parquet ⋈ local market_data/condition."""
    out = WEBAPP_CACHE / "dispute_market_context.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    if not DISPUTES_PARQUET.exists():
        return "skip: released disputes parquet not present"
    from data.hf import query, table_path
    from data.metadata import category_case_sql
    cp, mp = table_path("condition"), table_path("market_data")
    rows = query(f"""
        WITH d AS (SELECT DISTINCT conditionId cid FROM '{DISPUTES_PARQUET.as_posix()}'),
        mkt AS (
            SELECT condition cid, any_value({category_case_sql()}) cat,
                   any_value(startDate) sd, any_value(endDate) ed
            FROM '{mp}' WHERE condition IN (SELECT cid FROM d) GROUP BY condition)
        SELECT d.cid, m.cat, substr(m.sd,1,10), substr(m.ed,1,10),
               len(c.payoutNumerators)>0 AS resolved, {_HF_OUTCOME_SQL} AS outcome
        FROM d LEFT JOIN mkt m ON m.cid = d.cid LEFT JOIN '{cp}' c ON c.id = d.cid""")
    ctx = {r[0]: {"category": r[1], "startDate": r[2], "endDate": r[3],
                  "resolved": bool(r[4]), "resolvedOutcome": r[5]} for r in rows}
    _write("dispute_market_context.json", ctx)
    return f"wrote dispute_market_context.json ({len(ctx)} disputed markets)"


def build_ablation_full(force: bool = False) -> str:
    """The richer 4-arm powered replay (incl. the hazard arm) → .data_cache/webapp/ablation_full.json,
    which services.ablation() serves in place of the published 3-arm constants. HEAVY + networked
    (HF fill tape via the indexer), so it's gated behind `--ablation`; the app degrades gracefully
    to the published constants when the artifact is absent."""
    out = WEBAPP_CACHE / "ablation_full.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    url = os.environ.get("INDEXER_GRAPHQL_URL") or os.environ.get("ENVIO_GRAPHQL_URL")
    if not url:
        return "skip: no INDEXER_GRAPHQL_URL set (needed for the live replay)"
    from forwardtest.replay_ablation import run_replay
    from webapp.backend.services import _ablation_rows_from_replay
    grid = [0.0005, 0.001, 0.002, 0.005, 0.01]
    rows = _ablation_rows_from_replay(run_replay(url, grid))
    if not rows:
        return "skip: replay produced no rows"
    _write("ablation_full.json", rows)
    return f"wrote ablation_full.json ({len(rows)} rows)"


def main() -> None:
    print(f"[precompute] project root: {PROJECT_ROOT}")
    steps = [build_disputes_by_proposer, build_dispute_names, build_base_rate_counts,
             build_kappa_by_category, build_hf_overview, build_hf_markets, build_dispute_market_context]
    if "--ablation" in os.sys.argv:
        steps.append(build_ablation_full)  # opt-in: heavy + networked
    for step in steps:
        try:
            print("[precompute]", step(force="--force" in os.sys.argv))
        except Exception as e:  # noqa: BLE001 — precompute is best-effort; fallbacks cover gaps
            print(f"[precompute] {step.__name__} FAILED (fallback will be used): {e!r}")


if __name__ == "__main__":
    main()

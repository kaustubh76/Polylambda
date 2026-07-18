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


def _now_utc_date() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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

# Fallback only: the dataset's documented full-tape total, used when the Hub/token is unavailable.
# When a token IS present we COMPUTE fills-by-year instead (see _hf_fills_by_year) — which reproduces
# this exact number, so it stays a faithful fallback rather than an unverifiable assertion.
_HF_TOTAL_FILLS = 1_172_658_611


def _hf_fills_by_year() -> tuple[list[dict], str]:
    """Real full-tape fill counts per year, from the HUB.

    CRITICAL: `order_filled` exists locally but only as the DISPUTED-MARKET SLICE (2024: ~2.5M vs the
    full tape's 57.6M), so we must force `prefer_cache=False` — counting the local dir would silently
    report slice numbers as if they were the whole dataset. Needs a token; degrades to ([], "published").
    """
    from data.hf import has_hf_token, query, table_path
    if not has_hf_token():
        return [], "published"
    try:
        rows = query(f"SELECT year, count(*) FROM '{table_path('order_filled', prefer_cache=False)}' "
                     f"GROUP BY year ORDER BY year")
        return [{"year": str(r[0]), "n": int(r[1])} for r in rows], "computed"
    except Exception:  # noqa: BLE001 — best effort; the documented total remains the fallback
        return [], "published"


def _hf_volume_cte(restrict_cids_sql: str | None = None) -> str:
    """SQL CTEs computing per-condition traded volume + trade count.

    Join: orderbook.id (tokenId) → market_data.id (tokenId) → market_data.condition. Both outcome legs
    are summed per condition (market_data.outcomeIndex is NULL in this dataset, so no YES/NO split is
    possible here — see data/metadata.py). `orderbook` is Hub-only → this needs a token + network.
    """
    from data.hf import table_path
    ob, mp = table_path("orderbook"), table_path("market_data")
    where = (f"WHERE condition IN ({restrict_cids_sql})" if restrict_cids_sql
             else "WHERE condition IS NOT NULL")
    return f"""
        ob AS (SELECT id tid, TRY_CAST(scaledCollateralVolume AS DOUBLE) vol,
                      TRY_CAST(tradesQuantity AS BIGINT) tr
               FROM '{ob}'),
        tok AS (SELECT id tid, condition cid FROM '{mp}' {where}),
        volc AS (SELECT t.cid cid, sum(ob.vol) volume, sum(ob.tr) trades
                 FROM tok t JOIN ob ON ob.tid = t.tid GROUP BY t.cid)"""


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
    fills, fills_src = _hf_fills_by_year()          # real full-tape counts (needs the HF token)
    total_fills = sum(f["n"] for f in fills) if fills else _HF_TOTAL_FILLS

    data = {
        "resolution": {"YES": int(o0 or 0), "NO": int(o1 or 0), "tie": int(tie or 0),
                       "resolved": int(resolved), "unresolved": int(tot - resolved), "total": int(tot)},
        "markets_by_year": [{"year": r[0], "n": int(r[1])} for r in yrs],
        "fills_by_year": fills,
        "by_category": [{"category": k, "n_markets": v["n_markets"], "n_resolved": v["n_resolved"]}
                        for k, v in sorted(cats.items(), key=lambda kv: -kv[1]["n_markets"]) if k != "null"],
        "coverage": {"repo": HF_DATASET, "total_conditions": int(tot), "resolved_conditions": int(resolved),
                     "total_fills": int(total_fills), "fills_source": fills_src,
                     "market_date_min": drange[0], "market_date_max": drange[1],
                     "cutoff_block": int(os.environ.get("HF_CUTOFF_BLOCK", "85948287"))},
        "built_at": _now_utc_date(),
        "note": ("Computed from the HF dataset. Resolution outcomes are the argmax of each market's "
                 "on-chain payout vector; markets-by-year is by creation date; fills-by-year is the "
                 "full CLOB tape" + (" (computed)." if fills else " (dataset-documented total; no HF token).")),
    }
    _write("hf_overview.json", data)
    return (f"wrote hf_overview.json (conditions={tot:,}, categories={len(data['by_category'])}, "
            f"fills={total_fills:,} [{fills_src}])")


def build_hf_markets(force: bool = False, top_volume: int = 600, recent: int = 400) -> str:
    """Market-browser payload → .data_cache/webapp/hf_markets.json.

    The set is the UNION of the top-N markets BY TRADED VOLUME and the N most recently created, so the
    browser can rank by either. Volume comes from the Hub `orderbook` table (needs the HF token); without
    a token we degrade to a recency-only, volume-less set rather than failing.
    """
    out = WEBAPP_CACHE / "hf_markets.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    from data.hf import has_hf_token, query, table_path
    from data.metadata import category_case_sql
    cp, mp = table_path("condition"), table_path("market_data")
    mkt_cte = f"""
        mkt AS (
            SELECT condition cid, any_value(marketName) mname, any_value(marketSlug) mslug,
                   any_value({category_case_sql()}) cat,
                   any_value(startDate) sd, any_value(endDate) ed
            FROM '{mp}' WHERE condition IS NOT NULL AND marketName IS NOT NULL GROUP BY condition)"""
    with_volume = has_hf_token()
    if with_volume:
        try:
            rows = query(f"""
                WITH {_hf_volume_cte()},
                {mkt_cte},
                top_vol AS (SELECT cid FROM volc ORDER BY volume DESC NULLS LAST LIMIT {int(top_volume)}),
                rec AS (SELECT cid FROM mkt ORDER BY sd DESC NULLS LAST LIMIT {int(recent)}),
                sel AS (SELECT cid FROM top_vol UNION SELECT cid FROM rec)
                SELECT m.cid, m.mname, m.mslug, m.cat, substr(m.sd,1,10), substr(m.ed,1,10),
                       len(c.payoutNumerators)>0 AS resolved, {_HF_OUTCOME_SQL} AS outcome,
                       v.volume, v.trades
                FROM sel s JOIN mkt m ON m.cid = s.cid
                LEFT JOIN volc v ON v.cid = s.cid
                LEFT JOIN '{cp}' c ON c.id = s.cid
                ORDER BY v.volume DESC NULLS LAST""")
        except Exception as e:  # noqa: BLE001 — Hub/orderbook unavailable → recency-only fallback
            with_volume, rows = False, None
            print(f"[precompute] hf_markets volume join failed ({e.__class__.__name__}); recency-only")
    if not with_volume:
        rows = query(f"""
            WITH {mkt_cte}
            SELECT m.cid, m.mname, m.mslug, m.cat, substr(m.sd,1,10), substr(m.ed,1,10),
                   len(c.payoutNumerators)>0 AS resolved, {_HF_OUTCOME_SQL} AS outcome,
                   NULL AS volume, NULL AS trades
            FROM mkt m LEFT JOIN '{cp}' c ON c.id = m.cid
            ORDER BY m.sd DESC NULLS LAST LIMIT {int(top_volume + recent)}""")
    markets = [{"conditionId": r[0], "marketName": r[1], "marketSlug": r[2], "category": r[3],
                "startDate": r[4], "endDate": r[5], "resolved": bool(r[6]), "resolvedOutcome": r[7],
                "volume": round(float(r[8]), 2) if r[8] is not None else None,
                "trades": int(r[9]) if r[9] is not None else None}
               for r in rows]
    _write("hf_markets.json", {
        "markets": markets, "n": len(markets), "has_volume": with_volume, "built_at": _now_utc_date(),
        "note": ("Top markets by traded volume ∪ most recently created, from the HF dataset "
                 "(market_data ⋈ condition ⋈ orderbook). Volume is USDC across both outcome legs; "
                 "resolution is the on-chain payout argmax." if with_volume else
                 "Most-recently-created Polymarket markets from the HF dataset (market_data ⋈ condition). "
                 "Volume needs an HF token (Hub-only orderbook table).")})
    return f"wrote hf_markets.json ({len(markets)} markets, volume={'yes' if with_volume else 'no'})"


def build_dispute_market_context(force: bool = False) -> str:
    """HF market context for the ~1.5k disputed conditions → .data_cache/webapp/dispute_market_context.json,
    keyed by conditionId: {category, startDate, endDate, resolved, resolvedOutcome}. Enriches the disputes
    explorer detail. Offline: released disputes.parquet ⋈ local market_data/condition."""
    out = WEBAPP_CACHE / "dispute_market_context.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    if not DISPUTES_PARQUET.exists():
        return "skip: released disputes parquet not present"
    from data.hf import has_hf_token, query, table_path
    from data.metadata import category_case_sql
    cp, mp = table_path("condition"), table_path("market_data")
    d_cte = f"d AS (SELECT DISTINCT conditionId cid FROM '{DISPUTES_PARQUET.as_posix()}')"
    mkt_cte = f"""
        mkt AS (
            SELECT condition cid, any_value({category_case_sql()}) cat,
                   any_value(startDate) sd, any_value(endDate) ed
            FROM '{mp}' WHERE condition IN (SELECT cid FROM d) GROUP BY condition)"""
    with_volume = has_hf_token()
    if with_volume:
        try:
            rows = query(f"""
                WITH {d_cte},
                {_hf_volume_cte(restrict_cids_sql='SELECT cid FROM d')},
                {mkt_cte}
                SELECT d.cid, m.cat, substr(m.sd,1,10), substr(m.ed,1,10),
                       len(c.payoutNumerators)>0 AS resolved, {_HF_OUTCOME_SQL} AS outcome,
                       v.volume, v.trades
                FROM d LEFT JOIN mkt m ON m.cid = d.cid
                LEFT JOIN volc v ON v.cid = d.cid
                LEFT JOIN '{cp}' c ON c.id = d.cid""")
        except Exception as e:  # noqa: BLE001
            with_volume, rows = False, None
            print(f"[precompute] dispute context volume join failed ({e.__class__.__name__}); no volume")
    if not with_volume:
        rows = query(f"""
            WITH {d_cte},
            {mkt_cte}
            SELECT d.cid, m.cat, substr(m.sd,1,10), substr(m.ed,1,10),
                   len(c.payoutNumerators)>0 AS resolved, {_HF_OUTCOME_SQL} AS outcome,
                   NULL AS volume, NULL AS trades
            FROM d LEFT JOIN mkt m ON m.cid = d.cid LEFT JOIN '{cp}' c ON c.id = d.cid""")
    ctx = {r[0]: {"category": r[1], "startDate": r[2], "endDate": r[3],
                  "resolved": bool(r[4]), "resolvedOutcome": r[5],
                  "volume": round(float(r[6]), 2) if r[6] is not None else None,
                  "trades": int(r[7]) if r[7] is not None else None} for r in rows}
    n_vol = sum(1 for v in ctx.values() if v.get("volume"))
    _write("dispute_market_context.json", ctx)
    return f"wrote dispute_market_context.json ({len(ctx)} disputed markets, {n_vol} with volume)"


def build_ablation_full(force: bool = False) -> str:
    """The richer 4-arm powered replay (incl. the hazard arm) → .data_cache/webapp/ablation_full.json,
    which services.ablation() serves in place of the published 3-arm constants. HEAVY (~5h: an HF fill
    fetch per market over ~7k disputed+control markets), so it's gated behind `--ablation`; the app
    degrades gracefully to the committed replay / published constants when the artifact is absent.

    Offline-capable: run_replay's default DATA_SOURCE=hf sources disputes from the released parquet and
    fills from HF, so no indexer is required (the url is used only for DATA_SOURCE=graphql). Writes a
    {run_date, meta, results} envelope so services can report counts that MATCH the served curve."""
    out = WEBAPP_CACHE / "ablation_full.json"
    if out.exists() and not force:
        return f"skip (exists): {out.name}"
    import datetime
    url = os.environ.get("INDEXER_GRAPHQL_URL") or os.environ.get("ENVIO_GRAPHQL_URL") or ""
    from forwardtest.replay_ablation import run_replay
    from webapp.backend.services import _ablation_rows_from_replay
    grid = [0.0005, 0.001, 0.002, 0.005, 0.01]
    res = run_replay(url, grid)                  # list[AblationResult]
    rows = _ablation_rows_from_replay(res)
    if not rows:
        return "skip: replay produced no rows"
    # counts are identical across results (they describe the run) — take them off the first
    meta = {"n_disputes_with_fills": getattr(res[0], "n_disputes", None),
            "n_controls_with_fills": getattr(res[0], "n_controls", None)}
    _write("ablation_full.json",
           {"run_date": datetime.date.today().isoformat(), "meta": meta, "results": rows})
    return f"wrote ablation_full.json ({len(rows)} rows, {meta})"


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

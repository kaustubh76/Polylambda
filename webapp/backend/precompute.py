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
    steps = [build_disputes_by_proposer, build_dispute_names, build_base_rate_counts]
    if "--ablation" in os.sys.argv:
        steps.append(build_ablation_full)  # opt-in: heavy + networked
    for step in steps:
        try:
            print("[precompute]", step(force="--force" in os.sys.argv))
        except Exception as e:  # noqa: BLE001 — precompute is best-effort; fallbacks cover gaps
            print(f"[precompute] {step.__name__} FAILED (fallback will be used): {e!r}")


if __name__ == "__main__":
    main()

"""
calibrate — data-derived constants that replace hand-picked placeholders.

kappa_loss (estimators.lambda_engine) was a 0.05 placeholder ("until the replay calibrates
jump_drift/e_loss from the HF realized post-resolution move", DECISIONS.md #9/P5). This computes it
empirically from the released dispute layer's `realizedJumpLogit` column — the actual logit-space
move a market made through its dispute/resolution — so `e_loss = kappa_loss * lambda_jump` carries a
measured "damage per jump", not a guess.

Import is filesystem-free; the parquet is only read when calibrate_kappa_loss() is called.
"""
from __future__ import annotations

import os

# Pinned calibration (mean |realizedJumpLogit| over the 1,149 disputes with price context in
# dataset_release/polymarket-oov2-disputes-v1/disputes.parquet, computed 2026-07-06). Used as the
# import-time default so estimators.lambda_engine stays pure/offline; recompute with
# calibrate_kappa_loss() when the released dataset changes.
KAPPA_LOSS_CALIBRATED = 0.76

_RELEASE_PARQUET = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "dataset_release", "polymarket-oov2-disputes-v1", "disputes.parquet",
)


def calibrate_kappa_loss(parquet_path: str = _RELEASE_PARQUET) -> dict:
    """E[|realizedJumpLogit| | dispute] and dispersion, from the released dispute layer.

    Returns {kappa_loss(mean_abs), median_abs, sd_abs, n}. `kappa_loss` is the mean absolute
    logit jump — the honest E[loss|jump] the diagram (Panel D) calls for. The distribution is
    right-skewed (median << mean), so the mean is the conservative central estimate for a premium.
    """
    import duckdb

    q = f"""
        SELECT avg(abs(realizedJumpLogit))    AS mean_abs,
               median(abs(realizedJumpLogit)) AS median_abs,
               stddev(abs(realizedJumpLogit)) AS sd_abs,
               count(realizedJumpLogit)        AS n
        FROM '{parquet_path}'
        WHERE realizedJumpLogit IS NOT NULL
    """
    mean_abs, median_abs, sd_abs, n = duckdb.sql(q).fetchone()
    return {"kappa_loss": float(mean_abs), "median_abs": float(median_abs),
            "sd_abs": float(sd_abs), "n": int(n)}


# ---------------------------------------------------------------------------------------------
# per-category calibration — replaces the single-scalar jump_drift/e_loss placeholder in
# estimators.lambda_engine with a category-specific E[|realized move|]. Thin categories are shrunk
# toward the global mean (empirical-Bayes style) so a 3-dispute category doesn't get a wild κ.
# ---------------------------------------------------------------------------------------------
_KAPPA_BY_CATEGORY_CACHE = os.path.join(
    os.environ.get("DATA_CACHE_DIR", ".data_cache"), "webapp", "kappa_by_category.json")
_SHRINK_N = 30  # pseudo-count: a category needs ~this many disputes to move off the global prior


def calibrate_kappa_by_category(parquet_path: str = _RELEASE_PARQUET, shrink_n: int = _SHRINK_N) -> dict:
    """Per-category E[|realizedJumpLogit|], shrunk toward the global mean for thin categories.

    Returns {"global": {kappa, n}, "categories": {cat: {kappa, kappa_raw, n}}}. `kappa` is the
    shrunk estimate the engine should use: (n*raw + shrink_n*global) / (n + shrink_n).
    """
    import duckdb

    g = calibrate_kappa_loss(parquet_path)
    global_kappa, global_n = g["kappa_loss"], g["n"]
    rows = duckdb.sql(f"""
        SELECT category,
               avg(abs(realizedJumpLogit)) AS mean_abs,
               count(realizedJumpLogit)    AS n
        FROM '{parquet_path}'
        WHERE realizedJumpLogit IS NOT NULL AND category IS NOT NULL
        GROUP BY category
    """).fetchall()
    cats = {}
    for cat, mean_abs, n in rows:
        n = int(n)
        raw = float(mean_abs)
        shrunk = (n * raw + shrink_n * global_kappa) / (n + shrink_n)
        cats[str(cat)] = {"kappa": round(shrunk, 5), "kappa_raw": round(raw, 5), "n": n}
    return {"global": {"kappa": round(global_kappa, 5), "n": int(global_n)}, "categories": cats}


def build_kappa_by_category(path: str = _KAPPA_BY_CATEGORY_CACHE,
                            parquet_path: str = _RELEASE_PARQUET) -> str:
    """Write the per-category κ cache that estimators.lambda_engine reads at request time."""
    import json

    data = calibrate_kappa_by_category(parquet_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
    return f"wrote {path} ({len(data['categories'])} categories, global κ={data['global']['kappa']})"


def load_kappa_by_category(path: str = _KAPPA_BY_CATEGORY_CACHE) -> dict | None:
    """The per-category κ map for the engine, or None if not built yet (→ scalar fallback)."""
    import json

    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


if __name__ == "__main__":
    import json
    import sys

    if "--by-category" in sys.argv:
        print(json.dumps(calibrate_kappa_by_category(), indent=2))
    elif "--build" in sys.argv:
        print(build_kappa_by_category())
    else:
        print(json.dumps(calibrate_kappa_loss(), indent=2))

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


if __name__ == "__main__":
    import json

    print(json.dumps(calibrate_kappa_loss(), indent=2))

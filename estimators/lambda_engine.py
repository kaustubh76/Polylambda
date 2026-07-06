"""
lambda_engine — dispute jump model. Emits TWO signals so the engine serves BOTH positionings
(see ../DECISIONS.md A):
  * lambda_select : slow per-market dispute-proneness -> market SELECTION / sizing (reward-farmer)
  * lambda_jump   : jump intensity -> DIRECTIONAL jump premium + reward-aware exit (jump-avoidance)

Corrected design (DECISIONS.md #9, #11):
  * Disputes are ~1% of markets -> CALIBRATION-LIMITED. v1 = category base-rate + a FEW
    point-in-time-safe features. Report WITH a confidence interval, not a point.
  * Drop subjective 'ambiguity'. EXCLUDE 'voter concentration' (only known AFTER a dispute ->
    lookahead leakage) from the onset model.
  * Time-to-resolution is BIMODAL (auto-reset ~2-4h vs DVM-escalated ~4-6d).
  * The costly event is a DIRECTIONAL jump + degraded exit liquidity (~5c haircut), NOT a lock.

TWO-SOURCE JOIN (DECISIONS.md #13): the DENOMINATOR (resolved markets per category) comes from the
HF dataset (data.base_rates, which derives category from market_data). The dispute NUMERATOR comes
ONLY from the scoped local OOv2 indexer (HF has no OOv2 dispute events) and is passed in as
`dispute_counts` keyed by category. Until that indexer has run, disputes default to 0 and lambda is
reported as a base-rate prior with a WIDE Wilson interval — honest about the missing labels.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LambdaOutput:
    lambda_select: float   # dispute-proneness score for market selection
    lambda_jump: float     # jump intensity for the pricing premium / exit trigger
    jump_drift: float      # expected directional jump move in LOGIT units (>0 = toward YES)
    e_loss: float          # E[loss | jump] in logit units (incl. ~5c-haircut exit cost model)
    ci_low: float          # confidence interval on lambda (sparse-data honesty)
    ci_high: float


# point-in-time-safe features only (no lookahead, no post-dispute signals)
SAFE_FEATURES = ("category_base_rate", "market_size", "proposer_reliability", "latency_anomaly")

# E[loss|jump] scaling, CALIBRATED from the released dispute layer (mean |realizedJumpLogit| = 0.76,
# data/calibrate.py) — replaces the old 0.05 placeholder. Kept as a module constant so this file
# stays import-pure; callers (config-driven) pass cfg.kappa_loss to override.
DEFAULT_KAPPA_LOSS = 0.76


def category_base_rate(category: str, dispute_counts: dict[str, int] | None = None,
                       counts: dict[str, dict] | None = None) -> dict:
    """Dispute base rate for a category = disputes(numerator) / resolved(HF), with a Wilson CI.

    Returns {category, disputes, resolved, rate, ci_low, ci_high}. `dispute_counts` is the two-source
    NUMERATOR; if None it is lazily loaded from `data.disputes` (the no-Docker OOv2 source, cached).
    If no labels exist yet, disputes=0 → rate 0 with a wide upper CI, the honest v1 output.
    """
    from data.base_rates import category_base_rate as _hf_base_rate

    if dispute_counts is None:
        try:
            from data.disputes import dispute_counts_by_category

            dispute_counts = dispute_counts_by_category()
        except Exception:
            dispute_counts = {}
    return _hf_base_rate(category, dispute_counts, counts)


def fit_hazard(labeled_rows):
    """Class-weighted logistic on SAFE_FEATURES; report calibration (Brier), NOT accuracy/AUC.

    labeled_rows: iterable of dicts with SAFE_FEATURES keys + integer `disputed` (0/1). The labels
    come from the local OOv2 indexer joined to HF features; disputes are ~1% so class_weight is
    balanced and the honest metric is calibration on held-out folds. Returns (model, {"brier":...}).
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss

    rows = list(labeled_rows)
    if len(rows) < 30 or len({r["disputed"] for r in rows}) < 2:
        raise ValueError("fit_hazard: need >=30 labeled rows spanning both classes "
                         "(disputes are ~1% — this is calibration-limited by design, DECISIONS.md #9)")
    X = np.array([[float(r[f]) for f in SAFE_FEATURES] for r in rows])
    y = np.array([int(r["disputed"]) for r in rows])
    model = LogisticRegression(class_weight="balanced", max_iter=1000).fit(X, y)
    brier = brier_score_loss(y, model.predict_proba(X)[:, 1])
    return model, {"brier": brier, "n": len(rows), "positives": int(y.sum())}


def estimate_lambda(market_conditionid: str, features: dict,
                    *, dispute_counts: dict[str, int] | None = None,
                    model=None, kappa_loss: float = DEFAULT_KAPPA_LOSS) -> LambdaOutput:
    """base-rate prior (+ optional hazard) -> lambda_select + lambda_jump + directional jump + CI.

    `features` must carry SAFE_FEATURES (incl. 'category'); `model` is an optional fitted hazard.
    `kappa_loss` maps a unit of dispute intensity to an expected logit-space loss (the ~5c haircut +
    directional move); calibrate it from HF realized moves near resolution in the replay (P5).
    """
    import math

    category = features.get("category", "other")
    base = category_base_rate(category, dispute_counts)
    lambda_select = base["rate"]

    if model is not None:
        import numpy as np

        x = np.array([[float(features.get(f, 0.0)) for f in SAFE_FEATURES]])
        lambda_jump = float(model.predict_proba(x)[0, 1])
    else:
        lambda_jump = lambda_select  # v1: fall back to the base rate

    # Directional jump: disputes resolve toward a boundary. Sign from the current price if given
    # (favorites tend to be confirmed); magnitude scales with intensity. Placeholder until the
    # replay calibrates jump_drift/e_loss from the HF realized post-resolution move.
    p = float(features.get("price", 0.5))
    logit_p = math.log(min(max(p, 1e-6), 1 - 1e-6) / (1 - min(max(p, 1e-6), 1 - 1e-6)))
    # NB: guard logit_p == 0 (p == 0.5) — math.copysign(x, 0.0) returns +x (the sign of +0.0), which
    # would leak a spurious positive/YES drift at a perfectly neutral price. A neutral market has no
    # directional jump, so jump_drift must be exactly 0 there.
    direction = 0.0 if logit_p == 0.0 else math.copysign(kappa_loss, logit_p)
    jump_drift = direction * lambda_jump
    e_loss = kappa_loss * lambda_jump

    return LambdaOutput(lambda_select=lambda_select, lambda_jump=lambda_jump,
                        jump_drift=jump_drift, e_loss=e_loss,
                        ci_low=base["ci_low"], ci_high=base["ci_high"])

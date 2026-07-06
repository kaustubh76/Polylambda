"""estimators/hazard.py — the structural dispute-onset model: pure feature transforms, an honest
prior-recalibrated fit, and integration into estimate_lambda. All offline/synthetic (no HF/network).
"""
import math

import pytest

pytest.importorskip("sklearn")

from estimators.hazard import (
    LoadedHazard, feature_row, fit_and_save, latency_anomaly_feature, load_hazard_model,
    market_size_feature, proposer_reliability_feature,
)
from estimators.lambda_engine import estimate_lambda


def test_feature_transforms_are_bounded_and_monotone():
    assert market_size_feature(0) == 0.0
    assert market_size_feature(1000) > market_size_feature(10) > 0
    # leave-one-out: a proposer's own market never leaks its label into its feature
    hist = {"0xp": 3}
    assert proposer_reliability_feature("0xp", hist, exclude=True) == pytest.approx(math.log1p(2))
    assert proposer_reliability_feature(None, hist) == 0.0
    assert latency_anomaly_feature(None, 100.0, 10.0) == 0.0        # unknown latency → neutral
    assert latency_anomaly_feature(120.0, 100.0, 10.0) > 0.0        # above-median → positive z


def _separable_rows(n=240):
    """Synthetic: disputes correlate strongly with market_size + proposer history."""
    rows = []
    for i in range(n):
        d = i % 2
        rows.append(feature_row(category_base_rate=0.01,
                                market_size=(6.0 if d else 1.0) + 0.01 * (i % 5),
                                proposer_reliability=(1.5 if d else 0.0),
                                latency_anomaly=0.0, disputed=d))
    return rows


def test_fit_reports_honest_holdout_and_recalibrates(tmp_path):
    path = str(tmp_path / "hz.json")
    m = fit_and_save(_separable_rows(), natural_rate=0.01, path=path)
    # discrimination is the honest headline; the set is separable so AUC should be high
    assert m["holdout_auc"] is not None and m["holdout_auc"] > 0.9
    assert m["discriminates"] is True
    assert "CALIBRATION-LIMITED" in m["caveat"]                     # the honesty caveat is always present
    assert m["pi_train"] == pytest.approx(0.5, abs=0.05)            # class-balanced training set
    # the prior-correction offset pushes outputs from ~0.5 down toward the ~1% natural rate
    assert m["offset"] < 0


def test_loaded_model_outputs_natural_scale_probabilities(tmp_path):
    path = str(tmp_path / "hz.json")
    fit_and_save(_separable_rows(), natural_rate=0.01, path=path)
    lo = load_hazard_model(path)
    assert isinstance(lo, LoadedHazard)
    p_dis = lo.predict_proba([[0.01, 6.0, 1.5, 0.0]])[0, 1]
    p_ctrl = lo.predict_proba([[0.01, 1.0, 0.0, 0.0]])[0, 1]
    assert p_dis > p_ctrl                                           # discriminates
    # prior-correction pulls a control-like market down toward the ~1% natural rate (not ~0.5 that a
    # class-weighted logistic emits on a balanced set); assert the offset actually shifts it low
    assert p_ctrl < 0.1
    assert lo.offset < 0


def test_integrates_into_estimate_lambda(tmp_path):
    path = str(tmp_path / "hz.json")
    fit_and_save(_separable_rows(), natural_rate=0.01, path=path)
    lo = load_hazard_model(path)
    feats = {"category": "crypto", "price": 0.5, "category_base_rate": 0.01,
             "market_size": 6.0, "proposer_reliability": 1.5, "latency_anomaly": 0.0}
    out = estimate_lambda("0xc", feats, model=lo, dispute_counts={"crypto": 0})
    # with a model, lambda_jump is the hazard's calibrated probability (NOT the flat base rate)
    assert out.lambda_jump == pytest.approx(lo.predict_proba([[0.01, 6.0, 1.5, 0.0]])[0, 1])
    assert out.lambda_jump != out.lambda_select                    # engine ≠ base rate now


def test_load_missing_model_returns_none(tmp_path):
    assert load_hazard_model(str(tmp_path / "nope.json")) is None

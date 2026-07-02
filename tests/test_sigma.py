"""Tests for estimators/sigma.py (pure core)."""
from estimators.sigma import (
    category_price_prior,
    estimate_sigma_from_fills,
    ewma_sigma,
    logit_returns,
    price_bucket,
    robust_ewma_sigma,
    shrink,
    wash_filter,
)


def test_logit_returns_length():
    assert len(logit_returns([0.5, 0.6, 0.4])) == 2


def test_ewma_constant_returns():
    # constant |return| -> sigma equals that magnitude, independent of b
    assert abs(ewma_sigma([0.1] * 10, b=0.9) - 0.1) < 1e-9


def test_robust_caps_wash_spike():
    series = [0.05, -0.05, 0.05, -0.05, 0.05, -0.05, 0.05, -0.05, 3.0]
    assert robust_ewma_sigma(series, b=0.5, winsor_k=5.0) < ewma_sigma(series, b=0.5)


def test_shrink_thin_pulls_to_prior():
    thin = shrink(1.0, 0.2, n_obs=2, strength=20.0)
    rich = shrink(1.0, 0.2, n_obs=500, strength=20.0)
    assert abs(thin - 0.2) < abs(rich - 0.2)   # thin closer to prior
    assert rich > thin                          # more data -> toward market (1.0)


def test_wash_filter():
    fills = [
        {"maker": "0xA", "taker": "0xA", "size": 100, "price": 0.5},  # self-cross
        {"maker": "0xA", "taker": "0xB", "size": 0.1, "price": 0.5},  # sub-min-size
        {"maker": "0xA", "taker": "0xB", "size": 100, "price": 1.5},  # bad price
        {"maker": "0xA", "taker": "0xB", "size": 100, "price": 0.5},  # keep
    ]
    out = wash_filter(fills, min_size=1.0)
    assert len(out) == 1 and out[0]["price"] == 0.5


def test_price_bucket():
    assert price_bucket(0.05) == 0
    assert price_bucket(0.5) == 2
    assert price_bucket(0.95) == 4


def test_category_price_prior():
    obs = [
        {"category": "pol", "price": 0.50, "sigma": 0.4},
        {"category": "pol", "price": 0.55, "sigma": 0.6},   # same bucket as 0.52
        {"category": "pol", "price": 0.95, "sigma": 2.0},   # different bucket
        {"category": "crypto", "price": 0.50, "sigma": 1.0},
    ]
    assert abs(category_price_prior(obs, "pol", 0.52) - 0.5) < 1e-9        # mean(0.4, 0.6)
    assert abs(category_price_prior(obs, "unknown", 0.5) - 1.0) < 1e-9     # global mean


def test_estimate_sigma_fallback_when_thin():
    fills = [{"maker": "0xA", "taker": "0xB", "size": 100, "price": 0.5}]
    assert estimate_sigma_from_fills(fills, prior=0.7, min_trades=20) == 0.7


def test_estimate_sigma_runs_on_clean_tape():
    prices = [0.50, 0.51, 0.49, 0.50, 0.52, 0.48, 0.50, 0.51, 0.49, 0.50, 0.51,
              0.49, 0.50, 0.52, 0.48, 0.50, 0.51, 0.49, 0.50, 0.51, 0.49, 0.50]
    fills = [{"maker": "0xA", "taker": "0xB", "size": 100, "price": p} for p in prices]
    s = estimate_sigma_from_fills(fills, prior=0.5, b=0.94, min_trades=20, strength=5.0)
    assert 0.0 < s < 5.0

"""Tests for estimators/fair_value.py."""
from estimators.fair_value import (
    depth_weighted_mid,
    estimate_fair_value,
    favorite_longshot_tilt,
)


def test_depth_weighted_mid_simple():
    assert abs(depth_weighted_mid([(0.4, 100)], [(0.6, 100)]) - 0.5) < 1e-9


def test_depth_weighted_mid_size_weighting():
    # bid vwap = (0.4*100 + 0.3*300)/400 = 0.325 ; mid = (0.325 + 0.6)/2 = 0.4625
    m = depth_weighted_mid([(0.4, 100), (0.3, 300)], [(0.6, 100)])
    assert abs(m - 0.4625) < 1e-9


def test_tilt_sign_and_taper():
    assert favorite_longshot_tilt(0.8, T_t=10.0) > 0   # favorite nudged up
    assert favorite_longshot_tilt(0.2, T_t=10.0) < 0   # longshot nudged down
    assert favorite_longshot_tilt(0.8, T_t=0.0) == 0.0  # tapers to 0 at resolution


def test_estimate_fair_value_bounds_and_direction():
    fv = estimate_fair_value({"bids": [(0.84, 100)], "asks": [(0.86, 100)]}, T_t=10.0)
    assert 0.0 < fv < 1.0
    assert fv > 0.85  # favorite -> slightly above the raw 0.85 mid

"""forgone_rewards_if_exit / _reward_score — the DECISIONS.md #6 truth table (pure, offline)."""
import pytest

from execution.loop import (DAILY_FLOOR_USD, SINGLE_SIDED_FACTOR, _reward_score,
                            forgone_rewards_if_exit)

MS = dict(max_incentive_spread=0.03, reward_min_size=5.0, rewards_daily_rate_usd=50.0,
          danger_window_days=0.125)


def _st(mid, bid=None, ask=None, bs=0.0, as_=0.0, **over):
    return {**MS, "mid": mid, "our_bid": bid, "our_ask": ask, "bid_size": bs, "ask_size": as_, **over}


def test_two_sided_at_mid_full_credit_quadratic():
    # both sides at distance max_spread/2 -> per-side factor (1/2)^2 = 0.25
    s = _reward_score(0.50, 0.485, 0.515, 10.0, 10.0, 0.03, 5.0)
    assert s == pytest.approx(2 * 10.0 * 0.25)


def test_single_sided_inside_band_is_one_third():
    two = _reward_score(0.50, 0.485, 0.515, 10.0, 10.0, 0.03, 5.0)
    one = _reward_score(0.50, 0.485, None, 10.0, 0.0, 0.03, 5.0)
    assert one == pytest.approx(SINGLE_SIDED_FACTOR * (two / 2))


def test_single_sided_outside_band_is_zero():
    assert _reward_score(0.05, 0.045, None, 10.0, 0.0, 0.03, 5.0) == 0.0
    assert _reward_score(0.95, None, 0.955, 0.0, 10.0, 0.03, 5.0) == 0.0


def test_sub_min_size_and_beyond_max_spread_zero():
    assert _reward_score(0.50, 0.49, None, 4.9, 0.0, 0.03, 5.0) == 0.0       # size < min
    assert _reward_score(0.50, 0.46, None, 10.0, 0.0, 0.03, 5.0) == 0.0      # dist > max_spread


def test_score_monotone_in_size_and_proximity():
    near = _reward_score(0.50, 0.495, 0.505, 10.0, 10.0, 0.03, 5.0)
    far = _reward_score(0.50, 0.475, 0.525, 10.0, 10.0, 0.03, 5.0)
    big = _reward_score(0.50, 0.495, 0.505, 20.0, 20.0, 0.03, 5.0)
    assert near > far and big > near


def test_forgone_zero_when_not_earning():
    assert forgone_rewards_if_exit(_st(0.5)) == 0.0                          # no quotes at all
    assert forgone_rewards_if_exit(_st(0.05, bid=0.045, bs=10.0)) == 0.0     # outside the band


def test_forgone_scales_with_daily_rate_and_window():
    f = forgone_rewards_if_exit(_st(0.5, bid=0.49, ask=0.51, bs=10.0, as_=10.0))
    # share=1 (competitor_score default 0) -> 50 USD/day * 0.125d = 6.25
    assert f == pytest.approx(50.0 * 0.125)
    f2 = forgone_rewards_if_exit(_st(0.5, bid=0.49, ask=0.51, bs=10.0, as_=10.0,
                                     danger_window_days=0.25))
    assert f2 == pytest.approx(2 * f)


def test_daily_floor_applies_when_earning_a_little():
    f = forgone_rewards_if_exit(_st(0.5, bid=0.49, ask=0.51, bs=10.0, as_=10.0,
                                    rewards_daily_rate_usd=0.0))
    assert f == pytest.approx(DAILY_FLOOR_USD * 0.125)                       # floored, not zero


def test_competitor_score_reduces_share():
    full = forgone_rewards_if_exit(_st(0.5, bid=0.49, ask=0.51, bs=10.0, as_=10.0))
    s = _reward_score(0.5, 0.49, 0.51, 10.0, 10.0, 0.03, 5.0)
    half = forgone_rewards_if_exit(_st(0.5, bid=0.49, ask=0.51, bs=10.0, as_=10.0,
                                       competitor_score=s))                 # equal competitor
    assert half == pytest.approx(full / 2)

"""Unit + property tests for pricing/quote.py (the verified A-S + jump core)."""
import math

from pricing.quote import (
    QuoteParams,
    compute_quote,
    diffusion_spread_logit,
    inventory_cap,
    price_half_spread_via_jacobian,
    to_logit,
    to_prob,
)


def test_logit_roundtrip():
    for p in (0.01, 0.25, 0.5, 0.85, 0.99):
        assert abs(to_prob(to_logit(p)) - p) < 1e-9


def test_quote_bounds():
    """0 < bid < ask < 1 across the probability range and inventory signs."""
    for p in (0.05, 0.5, 0.9):
        for q in (-50.0, 0.0, 50.0):
            bid, ask = compute_quote(p, q=q, sigma=0.4, T_t=5.0)
            assert 0.0 < bid < ask < 1.0


def test_spread_increases_with_sigma():
    lo_b, lo_a = compute_quote(0.5, q=0.0, sigma=0.2, T_t=5.0)
    hi_b, hi_a = compute_quote(0.5, q=0.0, sigma=0.8, T_t=5.0)
    assert (hi_a - hi_b) > (lo_a - lo_b)


def test_jump_premium_widens_spread():
    off_b, off_a = compute_quote(0.5, q=0.0, sigma=0.4, T_t=5.0, lam=0.0, e_loss=1.0)
    on_b, on_a = compute_quote(0.5, q=0.0, sigma=0.4, T_t=5.0, lam=0.5, e_loss=1.0)
    assert (on_a - on_b) > (off_a - off_b)  # lambda ON must widen vs OFF


def test_inventory_skew_direction():
    """Long inventory -> reservation below mid -> quotes shifted DOWN (to shed)."""
    flat_b, flat_a = compute_quote(0.5, q=0.0, sigma=0.4, T_t=5.0)
    long_b, long_a = compute_quote(0.5, q=40.0, sigma=0.4, T_t=5.0)
    flat_mid = (flat_b + flat_a) / 2
    long_mid = (long_b + long_a) / 2
    assert long_mid < flat_mid


def test_directional_jump_skew():
    """Negative jump_drift (jump points toward 0) pushes the reservation/center DOWN."""
    up_b, up_a = compute_quote(0.5, q=0.0, sigma=0.4, T_t=5.0, lam=0.5, jump_drift=+1.0)
    dn_b, dn_a = compute_quote(0.5, q=0.0, sigma=0.4, T_t=5.0, lam=0.5, jump_drift=-1.0)
    assert (dn_b + dn_a) / 2 < (up_b + up_a) / 2


def test_jacobian_compresses_near_boundary():
    """Same logit half-spread maps to a SMALLER price half-spread near 0/1 than at 0.5."""
    mid = price_half_spread_via_jacobian(0.5, 0.3)
    edge = price_half_spread_via_jacobian(0.95, 0.3)
    assert edge < mid


def test_horizon_collapse_guard():
    """As (T-t)->0 the spread must NOT collapse below the T_t=min_horizon spread."""
    P = QuoteParams()
    at_zero = compute_quote(0.5, q=0.0, sigma=0.4, T_t=0.0, params=P)
    at_floor = compute_quote(0.5, q=0.0, sigma=0.4, T_t=P.min_horizon, params=P)
    s0 = at_zero[1] - at_zero[0]
    sf = at_floor[1] - at_floor[0]
    assert math.isclose(s0, sf, rel_tol=1e-9)  # guard clamps T_t up to min_horizon


def test_inventory_cap_tightens_near_boundary():
    assert inventory_cap(0.5, 100.0) > inventory_cap(0.95, 100.0) > 0.0


def test_diffusion_spread_positive():
    assert diffusion_spread_logit(gamma=0.5, sigma=0.4, T_t=5.0, k=5.0) > 0.0

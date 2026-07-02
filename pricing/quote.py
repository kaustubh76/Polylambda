"""
PolyLambda pricing core — Avellaneda-Stoikov in LOG-ODDS space + jump augmentation.

This module is pure math (no data, no I/O) and is fully implemented + unit-tested, because the
A-S formulas are verified correct (see ../DECISIONS.md #8; arXiv 2510.15205, GLFT 1105.3115,
and the original Avellaneda-Stoikov 2008 paper).

Design decisions baked in from the verification (DECISIONS.md):
  * Work in log-odds  X = ln(p/(1-p))  so the diffusion never pushes probability past 0/1.
  * Map quotes back to price space by pushing the logit ENDPOINTS through the sigmoid — this is
    the exact, boundary-safe transform (the local Jacobian dp/dx = p(1-p) is provided separately
    as `price_half_spread_via_jacobian` for intuition/tests).
  * DIRECTIONAL jump term: resolution jumps move toward 0 or 1, so we skew the RESERVATION price
    (not just widen a symmetric spread).
  * Guards: a (T-t)->0 floor (spread must NOT collapse into the resolution/jump window) and an
    inventory cap that tightens near the 0/1 boundary.

Notation (kept distinct on purpose — see DECISIONS.md #8):
    k     = A-S order-arrival / liquidity parameter   (fills fall off faster as k rises)
    kappa = jump-premium weight                        (tuning dial for jump risk)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-6  # keep probabilities strictly inside (0, 1)


# --- log-odds transforms ---------------------------------------------------
def to_logit(p: float) -> float:
    """probability -> log-odds. Clamped to (EPS, 1-EPS)."""
    p = min(max(p, EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def to_prob(x: float) -> float:
    """log-odds -> probability (logistic sigmoid)."""
    return 1.0 / (1.0 + math.exp(-x))


# --- Avellaneda-Stoikov terms (all in LOG-ODDS units) ----------------------
def reservation_logit(x_mid: float, q: float, gamma: float, sigma: float, T_t: float) -> float:
    """Reservation price in logit space:  r = x - q*gamma*sigma^2*(T-t)   (inventory skew)."""
    return x_mid - q * gamma * sigma * sigma * T_t


def diffusion_spread_logit(gamma: float, sigma: float, T_t: float, k: float) -> float:
    """Optimal A-S total spread in logit space:
        delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1 + gamma/k)
    (inventory-risk term + liquidity term). Coefficient on the log term is 2/gamma."""
    return gamma * sigma * sigma * T_t + (2.0 / gamma) * math.log(1.0 + gamma / k)


def jump_premium_logit(kappa: float, lam: float, e_loss: float) -> float:
    """Symmetric jump-risk premium added to the spread: kappa * lambda * E[loss | jump].
    Vanishes when lambda -> 0. (The DIRECTIONAL part is applied to the reservation price.)"""
    return kappa * lam * e_loss


def price_half_spread_via_jacobian(p: float, half_spread_logit: float) -> float:
    """Local linear map of a logit half-spread into PRICE space: delta_p ~= p*(1-p)*delta_x.
    Provided for intuition/tests (shows compression near 0/1). The main quote uses the exact
    endpoint-sigmoid transform instead."""
    return p * (1.0 - p) * half_spread_logit


def inventory_cap(p: float, base_cap: float) -> float:
    """Inventory cap that tightens as p -> 0/1:  |q_max| ~ base_cap * (p(1-p) / 0.25).
    (0.25 = max of p(1-p) at p=0.5, so the cap is `base_cap` mid-range and shrinks toward 0/1.)"""
    return base_cap * (p * (1.0 - p)) / 0.25


@dataclass
class QuoteParams:
    gamma: float = 0.5          # risk aversion
    k: float = 5.0              # A-S liquidity / order-arrival
    kappa: float = 1.0          # jump-premium weight
    min_horizon: float = 0.02   # (T-t)->0 guard: floor on effective horizon (in same units as T_t)
    boundary_floor: float = 0.002   # minimum price-space half-spread near boundaries
    base_inventory_cap: float = 100.0


def compute_quote(
    mid: float,
    q: float,
    sigma: float,
    T_t: float,
    *,
    lam: float = 0.0,
    e_loss: float = 0.0,
    jump_drift: float = 0.0,
    params: QuoteParams | None = None,
) -> tuple[float, float]:
    """Return (bid, ask) in PRICE space (both in (0,1), bid < ask).

    mid        : current fair mid probability in (0,1)
    q          : signed inventory (+long / -short) in outcome tokens
    sigma      : belief-volatility in LOGIT space (from estimators/sigma.py)
    T_t        : time to resolution (T - t), same units as sigma's horizon
    lam        : jump intensity (from estimators/lambda_engine.py, the lambda_jump signal)
    e_loss     : E[loss | jump] in logit units
    jump_drift : expected directional jump move in logit units (>0 pushes p up). Skews r.
    """
    P = params or QuoteParams()

    # (T-t)->0 guard: never let the horizon-dependent terms collapse into the danger window.
    T_eff = max(T_t, P.min_horizon)

    # inventory cap that tightens near the boundary
    cap = inventory_cap(mid, P.base_inventory_cap)
    q_eff = max(-cap, min(cap, q))

    x_mid = to_logit(mid)

    # reservation price: inventory skew (A-S) + DIRECTIONAL jump skew (toward expected jump).
    r_x = reservation_logit(x_mid, q_eff, P.gamma, sigma, T_eff)
    r_x += P.kappa * lam * jump_drift  # directional: lean the center toward where the jump points

    # total spread = diffusion A-S + symmetric jump premium
    delta_x = diffusion_spread_logit(P.gamma, sigma, T_eff, P.k) + jump_premium_logit(P.kappa, lam, e_loss)
    half_x = delta_x / 2.0

    # exact, boundary-safe map: push logit endpoints through the sigmoid
    bid = to_prob(r_x - half_x)
    ask = to_prob(r_x + half_x)

    # enforce a minimum price-space half-spread (protects when p(1-p) compression is extreme)
    r_p = to_prob(r_x)
    if (ask - bid) / 2.0 < P.boundary_floor:
        bid = r_p - P.boundary_floor
        ask = r_p + P.boundary_floor

    # clamp into (EPS, 1-EPS) and keep bid < ask
    bid = min(max(bid, EPS), 1.0 - EPS)
    ask = min(max(ask, EPS), 1.0 - EPS)
    if bid >= ask:
        bid, ask = max(EPS, r_p - EPS), min(1.0 - EPS, r_p + EPS)
    return bid, ask


if __name__ == "__main__":
    # quick manual sanity: political market at p=0.85, flat, 10 "days" out
    b, a = compute_quote(0.85, q=0.0, sigma=0.4, T_t=10.0, lam=0.1, e_loss=1.0, jump_drift=-0.5)
    print(f"p=0.85 flat -> bid={b:.4f} ask={a:.4f} spread={a-b:.4f}")

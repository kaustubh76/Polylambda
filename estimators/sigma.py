"""
sigma — belief-volatility estimator (LOGIT-space, robust EWMA, category x price shrinkage).

Corrected design (see ../DECISIONS.md #9):
  * Compute logit returns of the fill/mid tape; EWMA with memory knob `b` (config: ewma_b).
  * WASH FILTER FIRST: drop self-crosses (maker == taker) and sub-min-size prints. EWMA on wash
    prints measures MANIPULATION, not belief; and spread proportional to sigma^2, so inflated
    sigma => too-wide quotes => under-fill on exactly the reward-paying markets.
  * ROBUST estimator: winsorize returns at k x the median absolute return before EWMA, so a single
    wash spike can't blow up the estimate. Gate on a trade-count floor (min_trades) -> fall back
    to the prior when data is too thin.
  * Hierarchical SHRINKAGE toward a prior conditioned on category AND price level (logit sigma is
    heteroskedastic in price space).

The pure core here is fully unit-tested (tests/test_sigma.py). `fetch_fills` / `estimate_sigma`
are thin I/O wrappers over the HF fill tape (data.fills.fetch_fills_hf) — the indexer no longer
carries a fill tape (its GraphQL Fill entity was pruned as dead), so `graphql_url` is ignored.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# price buckets for the heteroskedastic (category x price-level) prior
PRICE_BUCKETS = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.7), (0.7, 0.9), (0.9, 1.0001)]


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def price_bucket(p: float) -> int:
    for i, (lo, hi) in enumerate(PRICE_BUCKETS):
        if lo <= p < hi:
            return i
    return len(PRICE_BUCKETS) - 1


# --- pure math -------------------------------------------------------------
def logit_returns(prices: Sequence[float]) -> list[float]:
    """Successive differences of log-odds."""
    xs = [_logit(p) for p in prices]
    return [xs[i] - xs[i - 1] for i in range(1, len(xs))]


def ewma_sigma(returns: Sequence[float], b: float) -> float:
    """EWMA volatility (std) of returns: v_t = b*v_{t-1} + (1-b)*r_t^2."""
    if not returns:
        return 0.0
    v = returns[0] ** 2
    for r in returns[1:]:
        v = b * v + (1 - b) * r * r
    return math.sqrt(v)


def robust_ewma_sigma(returns: Sequence[float], b: float, winsor_k: float = 5.0) -> float:
    """EWMA after winsorizing returns at winsor_k x the median absolute return (wash-robust)."""
    if not returns:
        return 0.0
    absr = sorted(abs(r) for r in returns)
    med = absr[len(absr) // 2]
    if med > 0:
        cap = winsor_k * med
        returns = [max(-cap, min(cap, r)) for r in returns]
    return ewma_sigma(returns, b)


def shrink(sigma_market: float, sigma_prior: float, n_obs: int, strength: float) -> float:
    """James-Stein-style shrinkage. `strength` = prior pseudo-count:
        w = n / (n + strength);  sigma = w*market + (1-w)*prior.
    Thin markets (small n) get pulled toward the prior; data-rich markets trust themselves."""
    w = n_obs / (n_obs + max(strength, 0.0)) if (n_obs + max(strength, 0.0)) > 0 else 0.0
    return w * sigma_market + (1 - w) * sigma_prior


# --- wash / quality filter -------------------------------------------------
def wash_filter(fills: Iterable[dict], min_size: float) -> list[dict]:
    """Drop self-crosses (maker == taker), sub-min-size prints, and out-of-range prices."""
    out = []
    for f in fills:
        maker = (f.get("maker") or "").lower()
        taker = (f.get("taker") or "").lower()
        if maker and taker and maker == taker:
            continue
        if float(f.get("size", 0)) < min_size:
            continue
        p = float(f.get("price", 0))
        if not (0.0 < p < 1.0):
            continue
        out.append(f)
    return out


# --- prior (category x price level) ---------------------------------------
def category_price_prior(
    observations: Sequence[dict], category: str, price_level: float, default: float = 0.5
) -> float:
    """Mean sigma across markets in the same (category, price-bucket); fall back to category, then
    global, then `default`. `observations` = [{category, price, sigma}, ...]."""
    b = price_bucket(price_level)
    same_bucket = [o["sigma"] for o in observations if o["category"] == category and price_bucket(o["price"]) == b]
    if same_bucket:
        return sum(same_bucket) / len(same_bucket)
    same_cat = [o["sigma"] for o in observations if o["category"] == category]
    if same_cat:
        return sum(same_cat) / len(same_cat)
    allv = [o["sigma"] for o in observations]
    return sum(allv) / len(allv) if allv else default


# --- orchestration (pure, on in-memory fills) ------------------------------
def estimate_sigma_from_fills(
    fills: Sequence[dict],
    *,
    prior: float,
    b: float = 0.94,
    min_size: float = 1.0,
    min_trades: int = 20,
    strength: float = 20.0,
    winsor_k: float = 5.0,
) -> float:
    """Full pipeline: wash_filter -> logit_returns -> robust EWMA -> shrink toward prior.
    Returns the prior (fallback) when the cleaned tape is thinner than min_trades."""
    clean = wash_filter(fills, min_size)
    if len(clean) < min_trades:
        return prior
    rets = logit_returns([float(f["price"]) for f in clean])
    if not rets:
        return prior
    sig = robust_ewma_sigma(rets, b, winsor_k)
    return shrink(sig, prior, len(rets), strength)


# --- I/O wrapper (thin) ---
def fetch_fills(graphql_url: str, condition_id: str, limit: int = 1000) -> list[dict]:
    """Pull a market's fill tape from the HF dataset via DuckDB (data.fills.fetch_fills_hf) —
    YES-normalized, year-pruned, back to 2022; needs no running indexer.

    `graphql_url` is accepted for call-site compatibility but IGNORED: the scoped indexer no longer
    indexes CLOB fills (the CTFExchange handlers and the dead Fill/TokenMap entities were removed —
    the HF tape is strictly more complete). Returns the {price,size,side,maker,taker,timestamp}
    dicts the pure core consumes."""
    from data.fills import fetch_fills_hf

    return fetch_fills_hf(condition_id, limit=limit)


def estimate_sigma(graphql_url: str, condition_id: str, category: str, prior: float, **cfg) -> float:
    """Fetch the tape and estimate sigma. `prior` from category_price_prior(...); `cfg` overrides
    b / min_size / min_trades / strength / winsor_k."""
    fills = fetch_fills(graphql_url, condition_id)
    return estimate_sigma_from_fills(fills, prior=prior, **cfg)

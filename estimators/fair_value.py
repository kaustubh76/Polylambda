"""
fair_value — model mid (depth-weighted book mid + light, tapered favorite-longshot tilt).

Corrected design (see ../DECISIONS.md):
  * Depth-weighted mid (NOT last trade — wash-prone).
  * Light favorite-longshot tilt: favorites (p>0.5) are mildly underpriced, longshots (p<0.5)
    mildly overpriced -> nudge toward the extreme, SMALL magnitude, and TAPER to ~0 near
    resolution (short horizon = less structural mispricing to harvest, more jump risk).
  * NO LOOKAHEAD, EVER — uses only the current book.
  * Output is the CENTER `mid` fed into pricing/quote.py.
"""
from __future__ import annotations

EPS = 1e-6


def depth_weighted_mid(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> float:
    """Size-weighted mid from book levels [(price, size), ...]."""
    def side_vwap(levels: list[tuple[float, float]]) -> float:
        tot = sum(s for _, s in levels)
        if tot <= 0:
            return levels[0][0] if levels else 0.0
        return sum(p * s for p, s in levels) / tot

    if not bids or not asks:
        raise ValueError("need both sides of the book for a depth-weighted mid")
    return 0.5 * (side_vwap(bids) + side_vwap(asks))


def favorite_longshot_tilt(
    mid: float, T_t: float, *, strength: float = 0.02, taper_horizon: float = 10.0
) -> float:
    """Signed tilt to ADD to the mid: strength * (mid - 0.5) * min(1, T_t/taper_horizon).
    Positive for favorites (mid>0.5), negative for longshots; vanishes as T_t -> 0."""
    horizon_factor = min(1.0, max(0.0, T_t) / taper_horizon) if taper_horizon > 0 else 0.0
    return strength * (mid - 0.5) * horizon_factor


def estimate_fair_value(
    book: dict, T_t: float, *, strength: float = 0.02, taper_horizon: float = 10.0
) -> float:
    """Depth-weighted mid + tapered favorite-longshot tilt, clamped to (0,1). Point-in-time only.
    `book` = {"bids": [(p,s),...], "asks": [(p,s),...]}."""
    mid = depth_weighted_mid(book["bids"], book["asks"])
    fair = mid + favorite_longshot_tilt(mid, T_t, strength=strength, taper_horizon=taper_horizon)
    return min(max(fair, EPS), 1.0 - EPS)

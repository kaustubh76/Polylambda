"""
clob — Polymarket CLOB V2 wrapper (read book, place/cancel maker orders).

Corrected design (see ../DECISIONS.md #4-#7):
  * Use the official Polymarket py-sdk (CLOB V2). ⚠ NOT py-clob-client (archived / V1 / dead).
  * Auth: L1 EIP-712 wallet sig derives creds; L2 HMAC for order ops.
  * Orders: GTC/GTD, POST-ONLY for guaranteed maker status; batch order/cancel; rate-aware backoff.
  * Order uniqueness = MILLISECOND TIMESTAMP (NO nonce logic).
  * Collateral is pUSD — wrap USDC.e via the CollateralOnramp before trading.
  * Attach a Builder Code (bytes32) for reward attribution.
  * Per-market tick_size / min_order_size are DYNAMIC — read at runtime; handle INVALID_TICK.
  * Book reads (REST/WS) need NO auth -> used by paper-live.
"""
from __future__ import annotations


def read_book(token_id: str) -> dict:
    """TODO (no auth): fetch REST /book or subscribe WS market feed. Return {bids, asks}."""
    raise NotImplementedError("read_book: public REST/WS book snapshot (no auth needed)")


def get_market_microstructure(token_id: str) -> dict:
    """TODO: fetch tick_size, min_order_size, max_incentive_spread, min reward size, fee category."""
    raise NotImplementedError("get_market_microstructure: read per-market dynamic params at runtime")


def wrap_usdce_to_pusd(amount: float) -> str:
    """TODO: wrap USDC.e -> pUSD via CollateralOnramp (0x93070a847efEf7F70739046A929D47a521F5B8ee)."""
    raise NotImplementedError("wrap_usdce_to_pusd: pUSD on-ramp before trading")


def place_order(token_id: str, side: str, price: float, size: float, *, post_only: bool = True,
                builder_code: str | None = None) -> str:
    """TODO: post-only maker order via py-sdk (GTC). ms-timestamp uniqueness; attach builder_code."""
    raise NotImplementedError("place_order: py-sdk post-only maker (CLOB V2)")


def cancel_orders(order_ids: list[str]) -> None:
    """TODO: batch DELETE /orders (rate-aware)."""
    raise NotImplementedError("cancel_orders: batch cancel with backoff")

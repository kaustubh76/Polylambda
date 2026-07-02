"""
Parity oracle: the DuckDB deriveFill SQL (data/fills.py DERIVE_FILL_SQL) must reproduce the
TypeScript indexer/src/lib.ts:deriveFill EXACTLY. These vectors mirror indexer/test/lib.test.ts.

Offline + deterministic (no network): builds a synthetic order_filled-shaped table (all VARCHAR,
like the real dataset) and applies the projection. Skipped only if duckdb isn't installed.
"""
import pytest

duckdb = pytest.importorskip("duckdb")

from data.fills import DERIVE_FILL_SQL


def _derive(maker_asset, taker_asset, maker_amt, taker_amt):
    """Run DERIVE_FILL_SQL on one synthetic row; return (is_buy, tok, price, size, side)."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE o AS SELECT ?::VARCHAR AS makerAssetId, ?::VARCHAR AS takerAssetId, "
        "?::VARCHAR AS makerAmountFilled, ?::VARCHAR AS takerAmountFilled",
        [str(maker_asset), str(taker_asset), str(maker_amt), str(taker_amt)],
    )
    return con.execute(f"SELECT {DERIVE_FILL_SQL} FROM o").fetchone()


def test_buy_maker_pays_collateral():
    # 60 USDC (6dp) for 100 outcome tokens -> price 0.6, size 100, BUY, tok = takerAssetId
    is_buy, tok, price, size, side = _derive(0, 12345, 60_000_000, 100_000_000)
    assert is_buy is True
    assert tok == "12345"
    assert price == pytest.approx(0.6, abs=1e-9)
    assert size == pytest.approx(100.0, abs=1e-9)
    assert side == "BUY"


def test_sell_maker_gives_tokens():
    # mirrors lib.test.ts: deriveFill(12345, 0, 100_000_000, 40_000_000) -> price 0.4, size 100, SELL
    is_buy, tok, price, size, side = _derive(12345, 0, 100_000_000, 40_000_000)
    assert is_buy is False
    assert tok == "12345"
    assert price == pytest.approx(0.4, abs=1e-9)
    assert size == pytest.approx(100.0, abs=1e-9)
    assert side == "SELL"


def test_guards_divide_by_zero():
    # deriveFill(0, 1, 5, 0).price === 0
    _is_buy, _tok, price, _size, _side = _derive(0, 1, 5, 0)
    assert price == 0.0


def test_prices_are_probabilities_on_real_shaped_input():
    # a spread of realistic (collateral, outcome) pairs must all land in (0,1)
    for coll, out in [(1_263_600, 2_430_000), (4_000_000, 50_000_000), (530_000_000, 1_000_000_000)]:
        _is_buy, _tok, price, _size, _side = _derive(0, 999, coll, out)
        assert 0.0 < price < 1.0

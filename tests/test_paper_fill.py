"""execution/paper.py — determinism of the synthetic sim + the conservative queue model."""
import pytest

from execution.paper import ConservativeFillModel, PaperClob, SimOrder


def _run_session(seed):
    clob = PaperClob(["SIM-A", "SIM-B"], seed=seed)
    events = []
    clob.place("SIM-A", "BUY", 0.48, 50.0, now_ts=0)
    clob.place("SIM-A", "SELL", 0.52, 50.0, now_ts=0)
    for t in range(30):
        for f in clob.step(float(t)):
            events.append((f["order_id"], f["side"], f["price"], round(f["size"], 4), f["timestamp"]))
    return events


def test_same_seed_identical_fill_sequence():
    assert _run_session(7) == _run_session(7)


def test_different_seed_differs():
    # 30 steps of random taker flow — overwhelmingly unlikely to coincide
    assert _run_session(7) != _run_session(8)


def test_paper_clob_never_imports_write_path():
    clob = PaperClob(["SIM-A"])
    book = clob.get_book("SIM-A")
    assert book["bids"][0][0] < book["asks"][0][0]           # sane book, no network involved
    assert clob.get_micro("SIM-A")["tick_size"] == 0.01


# --- conservative queue model ------------------------------------------------------------------

def test_no_fill_while_queue_ahead_absorbs_prints():
    m = ConservativeFillModel()
    o = SimOrder("o1", "T", "BUY", 0.50, 10.0, queue_ahead=100.0)
    qty = m.fills_for(o, [{"price": 0.50, "size": 60.0}])     # at-price volume < queue
    assert qty == 0.0 and o.queue_ahead == pytest.approx(40.0)


def test_fill_exactly_beyond_queue():
    m = ConservativeFillModel()
    o = SimOrder("o1", "T", "BUY", 0.50, 10.0, queue_ahead=100.0)
    qty = m.fills_for(o, [{"price": 0.50, "size": 105.0}])    # 5 beyond the queue
    assert qty == pytest.approx(5.0) and o.size == pytest.approx(5.0)


def test_print_through_counts_toward_queue_drain():
    m = ConservativeFillModel()
    o = SimOrder("o1", "T", "BUY", 0.50, 10.0, queue_ahead=50.0)
    qty = m.fills_for(o, [{"price": 0.49, "size": 70.0}])     # strictly through our price
    assert qty == pytest.approx(10.0)                          # queue drained, order fully filled


def test_sell_side_mirrored():
    m = ConservativeFillModel()
    o = SimOrder("o1", "T", "SELL", 0.50, 10.0, queue_ahead=20.0)
    assert m.fills_for(o, [{"price": 0.49, "size": 100.0}]) == 0.0   # below our ask: irrelevant
    assert m.fills_for(o, [{"price": 0.51, "size": 25.0}]) == pytest.approx(5.0)


def test_queue_at_sums_price_or_better():
    m = ConservativeFillModel()
    book = {"bids": [(0.50, 100.0), (0.49, 50.0)], "asks": [(0.51, 80.0), (0.52, 40.0)]}
    assert m.queue_at(book, "BUY", 0.50) == pytest.approx(100.0)     # only >= our bid price
    assert m.queue_at(book, "BUY", 0.49) == pytest.approx(150.0)
    assert m.queue_at(book, "SELL", 0.52) == pytest.approx(120.0)    # only <= our ask price

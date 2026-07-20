"""execution/risk.py — RiskGovernor fault injection: every halt path + ledger persistence."""
from execution.risk import RiskGovernor, RiskLimits


def _gov(tmp_path, clock=lambda: 1_784_000_000.0, **kw):
    limits = RiskLimits(kill_switch_path=str(tmp_path / "KILL"), **kw)
    return RiskGovernor(limits, ledger_dir=str(tmp_path / "risk"), clock=clock)


def test_allows_by_default(tmp_path):
    ok, reason = _gov(tmp_path).allow_tx("postQuote", market="0xm")
    assert ok and reason == ""


def test_kill_switch_blocks_within_one_call_and_unkill_restores(tmp_path):
    g = _gov(tmp_path)
    g.kill("test")
    ok, reason = g.allow_tx("postQuote")
    assert not ok and "kill" in reason
    assert g.status()["killed"] is True
    assert g.unkill() is True
    assert g.allow_tx("postQuote")[0] is True


def test_daily_loss_trip(tmp_path):
    g = _gov(tmp_path, max_daily_loss_usd=5.0)
    g.mark_equity(100.0)
    g.mark_equity(96.0)
    assert g.allow_tx("postQuote")[0] is True   # loss 4 < 5
    g.mark_equity(94.0)
    ok, reason = g.allow_tx("postQuote")
    assert not ok and "loss" in reason


def test_tx_and_gas_budgets(tmp_path):
    g = _gov(tmp_path, max_tx_per_day=2, max_gas_pol_per_day=100.0)
    g.record_tx("postQuote", "0xaa", 0.001)
    g.record_tx("postQuote", "0xbb", 0.001)
    ok, reason = g.allow_tx("postQuote")
    assert not ok and "tx budget" in reason

    g2 = _gov(tmp_path, max_gas_pol_per_day=0.001)
    g2.record_tx("postQuote", "0xcc", 0.002)
    ok, reason = g2.allow_tx("postQuote")
    assert not ok and "gas budget" in reason


def test_error_breaker_opens_and_closes(tmp_path):
    g = _gov(tmp_path, max_consecutive_errors=3)
    for _ in range(3):
        g.record_error("rpc timeout")
    ok, reason = g.allow_tx("postQuote")
    assert not ok and "breaker" in reason
    g.record_success()
    assert g.allow_tx("postQuote")[0] is True


def test_gross_cap(tmp_path):
    g = _gov(tmp_path, portfolio_gross_cap=1.0)
    g.record_fill("0xm1", "SELL", 0.6, 0.8)   # inventory -0.8
    g.record_fill("0xm2", "BUY", 0.4, 0.7)    # inventory +0.7 -> gross 1.5
    ok, reason = g.allow_tx("postQuote")
    assert not ok and "gross" in reason
    assert g.gross_exposure() == 1.5


def test_ledger_survives_restart(tmp_path):
    g = _gov(tmp_path, max_tx_per_day=3)
    g.record_tx("postQuote", "0xaa", 0.01)
    g.mark_equity(50.0)
    g.mark_equity(40.0)
    # new instance, same ledger dir + same clock day -> counters replayed
    g2 = _gov(tmp_path, max_tx_per_day=3)
    st = g2.status()
    assert st["tx_count"] == 1
    assert st["gas_pol"] == 0.01
    assert st["daily_loss_usd"] == 10.0


def test_day_roll_resets_budgets(tmp_path):
    now = [1_784_000_000.0]
    g = _gov(tmp_path, clock=lambda: now[0], max_tx_per_day=1)
    g.record_tx("postQuote", "0xaa", 0.01)
    assert g.allow_tx("postQuote")[0] is False
    now[0] += 86_400  # next UTC day
    assert g.allow_tx("postQuote")[0] is True
    assert g.status()["tx_count"] == 0

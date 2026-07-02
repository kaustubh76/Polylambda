"""
Offline unit tests for the data layer's pure logic + the rewired estimators (no network / no DuckDB
scan). These guard the parts that don't need the HF dataset: category derivation, Wilson CI, the
base-rate two-source join, and the replay's pure math.
"""
import importlib

import pytest


def test_all_new_modules_import():
    # syntax + import smoke for everything added/rewired (lazy imports keep these network-free)
    for mod in ("data.hf", "data.fills", "data.conditions", "data.metadata", "data.base_rates",
                "data.cache", "data.dossier", "data.prior_corpus",
                "estimators.lambda_engine", "recon.check", "forwardtest.replay_ablation"):
        importlib.import_module(mod)


def test_derive_category_keywords():
    from data.metadata import derive_category
    assert derive_category("will-bitcoin-hit-100k") == "crypto"
    assert derive_category("will-trump-win-the-2024-election") == "politics"
    assert derive_category("lakers-vs-celtics-game-7") == "sports"
    assert derive_category("some-random-thing") == "other"


def test_wilson_interval_bounds():
    from data.base_rates import _wilson
    p, lo, hi = _wilson(0, 100)          # zero disputes → point 0, but upper CI > 0 (honest)
    assert p == 0.0 and lo == 0.0 and hi > 0.0
    p, lo, hi = _wilson(5, 100)
    assert lo < p < hi and 0.0 <= lo and hi <= 1.0


def test_category_base_rate_two_source_join():
    from data.base_rates import category_base_rate
    counts = {"politics": {"n_markets": 1000, "n_resolved": 900}}
    out = category_base_rate("politics", {"politics": 9}, counts)   # 9 disputes / 900 resolved = 1%
    assert out["resolved"] == 900 and out["disputes"] == 9
    assert out["rate"] == pytest.approx(0.01, abs=1e-9)
    assert out["ci_low"] < 0.01 < out["ci_high"]                    # sparse-data CI straddles the point


def test_estimate_lambda_v1_base_rate_with_ci():
    # v1 (no hazard model): lambda_select == base rate, CI carried through, directional jump signed
    from estimators.lambda_engine import estimate_lambda
    counts = {"politics": {"n_markets": 1000, "n_resolved": 900}}
    import data.base_rates as br
    orig = br.category_counts_hf
    br.category_counts_hf = lambda: counts                          # avoid network
    try:
        out = estimate_lambda("0xabc", {"category": "politics", "price": 0.85},
                              dispute_counts={"politics": 9})
    finally:
        br.category_counts_hf = orig
    assert out.lambda_select == pytest.approx(0.01, abs=1e-9)
    assert out.ci_high > out.ci_low
    assert out.jump_drift > 0                                       # p=0.85 (favorite) → jump toward YES


def test_replay_pure_math():
    from forwardtest.replay_ablation import power_calc, _sharpe, _realized_jump_logit, _reward_proxy
    assert power_calc(1000, 0.01, 0.5) == pytest.approx(5.0)
    assert _sharpe([1.0, 1.0, 1.0]) == 0.0                          # zero variance
    assert _sharpe([1.0, 2.0, 3.0]) > 0
    fills = [{"timestamp": 10, "price": 0.5, "size": 100.0},
             {"timestamp": 30, "price": 0.9, "size": 100.0}]
    assert _realized_jump_logit(fills, 20) > 0                      # price jumped up across the event
    assert _reward_proxy(fills) > 0


def test_jump_drift_zero_at_neutral_price():
    # copysign(x, 0.0) returns +x; a neutral price (p=0.5, logit 0) must yield ZERO directional drift
    from estimators.lambda_engine import estimate_lambda
    import data.base_rates as br

    orig = br.category_counts_hf
    br.category_counts_hf = lambda: {"politics": {"n_markets": 1000, "n_resolved": 900}}
    try:
        out = estimate_lambda("0xabc", {"category": "politics", "price": 0.5}, dispute_counts={"politics": 9})
    finally:
        br.category_counts_hf = orig
    assert out.jump_drift == 0.0                                    # neutral → no YES/NO bias
    assert out.e_loss > 0.0                                         # magnitude still positive


def test_replay_market_arm_logic_uses_category_signal():
    # arm C (lambda_select) must key off the CATEGORY dispute rate, and controls must be handled right
    from forwardtest.replay_ablation import _replay_market
    fills = [{"timestamp": 10, "price": 0.5, "size": 1000.0},
             {"timestamp": 30, "price": 0.6, "size": 1000.0}]
    # CONTROL (disputeTs None), signal FIRES (0.01 > 0.001): C blanket-avoids → 0; B has no jump → == A
    fired = _replay_market("c", fills, None, lambda_star=0.001, lambda_select=0.01)
    assert fired["pnl_C"] == 0.0
    assert fired["pnl_A"] == fired["pnl_B"] and fired["pnl_A"] > 0
    # CONTROL, signal does NOT fire (0.01 < 0.05): all arms keep the safe market → equal, C not penalized
    kept = _replay_market("c", fills, None, lambda_star=0.05, lambda_select=0.01)
    assert kept["pnl_A"] == kept["pnl_B"] == kept["pnl_C"] > 0

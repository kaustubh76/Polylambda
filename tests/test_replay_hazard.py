"""forwardtest/replay_ablation.py — the structural-hazard exit arm (lambda_jump_hazard) runs the
IDENTICAL reward-aware surgical exit as arm B but off the per-market hazard λ, so the two are
directly comparable. Pure/offline (drives _replay_market with hand-set λ values)."""
from forwardtest.replay_ablation import _replay_market

# a disputed market: price jumps 0.5 → 0.05 across the dispute; reward accrues from the in-band prints
DISPUTED = [{"price": 0.5, "size": 100, "timestamp": 10, "maker": "a", "taker": "b"},
            {"price": 0.5, "size": 100, "timestamp": 20, "maker": "a", "taker": "b"},
            {"price": 0.05, "size": 100, "timestamp": 40, "maker": "a", "taker": "b"}]
CONTROL = [{"price": 0.5, "size": 100, "timestamp": 10, "maker": "a", "taker": "b"},
           {"price": 0.5, "size": 100, "timestamp": 20, "maker": "a", "taker": "b"}]
DISPUTE_TS = 30


def test_hazard_arm_is_additive_and_absent_without_a_model():
    m = _replay_market("0x", DISPUTED, DISPUTE_TS, lambda_star=0.01, lambda_select=0.02)
    assert "pnl_B" in m and "pnl_Bh" not in m        # no lambda_hazard → 3-arm behavior unchanged


def test_hazard_arm_follows_its_own_lambda_not_the_base_rate():
    ls = 0.01
    # base rate fires (0.02 > λ*) so arm B exits; hazard below λ* (0.005) so the hazard arm HOLDS
    m = _replay_market("0x", DISPUTED, DISPUTE_TS, ls, lambda_select=0.02, lambda_hazard=0.005)
    assert m["avoided_B"] > 0.0                       # B avoided the jump
    assert m["avoided_Bh"] == 0.0                     # hazard arm did not exit (its λ below threshold)
    assert m["pnl_Bh"] != m["pnl_B"]                  # the arms genuinely diverge on λ

    # reverse: hazard fires, base rate does not → only the hazard arm exits
    m2 = _replay_market("0x", DISPUTED, DISPUTE_TS, ls, lambda_select=0.005, lambda_hazard=0.02)
    assert m2["avoided_B"] == 0.0 and m2["avoided_Bh"] > 0.0


def test_hazard_arm_never_exits_a_control():
    # no dispute → no jump_loss → the reward-aware gate blocks the exit even with a high λ (parity w/ B)
    m = _replay_market("0x", CONTROL, None, lambda_star=0.001, lambda_select=0.02, lambda_hazard=0.02)
    assert m["avoided_Bh"] == 0.0 and m["forgone_Bh"] == 0.0
    assert m["pnl_Bh"] == m["pnl_B"]                  # identical on a control


def test_hazard_arm_matches_B_when_lambdas_equal():
    m = _replay_market("0x", DISPUTED, DISPUTE_TS, 0.01, lambda_select=0.02, lambda_hazard=0.02)
    assert m["pnl_Bh"] == m["pnl_B"] and m["avoided_Bh"] == m["avoided_B"]

"""scripts/preflight_golive.py — the go-live readiness evaluators.

Offline + deterministic: exercises the PURE evaluators (no network, no chain, no signing) so the
PASS/FAIL verdict — and therefore the process exit code the operator relies on — is proven correct
"without fail". The IO shells (RPC reads / the /api/testnet/keeper GET) just feed these.
"""
import os
import sys

sys.path.insert(0, "scripts")
import preflight_golive as pf  # noqa: E402

FLOOR = 0.02


# --- remote: GET /api/testnet/keeper -> live go-live gates ------------------------------------
def _ready_status() -> dict:
    return {"running": True, "ticks_done": 42, "autostart": True, "engine_ready": True,
            "n_markets": 6, "last_error": "",
            "engine": {"address": "0xFc46DA4cbAbDca9f903863De571E03A39D9079aD", "pol": 1.0, "usdc": 5.0},
            "risk": {"halted": False, "halt_reason": ""}}


def test_remote_ready_passes():
    ok, checks = pf.evaluate_remote(_ready_status(), FLOOR)
    assert ok is True
    assert all(c[1] is not False for c in checks)


def test_remote_autostart_off_fails():
    st = _ready_status(); st["autostart"] = False
    ok, checks = pf.evaluate_remote(st, FLOOR)
    assert ok is False
    assert any(c[0] == "autostart" and c[1] is False for c in checks)


def test_remote_low_pol_fails():
    st = _ready_status(); st["engine"]["pol"] = 0.001
    ok, checks = pf.evaluate_remote(st, FLOOR)
    assert ok is False
    assert any(c[0] == "engine POL >= floor" and c[1] is False for c in checks)


def test_remote_no_markets_and_not_running_fails():
    st = _ready_status(); st["n_markets"] = 0; st["running"] = False
    ok, checks = pf.evaluate_remote(st, FLOOR)
    assert ok is False
    assert any(c[0] == "markets loaded" and c[1] is False for c in checks)
    assert any(c[0] == "running" and c[1] is False for c in checks)


def test_remote_halted_fails_and_surfaces_reason():
    st = _ready_status()
    st["risk"] = {"halted": True, "halt_reason": "engine out of gas (0.0100 POL < 0.02 floor)"}
    ok, checks = pf.evaluate_remote(st, FLOOR)
    assert ok is False
    halt = next(c for c in checks if c[0] == "risk not halted")
    assert halt[1] is False and "out of gas" in halt[2]


def test_remote_missing_engine_balance_fails_gracefully():
    st = _ready_status(); st["engine"] = {}          # no pol reported
    ok, checks = pf.evaluate_remote(st, FLOOR)
    assert ok is False                               # unknown balance is not "ready"


# --- local: wallet/chain/fleet facts -> PASS/FAIL ---------------------------------------------
def _healthy_local() -> dict:
    return dict(chain_id=80002, head_ok=True, engine_addr="0xFc46DA4cbAbDca9f903863De571E03A39D9079aD",
                pol=1.0, usdc=5.0, n_managed=6, kill_present=False, floor=FLOOR,
                autostart=True, interval="60")


def test_local_healthy_passes():
    ok, _ = pf.evaluate_local(**_healthy_local())
    assert ok is True


def test_local_autostart_hint_is_advisory_not_fatal():
    # KEEPER_AUTOSTART unset locally must NOT fail the local run (Render is the source of truth).
    args = _healthy_local(); args["autostart"] = False
    ok, checks = pf.evaluate_local(**args)
    assert ok is True
    hint = next(c for c in checks if c[0] == "KEEPER_AUTOSTART")
    assert hint[1] is None                           # WARN, not FAIL


def test_local_low_pol_fails():
    args = _healthy_local(); args["pol"] = 0.0
    ok, checks = pf.evaluate_local(**args)
    assert ok is False
    assert any(c[0] == "engine POL >= floor" and c[1] is False for c in checks)


def test_local_wrong_chain_fails():
    args = _healthy_local(); args["chain_id"] = 137
    ok, checks = pf.evaluate_local(**args)
    assert ok is False
    assert any(c[0] == "chain is Amoy" and c[1] is False for c in checks)


def test_local_no_managed_market_fails():
    args = _healthy_local(); args["n_managed"] = 0
    ok, checks = pf.evaluate_local(**args)
    assert ok is False
    assert any(c[0] == "keeper_managed fleet" and c[1] is False for c in checks)


def test_local_unreachable_rpc_fails_without_crashing():
    # RPC down: chain_id/pol come through as None — the evaluator FAILs cleanly, never raises.
    args = _healthy_local()
    args.update(chain_id=None, head_ok=False, pol=None, usdc=None)
    ok, checks = pf.evaluate_local(**args)
    assert ok is False
    assert any(c[0] == "rpc reachable" and c[1] is False for c in checks)


def test_local_zero_usdc_is_warn_not_fatal():
    args = _healthy_local(); args["usdc"] = 0.0
    ok, checks = pf.evaluate_local(**args)
    assert ok is True                                # collateral is advisory
    usdc = next(c for c in checks if c[0] == "engine USDC")
    assert usdc[1] is None


def test_local_kill_switch_present_fails():
    args = _healthy_local(); args["kill_present"] = True
    ok, checks = pf.evaluate_local(**args)
    assert ok is False
    assert any(c[0] == "kill-switch clear" and c[1] is False for c in checks)

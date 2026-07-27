"""Go-live preflight — read-only readiness checker for the testnet signing engine.

Verifies every gate that must hold for the keeper to actually sign on Amoy, and prints a
smoke_reconcile-style PASS/FAIL with a real exit code (0 = go, 1 = not yet). It NEVER signs and
NEVER prints the private key — only the derived engine address.

Two modes:
  * local (default): checks the real wallet/chain from this machine's env + .env + Amoy RPC —
    RPC liveness, chain_id==80002, ENGINE_PRIVATE_KEY present, wallet POL >= min_pol_balance floor,
    a keeper_managed fleet, and no kill-switch. KEEPER_AUTOSTART/INTERVAL are reported as hints
    (the running Render service is the source of truth for those).
  * --remote <base_url>: GETs {base}/api/testnet/keeper and checks the ACTUAL deployed service —
    autostart, engine_ready, running, n_markets, engine POL, and that the risk governor isn't halted.
    This is the check that answers "is the live engine ready?" without shell access.

    .venv/bin/python scripts/preflight_golive.py
    .venv/bin/python scripts/preflight_golive.py --remote https://polylambda-9lu2.onrender.com

See notes/14-go-live-runbook.md for the operator steps this checker gates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# a single check result: (label, ok, detail). `ok=None` == advisory/WARN (doesn't fail the run).
Check = tuple


def _fmt(checks: list) -> str:
    lines = []
    for label, ok, detail in checks:
        tag = "OK  " if ok is True else ("WARN" if ok is None else "FAIL")
        lines.append(f"  [{tag}] {label:<22} {detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# pure evaluators — no network/chain; unit-tested in tests/test_preflight.py
# ---------------------------------------------------------------------------------------------
def evaluate_local(*, chain_id: int | None, head_ok: bool, engine_addr: str | None,
                   pol: float | None, usdc: float | None, n_managed: int, kill_present: bool,
                   floor: float, autostart: bool, interval: str | None) -> tuple[bool, list]:
    """Fold the locally-observed wallet/chain facts into PASS/FAIL checks. `pol`/`usdc`/`chain_id`
    may be None when the RPC was unreachable (those checks then FAIL, never crash)."""
    checks: list = []
    checks.append(("rpc reachable", bool(head_ok), "Amoy RPC head block read" if head_ok
                   else "could not read head block — check AMOY_RPC_URL"))
    checks.append(("chain is Amoy", chain_id == 80002,
                   f"chain_id={chain_id} (need 80002)"))
    checks.append(("engine key present", bool(engine_addr),
                   f"ENGINE_PRIVATE_KEY resolved -> {engine_addr}" if engine_addr
                   else "ENGINE_PRIVATE_KEY missing (shell env or .env)"))
    pol_ok = pol is not None and pol >= floor
    checks.append(("engine POL >= floor", pol_ok,
                   f"{pol:.4f} POL (floor {floor}) — fund via Amoy faucet" if pol is not None and not pol_ok
                   else (f"{pol:.4f} POL (floor {floor})" if pol is not None else "balance unread")))
    # collateral is advisory: the engine can post quotes but needs test-USDC to take/settle.
    checks.append(("engine USDC", None if (usdc or 0) <= 0 else True,
                   f"{usdc:.4f} USDC" + ("  (0 — quoting needs collateral)" if (usdc or 0) <= 0 else "")
                   if usdc is not None else "balance unread"))
    checks.append(("keeper_managed fleet", n_managed >= 1,
                   f"{n_managed} managed market(s)" if n_managed >= 1
                   else "0 managed — run scripts/deploy_fleet.py"))
    checks.append(("kill-switch clear", not kill_present,
                   "no KILL file" if not kill_present else "KILL file present — signing halted"))
    # env hints — advisory locally; Render's running service is authoritative (use --remote).
    checks.append(("KEEPER_AUTOSTART", None,
                   ("=1 in this env" if autostart else "unset here — must be =1 on the Render service")))
    checks.append(("KEEPER_INTERVAL_S", None, f"{interval or '60 (default)'}s"))
    ok = all(c[1] is not False for c in checks)   # WARN (None) does not fail the run
    return ok, checks


def evaluate_remote(status: dict, floor: float) -> tuple[bool, list]:
    """Fold a GET /api/testnet/keeper payload into the live go-live gates."""
    engine = status.get("engine") or {}
    risk = status.get("risk") or {}
    pol = engine.get("pol")
    n_markets = status.get("n_markets", 0)
    halted = bool(risk.get("halted"))
    checks: list = [
        ("autostart", bool(status.get("autostart")),
         "KEEPER_AUTOSTART=1 on the service" if status.get("autostart")
         else "not set on the running service (set it in the Render dashboard)"),
        ("engine_ready", bool(status.get("engine_ready")),
         "ENGINE_PRIVATE_KEY present" if status.get("engine_ready")
         else "ENGINE_PRIVATE_KEY missing (set the Render secret)"),
        ("running", bool(status.get("running")),
         f"keeper thread alive · ticks_done={status.get('ticks_done', 0)}" if status.get("running")
         else "keeper thread not running"),
        ("markets loaded", n_markets >= 1, f"n_markets={n_markets}"),
        ("engine POL >= floor", pol is not None and pol >= floor,
         (f"{pol:.4f} POL (floor {floor})" if pol is not None else "engine balance not reported")),
        ("risk not halted", not halted,
         "governor active" if not halted else f"HALTED — {risk.get('halt_reason', 'see /api/testnet/keeper')}"),
    ]
    last_err = status.get("last_error")
    if last_err:
        checks.append(("last_error", None, str(last_err)))
    ok = all(c[1] is not False for c in checks)
    return ok, checks


# ---------------------------------------------------------------------------------------------
# IO shells — gather the facts, then delegate to the pure evaluators above
# ---------------------------------------------------------------------------------------------
def _http_get_json(url: str, *, attempts: int = 4, timeout: float = 20.0) -> dict:
    """GET JSON with a short retry so a free-tier cold start (502/timeout) doesn't read as failure."""
    import time as _time
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last = e
            if i < attempts - 1:
                print(f"  … {url} not ready ({type(e).__name__}) — waking, retry {i + 1}/{attempts - 1}")
                _time.sleep(4.0)
    raise SystemExit(f"\nGO-LIVE PREFLIGHT FAIL — could not reach {url}: {last}")


def run_remote(base: str, floor: float) -> int:
    base = base.rstrip("/")
    status = _http_get_json(f"{base}/api/testnet/keeper")
    engine = status.get("engine") or {}
    addr = engine.get("address", "?")
    print(f"remote  {base}")
    print(f"engine  {addr} | POL {engine.get('pol', '?')} | USDC {engine.get('usdc', '?')}")
    ok, checks = evaluate_remote(status, floor)
    print(_fmt(checks))
    print("\nGO-LIVE PREFLIGHT (remote)", "PASS — the live engine is signing on Amoy" if ok
          else "FAIL — the live engine is not go-live yet (see notes/14-go-live-runbook.md)")
    return 0 if ok else 1


def run_local(floor: float, kill_path: str, fleet_path: str | None) -> int:
    from execution.testnet_chain import AmoySigner, ChainReader, load_fleet, make_w3

    chain_id = None
    head_ok = False
    pol = usdc = None
    addr = None
    try:
        w3 = make_w3()
        from execution.testnet_chain import _rpc_retry
        chain_id = _rpc_retry(lambda: w3.eth.chain_id)
        head_ok = True
    except Exception as e:  # noqa: BLE001 — unreachable RPC must read as FAIL, not a traceback
        print(f"  … Amoy RPC unreachable: {type(e).__name__}: {e}")
        w3 = None
    fleet, abi = load_fleet(fleet_path)
    n_managed = sum(1 for m in fleet if m.keeper_managed)
    if w3 is not None:
        try:
            addr = AmoySigner(w3).address
            if addr:
                bals = ChainReader(w3, abi).balances(addr)
                pol, usdc = bals["pol"], bals["usdc"]
        except Exception as e:  # noqa: BLE001
            print(f"  … could not read engine balance: {type(e).__name__}: {e}")

    print(f"engine  {addr or '(no key)'} | POL {pol if pol is not None else '?'} "
          f"| USDC {usdc if usdc is not None else '?'}")
    ok, checks = evaluate_local(
        chain_id=chain_id, head_ok=head_ok, engine_addr=addr, pol=pol, usdc=usdc,
        n_managed=n_managed, kill_present=os.path.exists(kill_path), floor=floor,
        autostart=os.environ.get("KEEPER_AUTOSTART") == "1",
        interval=os.environ.get("KEEPER_INTERVAL_S"))
    print(_fmt(checks))
    print("\nGO-LIVE PREFLIGHT (local)", "PASS — wallet/chain/fleet ready to sign" if ok
          else "FAIL — resolve the FAIL rows above (see notes/14-go-live-runbook.md)")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only go-live readiness checker for the testnet engine")
    ap.add_argument("--remote", metavar="BASE_URL", default=None,
                    help="check a deployed service via GET {BASE_URL}/api/testnet/keeper")
    ap.add_argument("--floor", type=float, default=None, help="POL floor (default: config min_pol_balance)")
    ap.add_argument("--fleet", default=None, help="fleet registry path (default: markets.json)")
    args = ap.parse_args()

    floor = args.floor
    kill_path = ".data_cache/risk/KILL"
    if floor is None:
        try:
            from config.loader import load_config
            cfg = load_config()
            floor = cfg.min_pol_balance
            kill_path = cfg.kill_switch_path
        except Exception:  # noqa: BLE001 — fall back to the documented defaults
            floor = 0.02

    rc = run_remote(args.remote, floor) if args.remote else run_local(floor, kill_path, args.fleet)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

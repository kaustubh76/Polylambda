"""Generate the backend engine wallet (Polygon Amoy testnet burner).

Writes ENGINE_PRIVATE_KEY + ENGINE_ADDRESS to the gitignored .env (never printed), and prints ONLY
the address to fund. Idempotent: keeps an existing key if .env already has one.

    python scripts/gen_engine_wallet.py
"""
from __future__ import annotations

import os
import re

from eth_account import Account

ENV = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


def _read_env() -> dict[str, str]:
    if not os.path.exists(ENV):
        return {}
    out = {}
    for line in open(ENV):
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main() -> None:
    env = _read_env()
    if env.get("ENGINE_PRIVATE_KEY"):
        addr = env.get("ENGINE_ADDRESS") or Account.from_key(env["ENGINE_PRIVATE_KEY"]).address
        print("engine wallet already provisioned (kept existing key).")
        print("ENGINE ADDRESS (fund this):", addr)
        return

    acct = Account.create()
    lines = []
    if os.path.exists(ENV):
        lines = open(ENV).read().rstrip("\n").split("\n")
    lines.append(f"ENGINE_PRIVATE_KEY={acct.key.hex()}")
    lines.append(f"ENGINE_ADDRESS={acct.address}")
    with open(ENV, "w") as f:
        f.write("\n".join([ln for ln in lines if ln.strip()]) + "\n")
    os.chmod(ENV, 0o600)
    print("generated a fresh Amoy engine wallet; key written to .env (gitignored, not printed).")
    print("ENGINE ADDRESS (fund this):", acct.address)


if __name__ == "__main__":
    main()

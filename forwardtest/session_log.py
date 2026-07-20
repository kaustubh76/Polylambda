"""
session_log — the JSONL session record: the runner WRITES it, the live ablation READS it.

One line per event. Every record carries {t, type, mode, simulated} — `simulated` defaults True
(paper/paper-live never place real orders, JURISDICTION.md paper-only default); a REAL execution
mode owns flipping it: the testnet keeper (execution/testnet_keeper.py) passes simulated=False
because its fills are on-chain transactions. No consumer may override the writer's stamp.

Record types (fields set by the runner/loop; the ablation is a pure reader of this file):
  session_start   config snapshot + arm_rule + per-market {cid, token_id, category, end_date_ts,
                  arm, lambda_select, lambda_jump, ci_low, ci_high, micro{...}, seed}
  tick            cid, mid, sigma, T_t, best_bid, best_ask, inventory, cash, equity_mark,
                  sim_reward_score_cum, quoting
  quote           cid, arm, bid, ask, bid_size, ask_size, replaced, order_ids, defensive
  fill            cid, arm, side, price, size, order_id, queue_model, inventory_after, cash_after
  exit            cid, arm, trigger, lambda_jump, lambda_star, e_jump_loss, forgone_rewards,
                  spread_cost, inventory_before, inventory_after, exit_price, haircut_paid
  dispute_witnessed  cid, source, note
  session_end     per_market{...}, per_arm_totals{...}, n_disputes_witnessed, uptime_fraction
"""
from __future__ import annotations

import json
import os
import time


def open_log(path: str):
    """Open (append) a session log file, creating parent dirs. Returns the file handle."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a")


def append(fh, record_type: str, *, mode: str, t: float | None = None,
           simulated: bool = True, **fields) -> dict:
    """Write one event line (adds t/type/mode/simulated), flush, and return the record."""
    rec = {"t": time.time() if t is None else t, "type": record_type, "mode": mode,
           "simulated": simulated, **fields}
    fh.write(json.dumps(rec) + "\n")
    fh.flush()
    return rec


def read(path: str) -> list[dict]:
    """All records, in order. Skips blank/truncated trailing lines (crash-tolerant reader)."""
    out: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn final line from a killed session must not poison the ablation
    return out

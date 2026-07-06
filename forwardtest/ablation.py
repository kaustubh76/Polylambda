"""
ablation — LIVE lambda ON vs OFF over the forward-test window.

⚠ Underpowered by design (see replay_ablation.py and ../DECISIONS.md #11): an 18-day live run
witnesses ~0-3 disputes, so this is a DIRECTIONAL SANITY CHECK only, NOT the edge proof. Always
report it alongside the pre-registered power calc; the PRIMARY proof is the historical replay.

This is a PURE READER of the session log written by forwardtest.runner (schema in
forwardtest.session_log): it never touches the network and never recomputes the model — it splits
the recorded stream into the lambda_on / lambda_off arms and reports the delta.
"""
from __future__ import annotations

# A ~2-3 week live run witnesses a handful of disputes at best; below this the delta is noise.
MIN_DISPUTES_FOR_SIGNAL = 10


def _arm_rollup(records: list[dict], arm: str) -> dict:
    """Fold the log stream for one arm: final equity/inventory/cash, fills, exits, reward score.

    Equity is taken from the LAST session_end per_market row when present (authoritative mark),
    else reconstructed from the last tick's equity_mark per cid (crash-tolerant fallback)."""
    fills = [r for r in records if r.get("type") == "fill" and r.get("arm") == arm]
    exits = [r for r in records if r.get("type") == "exit" and r.get("arm") == arm]

    equity = cash = inventory = sim_reward = 0.0
    end = next((r for r in reversed(records) if r.get("type") == "session_end"), None)
    if end and end.get("per_market"):
        rows = [row for row in end["per_market"] if row.get("arm") == arm]
        equity = sum(row.get("equity_mark", 0.0) for row in rows)
        cash = sum(row.get("cash", 0.0) for row in rows)
        inventory = sum(row.get("inventory", 0.0) for row in rows)
        sim_reward = sum(row.get("sim_reward_score", 0.0) for row in rows)
    else:
        # fallback (session killed before session_end): tick records carry cid but not arm, so
        # attribute equity via the cids that appear in THIS arm's quote/fill/exit records, using
        # each cid's last-seen tick equity_mark and cash.
        quotes = [r for r in records if r.get("type") == "quote" and r.get("arm") == arm]
        arm_cids = {r["cid"] for r in fills} | {r["cid"] for r in exits} | {r["cid"] for r in quotes}
        last_equity: dict[str, float] = {}
        last_cash: dict[str, float] = {}
        for r in records:
            if r.get("type") == "tick" and r.get("cid") in arm_cids:
                last_equity[r["cid"]] = r.get("equity_mark", 0.0)
                last_cash[r["cid"]] = r.get("cash", 0.0)
        equity = sum(last_equity.values())
        cash = sum(last_cash.values())
        sim_reward = 0.0

    return {"arm": arm, "n_fills": len(fills), "n_exits": len(exits),
            "equity_mark": equity, "pnl": equity, "cash": cash, "inventory": inventory,
            "sim_reward_score": sim_reward}


def run_live_ablation(session_log_path: str) -> dict:
    """Split the forward-test session into lambda-ON vs lambda-OFF arms and report the delta.

    Returns a dict with per-arm rollups, the ON−OFF P&L/exit deltas, n_disputes, and an explicit
    `underpowered`/`caveat` verdict. P&L is equity mark only; sim_reward_score is reported but
    NEVER added into P&L (it is simulated, not realized — JURISDICTION.md honesty constraint).
    """
    from forwardtest.session_log import read

    records = read(session_log_path)
    on = _arm_rollup(records, "lambda_on")
    off = _arm_rollup(records, "lambda_off")

    n_disputes = sum(1 for r in records if r.get("type") == "dispute_witnessed")
    end = next((r for r in reversed(records) if r.get("type") == "session_end"), None)
    if end is not None:
        n_disputes = end.get("n_disputes_witnessed", n_disputes)

    underpowered = n_disputes < MIN_DISPUTES_FOR_SIGNAL
    delta = {"pnl": on["pnl"] - off["pnl"], "n_exits": on["n_exits"] - off["n_exits"],
             "sim_reward_score": on["sim_reward_score"] - off["sim_reward_score"]}

    caveat = (
        f"UNDERPOWERED: {n_disputes} dispute(s) witnessed (< {MIN_DISPUTES_FOR_SIGNAL} needed for a "
        "signal). This is a DIRECTIONAL SANITY CHECK only — the primary edge proof is the historical "
        "replay (forwardtest.replay_ablation), reported with the pre-registered power calc "
        "(DECISIONS.md #11). Do NOT read the sign of the delta as evidence."
    ) if underpowered else (
        f"{n_disputes} disputes witnessed; still corroborate against the historical replay before "
        "drawing conclusions."
    )

    return {"session_log_path": session_log_path, "n_records": len(records),
            "lambda_on": on, "lambda_off": off, "delta_on_minus_off": delta,
            "n_disputes": n_disputes, "underpowered": underpowered, "caveat": caveat}


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        raise SystemExit("usage: python -m forwardtest.ablation <session_log.jsonl>")
    print(json.dumps(run_live_ablation(path), indent=2))

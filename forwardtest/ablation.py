"""
ablation — LIVE lambda ON vs OFF over the forward-test window.

⚠ Underpowered by design (see replay_ablation.py and ../DECISIONS.md #11): an 18-day live run
witnesses ~0-3 disputes, so this is a DIRECTIONAL SANITY CHECK only, NOT the edge proof. Always
report it alongside the pre-registered power calc; the PRIMARY proof is the historical replay.
"""
from __future__ import annotations


def run_live_ablation(session_log_path: str) -> dict:
    """TODO: split the forward-test session into lambda-ON vs lambda-OFF arms; report the delta
    with n_disputes and the power calc. Label as underpowered."""
    raise NotImplementedError("run_live_ablation: underpowered live sanity check (label as such)")

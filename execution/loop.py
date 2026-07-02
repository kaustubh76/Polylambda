"""
loop — the quoting loop + REWARD-AWARE exit-on-risk.

Corrected design (see ../DECISIONS.md #1, and Panel L of the diagram):
  read book -> estimate sigma/fair/lambda -> compute_quote -> place/cancel maker orders ->
  manage inventory -> EXIT-ON-RISK.

Exit-on-risk (the defining move), CORRECTED:
  There is NO trading lock during a dispute (redemption freezes; the CLOB stays open; exit costs
  a ~5c haircut into thinner liquidity). So do NOT blindly flatten. Exit is REWARD-AWARE:

      if proposal_detected(market) or lambda_jump > lambda_star:
          if E[jump loss] > forgone_rewards + spread:      # only when it actually pays
              cancel/replace resting orders
              REDUCE inventory before the danger window     # reduce, not necessarily to 0
              re-quote lighter (not zero) until resolved

  Pulling liquidity forfeits Liquidity Rewards (uptime + two-sided depth), so the ablation MUST
  net reward loss against avoided adverse selection.

  Latency: source the time-critical proposal signal from a LOW-LATENCY log subscription (not the
  batch indexer), with a reorg-confirmation guard before any costly action.
"""
from __future__ import annotations


def forgone_rewards_if_exit(market_state: dict) -> float:
    """TODO: estimate reward score lost by pulling (size x uptime x quadratic midpoint proximity)."""
    raise NotImplementedError("forgone_rewards_if_exit: model the Liquidity Rewards score at risk")


def should_exit(lambda_jump: float, lambda_star: float, e_jump_loss: float,
                forgone_rewards: float, spread: float, proposal_detected: bool) -> bool:
    """Reward-aware exit gate — implemented (pure decision rule)."""
    triggered = proposal_detected or (lambda_jump > lambda_star)
    return triggered and (e_jump_loss > forgone_rewards + spread)


def run_loop(markets: list[str], mode: str = "paper") -> None:
    """TODO: main quoting loop. In paper/paper-live: read the REAL book, simulate fills locally,
    place NO real orders. Only `live` places real orders (jurisdiction-gated — see JURISDICTION.md)."""
    raise NotImplementedError("run_loop: wire estimators -> quote -> (sim/real) execution + exit")

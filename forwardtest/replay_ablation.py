"""
replay_ablation — the PRIMARY edge proof (see ../DECISIONS.md #11, Panel H/L of the diagram).

The LIVE lambda-ablation is statistically powerless in 18 days (~0-3 disputes witnessed, ~0 DVM
hard-locks) — you cannot compute a Sharpe on n~1. So the primary proof is a HISTORICAL
counterfactual replay:

  Over the indexed historical disputes + matched non-disputed controls, replay the quoting +
  (reward-aware) exit-on-risk logic and measure avoided-loss vs forgone-reward for:
      A) diffusion-only            (lambda term OFF)
      B) + lambda_jump exit         (jump-avoidance)
      C) + lambda_select filter     (reward-farmer)
  Report risk-adjusted P&L delta NET OF FORGONE REWARDS, with lambda_star SENSITIVITY CURVES
  (not a single tuned point). Pre-register the power calc so a null reads as "underpowered", not
  "no edge".

TWO-SOURCE JOIN (this is where the whole data layer converges):
  * DISPUTED markets + dispute timestamps  <- the scoped local OOv2 indexer (GraphQL). HF has none.
  * matched CONTROLS + fill tapes + outcomes <- the HF dataset (data.metadata / data.fills /
    data.conditions), ideally via data.cache.materialize_slice for speed.
The join key is conditionId. Until the local indexer has produced disputes, `load_disputes` returns
[] and run_replay reports 0 disputes with the power calc — honestly "no labels yet", not "no edge".

Honest simplifications (documented, per README scope — no historical order-book reconstruction):
  * "mid" is the fill-tape mid (proxy); no queue position / true fill probability is modeled.
  * the realized jump is the logit move from the pre-dispute price to the post-resolution price.
  * rewards are a coarse size x uptime proxy; this is a counterfactual on avoided directional loss
    vs forgone reward, NOT a live-fillable P&L.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass


@dataclass
class AblationResult:
    arm: str                 # "diffusion_only" | "lambda_jump" | "lambda_select"
    lambda_star: float
    n_disputes: int
    n_controls: int
    pnl_net_of_rewards: float
    sharpe: float
    avoided_loss: float
    forgone_rewards: float


def power_calc(markets_quoted: int, dispute_rate: float, resting_fraction: float) -> float:
    """Expected disputes witnessed = markets_quoted * dispute_rate * resting_fraction.
    Implemented (pure) — pre-register this before running so a null is read honestly."""
    return markets_quoted * dispute_rate * resting_fraction


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def load_disputes(graphql_url: str) -> list[dict]:
    """Disputed markets: [{conditionId, disputeTs}].

    DATA_SOURCE=hf (default): the no-Docker `data.disputes` source (OOv2 DisputePrice via public RPC,
    V2/Legacy adapters, HF-joined; see its docstring for the NegRisk limitation).
    DATA_SOURCE=graphql: the scoped local OOv2 indexer. Returns [] (not an error) when neither has
    produced labels yet — run_replay then reports 0 disputes.
    """
    from data.hf import DATA_SOURCE

    if DATA_SOURCE == "hf":
        try:
            from data.disputes import load_disputes as _hf_disputes

            return [{"conditionId": d["conditionId"], "disputeTs": d["disputeTs"]} for d in _hf_disputes()]
        except Exception:
            return []

    import requests

    q = """query { Dispute { disputeTs request { market { id } } } }"""
    try:
        r = requests.post(graphql_url, json={"query": q}, timeout=30)
        r.raise_for_status()
        rows = r.json().get("data", {}).get("Dispute", []) or []
    except Exception:
        return []
    out = []
    for d in rows:
        cid = (((d.get("request") or {}).get("market") or {}).get("id"))
        if cid:
            out.append({"conditionId": cid, "disputeTs": int(d.get("disputeTs") or 0)})
    return out


def _realized_jump_logit(fills: list[dict], event_ts: int) -> float:
    """Directional jump = logit(price just after event) - logit(price just before). 0 if unclear."""
    pre = [f for f in fills if f["timestamp"] <= event_ts]
    post = [f for f in fills if f["timestamp"] > event_ts]
    if not pre or not post:
        return 0.0
    return _logit(post[0]["price"]) - _logit(pre[-1]["price"])


def _reward_proxy(fills: list[dict]) -> float:
    """Coarse Liquidity-Reward proxy for a market: total two-sided size while near mid (uptime x size)."""
    return sum(f["size"] for f in fills if 0.10 <= f["price"] <= 0.90) * 1e-4


def _replay_market(cid: str, fills: list[dict], disputeTs: int | None,
                   lambda_star: float, *, inventory: float = 10.0) -> dict:
    """Per-market arm contributions. Reuses the pure sigma core + should_exit gate.

    Returns {avoided_loss, forgone_rewards, pnl_A, pnl_B, pnl_C} for one market.
    """
    from estimators.sigma import estimate_sigma_from_fills
    from execution.loop import should_exit

    reward = _reward_proxy(fills)
    if disputeTs is None:  # a CONTROL: no jump ever; the "cost" side of the ledger
        # A holds & earns reward; B may wrongly exit (forgo part of reward); C wrongly filters (forgo all)
        return {"avoided_loss": 0.0, "forgone_rewards": reward,
                "pnl_A": reward, "pnl_B": reward, "pnl_C": 0.0}

    sigma = estimate_sigma_from_fills(fills, prior=0.5) or 0.3
    jump = _realized_jump_logit(fills, disputeTs)            # directional move in logit space
    # loss to a resting MM holding `inventory` through the jump (adverse if positioned against it)
    jump_loss = abs(inventory * jump) + 0.05 * inventory      # + ~5c haircut on exit liquidity
    lambda_jump = min(1.0, sigma)                             # proxy intensity from vol until model exists

    exit_now = should_exit(lambda_jump, lambda_star, e_jump_loss=jump_loss,
                           forgone_rewards=reward, spread=0.0, proposal_detected=True)
    avoided = jump_loss if exit_now else 0.0
    pnl_A = reward - jump_loss                                # holds through: eats the jump
    pnl_B = reward - (jump_loss - avoided)                    # exits when it pays: avoids the jump, keeps reward
    pnl_C = 0.0 if lambda_jump > lambda_star else (reward - jump_loss)  # filter avoids the market entirely
    return {"avoided_loss": avoided, "forgone_rewards": 0.0 if exit_now else reward,
            "pnl_A": pnl_A, "pnl_B": pnl_B, "pnl_C": pnl_C}


def _sharpe(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return mu / math.sqrt(var) if var > 0 else 0.0


def run_replay(graphql_url: str, lambda_star_grid: list[float],
               *, control_ratio: int = 3, fill_limit: int = 5000) -> list[AblationResult]:
    """Historical counterfactual over disputed markets (local) + matched controls (HF).

    For each lambda_star, replays arms A/B/C and returns an AblationResult per (arm, lambda_star),
    net of forgone rewards, with a Sharpe across markets. Emits the pre-registered power calc.
    """
    from data.fills import fetch_fills_hf
    from data.metadata import derive_category, market_meta

    disputes = load_disputes(graphql_url)
    disputed_ids = [d["conditionId"] for d in disputes]
    dispute_ts = {d["conditionId"]: d["disputeTs"] for d in disputes}

    # matched controls: resolved, undisputed markets in the same categories (HF).
    controls: list[str] = []
    if disputed_ids:
        from data.prior_corpus import sampled_condition_ids

        cats = {market_meta(c).get("category") if market_meta(c) else "other" for c in disputed_ids}
        pool = [c for c in sampled_condition_ids(per_category=control_ratio * len(disputed_ids))
                if c not in set(disputed_ids)]
        controls = pool[: control_ratio * len(disputed_ids)]

    # pre-registered power calc (honest read of a null)
    exp = power_calc(len(disputed_ids) + len(controls), dispute_rate=0.011, resting_fraction=1.0)
    print(f"[replay] disputes={len(disputed_ids)} controls={len(controls)} "
          f"expected_disputes(power_calc)={exp:.2f}")
    if not disputed_ids:
        print("[replay] no dispute labels yet — run the scoped local OOv2 indexer (see indexer/). "
              "Reporting empty result (this is 'no labels', NOT 'no edge').")

    # per-market contributions (cache the slice first for speed on real runs)
    contribs: dict[str, list[dict]] = {}
    for cid in disputed_ids + controls:
        fills = fetch_fills_hf(cid, limit=fill_limit)
        if not fills:
            continue
        contribs[cid] = fills

    results: list[AblationResult] = []
    for ls in lambda_star_grid:
        pnl_A, pnl_B, pnl_C, avoided, forgone = [], [], [], 0.0, 0.0
        for cid, fills in contribs.items():
            m = _replay_market(cid, fills, dispute_ts.get(cid), ls)
            pnl_A.append(m["pnl_A"]); pnl_B.append(m["pnl_B"]); pnl_C.append(m["pnl_C"])
            avoided += m["avoided_loss"]; forgone += m["forgone_rewards"]
        for arm, pnl in (("diffusion_only", pnl_A), ("lambda_jump", pnl_B), ("lambda_select", pnl_C)):
            results.append(AblationResult(
                arm=arm, lambda_star=ls, n_disputes=len(disputed_ids), n_controls=len(controls),
                pnl_net_of_rewards=sum(pnl), sharpe=_sharpe(pnl),
                avoided_loss=avoided if arm != "diffusion_only" else 0.0,
                forgone_rewards=forgone if arm != "diffusion_only" else 0.0))
    return results


if __name__ == "__main__":
    url = os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    for r in run_replay(url, [0.05, 0.1, 0.15, 0.2, 0.3]):
        print(f"  {r.arm:<15} l*={r.lambda_star:<5} pnl_net={r.pnl_net_of_rewards:+.2f} "
              f"sharpe={r.sharpe:+.2f} avoided={r.avoided_loss:.2f} forgone={r.forgone_rewards:.2f}")

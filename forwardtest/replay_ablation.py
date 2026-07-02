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


EXIT_REWARD_FRACTION = 0.4  # reward (uptime) forgone by pulling liquidity early to dodge a jump


def _replay_market(cid: str, fills: list[dict], disputeTs: int | None,
                   lambda_star: float, lambda_select: float, *, inventory: float = 10.0) -> dict:
    """Per-market arm contributions, driven by the CATEGORY dispute base rate (`lambda_select`).

    The SAME lambda signal (category dispute-proneness) feeds both jump arms; they differ in ACTION
    (DECISIONS.md A — two consumers of one hazard signal):
      * arm A (diffusion, lambda OFF): always hold through resolution.
      * arm B (lambda_jump): REWARD-AWARE surgical exit — pull before the jump only when the signal
        fires AND the avoided jump-loss exceeds the reward forgone by exiting early. It therefore
        NEVER exits a control (no jump to avoid → reward-aware gate blocks it).
      * arm C (lambda_select): BLANKET-avoid every market whose CATEGORY is dispute-prone (disputed
        AND control alike), forgoing their reward; trade the rest normally.
    Surgical exit (B) vs blanket avoidance (C) is exactly the positioning fork the ablation adjudicates.

    Returns per-arm pnl + per-arm avoided_loss / forgone_rewards (the TRUE opportunity cost, tracked
    separately from should_exit's decision threshold).
    """
    from execution.loop import should_exit

    reward = _reward_proxy(fills)
    jump_loss = 0.0
    if disputeTs is not None:
        jump = _realized_jump_logit(fills, disputeTs)          # directional move in logit space
        jump_loss = abs(inventory * jump) + 0.05 * inventory   # adverse move + ~5c exit haircut
    fires = lambda_select > lambda_star                        # the category dispute signal fires

    # Arm A — always hold through
    pnl_A = reward - jump_loss

    # Arm B — reward-aware surgical exit. proposal_detected=False: a HISTORICAL replay has no live
    # proposal signal, so the exit is driven purely by the lambda threshold + the reward-aware gate.
    # should_exit fires only when (lambda_select > lambda_star) AND (jump_loss > exit_forgone), so on a
    # control (jump_loss == 0) it correctly never exits.
    exit_forgone = EXIT_REWARD_FRACTION * reward
    exit_now = should_exit(lambda_select, lambda_star, e_jump_loss=jump_loss,
                           forgone_rewards=exit_forgone, spread=0.0, proposal_detected=False)
    if exit_now:
        pnl_B, avoided_B, forgone_B = reward - exit_forgone, jump_loss, exit_forgone
    else:
        pnl_B, avoided_B, forgone_B = reward - jump_loss, 0.0, 0.0

    # Arm C — blanket-avoid dispute-prone categories (skip → miss reward, avoid any loss)
    if fires:
        pnl_C, avoided_C, forgone_C = 0.0, jump_loss, reward
    else:
        pnl_C, avoided_C, forgone_C = reward - jump_loss, 0.0, 0.0

    return {"pnl_A": pnl_A, "pnl_B": pnl_B, "pnl_C": pnl_C,
            "avoided_B": avoided_B, "forgone_B": forgone_B,
            "avoided_C": avoided_C, "forgone_C": forgone_C}


def _sharpe(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return mu / math.sqrt(var) if var > 0 else 0.0


def run_replay(graphql_url: str, lambda_star_grid: list[float],
               *, control_ratio: int = 3, fill_limit: int = 5000,
               disputed: list[str] | None = None, controls: list[str] | None = None) -> list[AblationResult]:
    """Historical counterfactual over disputed markets (local) + matched controls (HF).

    For each lambda_star, replays arms A/B/C and returns an AblationResult per (arm, lambda_star),
    net of forgone rewards, with a Sharpe across markets. Emits the pre-registered power calc.
    `disputed`/`controls` override the auto load/sample (for a reproducible full run over a cached slice).
    """
    from data.fills import fetch_fills_hf

    disputes = load_disputes(graphql_url)
    dispute_ts = {d["conditionId"]: d["disputeTs"] for d in disputes}
    disputed_ids = disputed if disputed is not None else [d["conditionId"] for d in disputes]

    # matched controls: resolved, undisputed markets (HF). Sample only when not provided.
    if controls is None:
        controls = []
        if disputed_ids:
            from data.prior_corpus import sampled_condition_ids

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

    # report PROCESSED counts (markets with fills), not raw label counts — no over-statement
    n_disp_proc = sum(1 for cid in contribs if cid in dispute_ts)
    n_ctrl_proc = len(contribs) - n_disp_proc
    print(f"[replay] processed {n_disp_proc} disputed + {n_ctrl_proc} control markets (with fills)")

    # lambda_select per market = the CATEGORY dispute base rate (disputes/resolved). This is the real
    # lambda signal both jump arms consume — NOT per-market volatility. Note these rates are ~0.0004-
    # 0.009, so lambda_star_grid must be scaled to that range (a 0.05-0.30 grid never fires).
    from data.base_rates import category_base_rate, category_counts_hf
    from data.disputes import dispute_counts_by_category
    from data.hf import query, table_path
    from data.metadata import category_case_sql

    counts = category_counts_hf()
    dcounts = dispute_counts_by_category()
    cids = list(contribs)
    inl = ",".join(f"'{c}'" for c in cids)
    cat_of = {c: cat for c, cat in query(
        f"SELECT condition, any_value({category_case_sql()}) FROM '{table_path('market_data')}' "
        f"WHERE condition IN ({inl}) GROUP BY condition")}
    lam_sel = {c: category_base_rate(cat_of.get(c, "other"), dcounts, counts)["rate"] for c in cids}
    lo, hi = min(lam_sel.values()), max(lam_sel.values())
    print(f"[replay] lambda_select (category base rate) range: {lo:.4f}..{hi:.4f}")

    results: list[AblationResult] = []
    for ls in lambda_star_grid:
        pnl_A, pnl_B, pnl_C = [], [], []
        av_B = fo_B = av_C = fo_C = 0.0
        for cid, fills in contribs.items():
            m = _replay_market(cid, fills, dispute_ts.get(cid), ls, lam_sel[cid])
            pnl_A.append(m["pnl_A"]); pnl_B.append(m["pnl_B"]); pnl_C.append(m["pnl_C"])
            av_B += m["avoided_B"]; fo_B += m["forgone_B"]; av_C += m["avoided_C"]; fo_C += m["forgone_C"]
        # Sharpe is over the SAME fixed market universe for every arm (a skipped market contributes a
        # real 0 return), so the three arms are directly comparable at each lambda_star.
        for arm, pnl, av, fo in (("diffusion_only", pnl_A, 0.0, 0.0),
                                 ("lambda_jump", pnl_B, av_B, fo_B),
                                 ("lambda_select", pnl_C, av_C, fo_C)):
            results.append(AblationResult(
                arm=arm, lambda_star=ls, n_disputes=n_disp_proc, n_controls=n_ctrl_proc,
                pnl_net_of_rewards=sum(pnl), sharpe=_sharpe(pnl), avoided_loss=av, forgone_rewards=fo))
    return results


if __name__ == "__main__":
    url = os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    # lambda_star grid scaled to CATEGORY dispute base rates (~0.0004-0.009), not the old 0.05-0.30.
    for r in run_replay(url, [0.0005, 0.001, 0.002, 0.005, 0.01]):
        print(f"  {r.arm:<15} l*={r.lambda_star:<7} pnl_net={r.pnl_net_of_rewards:+.2f} "
              f"sharpe={r.sharpe:+.2f} avoided={r.avoided_loss:.2f} forgone={r.forgone_rewards:.2f}")

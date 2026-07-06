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

# --- Liquidity Rewards score model (DECISIONS.md #6) -----------------------------------------
# Program shape: score = size x uptime x QUADRATIC midpoint-proximity; two-sided quotes get full
# credit; a single-sided quote earns ~1/3 credit while mid is in [0.10, 0.90] and ZERO outside it;
# $1/day floor per qualifying market; per-market max_incentive_spread / reward_min_size params.
REWARD_BAND = (0.10, 0.90)      # single-sided credit exists only inside this mid band
SINGLE_SIDED_FACTOR = 1.0 / 3.0
DAILY_FLOOR_USD = 1.0
DANGER_WINDOW_DAYS = 0.125      # ~3h: the dispute auto-reset mode (2-4h); DVM escalation is 4-6d


def _side_score(price: float | None, size: float, mid: float,
                max_spread: float, min_size: float) -> float:
    """Quadratic per-side score Q; 0 if unquoted, sub-min-size, or outside max_incentive_spread."""
    if price is None or size < min_size or max_spread <= 0:
        return 0.0
    dist = abs(price - mid)
    if dist > max_spread:
        return 0.0
    return size * ((max_spread - dist) / max_spread) ** 2


def _reward_score(mid: float, bid: float | None, ask: float | None,
                  bid_size: float, ask_size: float,
                  max_spread: float, min_size: float) -> float:
    """The market's reward score S for our current quotes (shared by the exit model AND the loop's
    per-tick accrual, so the two can never diverge)."""
    qb = _side_score(bid, bid_size, mid, max_spread, min_size)
    qa = _side_score(ask, ask_size, mid, max_spread, min_size)
    if qb > 0 and qa > 0:
        return qb + qa                                  # two-sided: full credit
    single = qb + qa                                    # exactly one (or zero) side qualifies
    if single <= 0:
        return 0.0
    if REWARD_BAND[0] <= mid <= REWARD_BAND[1]:
        return SINGLE_SIDED_FACTOR * single             # single-sided inside the band: ~1/3 credit
    return 0.0                                          # single-sided outside [0.10, 0.90]: zero


def forgone_rewards_if_exit(market_state: dict) -> float:
    """Estimated USD Liquidity Rewards forfeited by pulling quotes for the danger window.

    market_state keys: mid, our_bid, our_ask, bid_size, ask_size, max_incentive_spread,
    reward_min_size, rewards_daily_rate_usd, and optionally danger_window_days (default ~3h,
    the dispute auto-reset mode) and competitor_score.

    competitor_score defaults to 0.0 -> our share of the reward pool is treated as 1.0, which
    OVERSTATES the forgone amount. That bias is deliberate: forgone sits on the blocking side of
    the exit gate (E[jump loss] > forgone + spread), so overstating it biases AGAINST exiting —
    if lambda_jump still wins the ablation under this handicap, the result is robust.
    """
    s = _reward_score(
        market_state["mid"], market_state.get("our_bid"), market_state.get("our_ask"),
        market_state.get("bid_size", 0.0), market_state.get("ask_size", 0.0),
        market_state.get("max_incentive_spread", 0.0), market_state.get("reward_min_size", 0.0),
    )
    if s <= 0:
        return 0.0                                      # earning nothing -> forfeiting nothing
    window = market_state.get("danger_window_days", DANGER_WINDOW_DAYS)
    share = s / (s + market_state.get("competitor_score", 0.0))
    forgone = share * market_state.get("rewards_daily_rate_usd", 0.0) * window
    return max(forgone, DAILY_FLOOR_USD * window)       # the $1/day floor, pro-rated


def should_exit(lambda_jump: float, lambda_star: float, e_jump_loss: float,
                forgone_rewards: float, spread: float, proposal_detected: bool) -> bool:
    """Reward-aware exit gate — implemented (pure decision rule)."""
    triggered = proposal_detected or (lambda_jump > lambda_star)
    return triggered and (e_jump_loss > forgone_rewards + spread)


# ==============================================================================================
# The quoting loop: MarketState + tick() + run_loop. All I/O flows through the injected clob
# adapter (execution/paper.py PaperClob / PaperLiveClob) and the injected session logger — the
# loop itself never opens a socket, so paper mode is provably network-free (tested).
# ==============================================================================================
import math
from dataclasses import dataclass, field

REDUCE_FRACTION = 0.5   # inventory fraction taker-reduced on a reward-aware exit
LIGHT_FACTOR = 0.3      # re-quote size multiplier while defensive (reduce, don't vanish)


@dataclass
class MarketState:
    cid: str
    token_id: str
    category: str
    arm: str                                   # "lambda_on" | "lambda_off" (live ablation arms)
    end_date_ts: float
    inventory: float = 0.0
    cash: float = 0.0
    order_ids: list = field(default_factory=list)
    micro: dict = field(default_factory=dict)
    lam: object = None                         # LambdaOutput, resolved ONCE at session start
    sigma_prior: float = 0.15
    defensive: bool = False
    sim_reward_score: float = 0.0              # accrued score — NEVER added to any P&L figure
    n_exits: int = 0


def _tick_round(price: float, tick: float, side: str) -> float:
    """Post-only-safe: bid rounds DOWN, ask rounds UP — rounding can never cross the book."""
    steps = price / tick
    px = (math.floor(steps) if side == "BUY" else math.ceil(steps)) * tick
    return min(max(px, tick), 1.0 - tick)


def tick(state: MarketState, book: dict, now_ts: float, cfg, clob, log=None,
         *, proposal_detected: bool = False) -> MarketState:
    """One decision cycle for one market. Pure given (book, clob, log) injections."""
    from estimators.fair_value import estimate_fair_value
    from estimators.sigma import estimate_sigma_from_fills

    if not book.get("bids") or not book.get("asks"):
        return state                                       # empty book: hold quotes, do nothing
    T_t = max((state.end_date_ts - now_ts) / 86400.0, 0.0)  # days (min_horizon guards ~0)
    mid = estimate_fair_value(book, T_t)
    sigma = estimate_sigma_from_fills(clob.tape(state.token_id), prior=state.sigma_prior,
                                      b=cfg.ewma_b, min_trades=cfg.min_trades_for_sigma)

    lam_on = state.arm == "lambda_on" and state.lam is not None
    lam_jump = state.lam.lambda_jump if lam_on else 0.0
    e_loss = state.lam.e_loss if lam_on else 0.0
    drift = state.lam.jump_drift if lam_on else 0.0

    micro = state.micro or clob.get_micro(state.token_id)
    tick_size = float(micro.get("tick_size", 0.01))
    min_size = float(micro.get("min_order_size", 5.0))

    # --- reward-aware exit (λ-ON arm only; the OFF arm never evaluates the gate) ---
    if lam_on and state.inventory != 0.0:
        cur_bid = book["bids"][0][0]
        cur_ask = book["asks"][0][0]
        qsize = cfg.quote_size * (LIGHT_FACTOR if state.defensive else 1.0)
        mstate = {"mid": mid, "our_bid": cur_bid, "our_ask": cur_ask,
                  "bid_size": qsize, "ask_size": qsize,
                  "max_incentive_spread": micro.get("max_incentive_spread", 0.0),
                  "reward_min_size": micro.get("reward_min_size", 0.0),
                  "rewards_daily_rate_usd": micro.get("rewards_daily_rate_usd", 0.0)}
        forgone = forgone_rewards_if_exit(mstate)
        # E[jump loss] in USD: logit-space e_loss -> price-space via the local Jacobian p(1-p)
        e_jump_loss_usd = abs(state.inventory) * e_loss * mid * (1.0 - mid)
        reduce_size = abs(state.inventory) * REDUCE_FRACTION
        spread_cost = 0.5 * (cur_ask - cur_bid) * reduce_size
        if should_exit(lam_jump, cfg.lambda_star, e_jump_loss_usd, forgone, spread_cost,
                       proposal_detected):
            clob.cancel(list(state.order_ids))
            state.order_ids.clear()
            # taker-reduce at the touch: sell inventory into the bid / buy back into the ask
            px = cur_bid if state.inventory > 0 else cur_ask
            delta = -reduce_size if state.inventory > 0 else reduce_size
            state.cash += -delta * px
            inv_before = state.inventory
            state.inventory += delta
            state.defensive = True
            state.n_exits += 1
            if log:
                log("exit", cid=state.cid, arm=state.arm, trigger="proposal" if proposal_detected
                    else "lambda", lambda_jump=lam_jump, lambda_star=cfg.lambda_star,
                    e_jump_loss=e_jump_loss_usd, forgone_rewards=forgone, spread_cost=spread_cost,
                    inventory_before=inv_before, inventory_after=state.inventory, exit_price=px,
                    haircut_paid=spread_cost)

    # --- quote (both arms; λ terms are zero on the OFF arm) ---
    bid, ask = None, None
    try:
        from pricing.quote import compute_quote

        bid, ask = compute_quote(mid, state.inventory, sigma, T_t, lam=lam_jump, e_loss=e_loss,
                                 jump_drift=drift, params=cfg.quote)
        bid = _tick_round(bid, tick_size, "BUY")
        ask = _tick_round(ask, tick_size, "SELL")
        if ask - bid < tick_size:                          # post-only sanity: never inverted
            bid, ask = None, None
    except ValueError:
        pass                                               # boundary regime: skip this tick
    if bid is not None:
        size = max(min_size, cfg.quote_size * (LIGHT_FACTOR if state.defensive else 1.0))
        clob.cancel(list(state.order_ids))
        state.order_ids = [clob.place(state.token_id, "BUY", bid, size, now_ts=now_ts),
                           clob.place(state.token_id, "SELL", ask, size, now_ts=now_ts)]
        if log:
            log("quote", cid=state.cid, arm=state.arm, bid=bid, ask=ask, bid_size=size,
                ask_size=size, replaced=True, order_ids=list(state.order_ids),
                defensive=state.defensive)
        # accrue the SIMULATED reward score (same model as the exit gate — cannot diverge)
        state.sim_reward_score += _reward_score(mid, bid, ask, size, size,
                                                micro.get("max_incentive_spread", 0.0),
                                                micro.get("reward_min_size", 0.0))
    if log:
        log("tick", cid=state.cid, mid=mid, sigma=sigma, T_t=T_t,
            best_bid=book["bids"][0][0], best_ask=book["asks"][0][0],
            inventory=state.inventory, cash=state.cash,
            equity_mark=state.cash + state.inventory * mid,
            sim_reward_score_cum=state.sim_reward_score, quoting=bid is not None)
    state.micro = micro
    return state


def run_loop(markets: list, mode: str = "paper", *, n_ticks: int = 100, interval_s: float = 5.0,
             clob=None, log=None, proposal_detector=None, cfg=None) -> list:
    """The main quoting loop over MarketState objects (built by forwardtest.runner).

    paper/paper-live: fills are SIMULATED locally (PaperClob / PaperLiveClob) — no real orders.
    live is jurisdiction-gated at the clob layer (execution.clob.LiveGateError) and out of v1.
    proposal_detector: injected callable cid -> bool; defaults to always-False — the low-latency
    proposal log-watcher is explicitly v2 (module docstring latency note).
    """
    import time as _t

    if cfg is None:
        from config.loader import load_config

        cfg = load_config()
    if clob is None:
        if mode == "paper":
            from execution.paper import PaperClob

            clob = PaperClob([m.token_id for m in markets])
        elif mode == "paper-live":
            from execution.paper import PaperLiveClob

            clob = PaperLiveClob([m.token_id for m in markets])
        else:
            raise RuntimeError("live mode has no v1 loop adapter — JURISDICTION.md gates it")
    detect = proposal_detector or (lambda cid: False)

    by_token = {m.token_id: m for m in markets}
    for i in range(n_ticks):
        now = _t.time()
        for f in clob.step(now):
            s = by_token.get(f["token_id"])
            if s is None:
                continue
            signed = f["size"] if f["side"] == "BUY" else -f["size"]
            s.inventory += signed
            s.cash -= signed * f["price"]
            if log:
                log("fill", cid=s.cid, arm=s.arm, side=f["side"], price=f["price"], size=f["size"],
                    order_id=f["order_id"], queue_model=f.get("queue_model", "synthetic"),
                    inventory_after=s.inventory, cash_after=s.cash)
        for s in markets:
            book = clob.get_book(s.token_id)
            tick(s, book, now, cfg, clob, log, proposal_detected=detect(s.cid))
        if interval_s > 0 and i < n_ticks - 1:
            _t.sleep(interval_s)
    return markets

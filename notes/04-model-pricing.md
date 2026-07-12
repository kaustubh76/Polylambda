# 04 · Model & pricing

> **Source of truth.** `estimators/{sigma,lambda_engine,hazard,fair_value}.py`, `pricing/quote.py`,
> `execution/loop.py`. Mirrors `quant-implementation-full.excalidraw` panels A–J. `../METHODOLOGY.md`
> has the verified-core narrative.

## 1. The model — implied prob as a jump-diffusion (in log-odds)

Work in log-odds `X = ln(p/(1−p))`, `X ∈ (−∞, +∞)` (so `p` can't escape 0/1):

```
dX = μ·dt  +  σ·dW  +  J·dN
     drift    diffusion   jumps (Poisson, intensity λ)
```

- **μ** drift — small, folded into fair value.
- **σ dW** diffusion — news-driven belief drift ("belief-vol").
- **J dN** jumps — resolution / dispute / shock.

**Load-bearing insight (Panel C):** classic MM assumes you can trade out continuously. On Polymarket a
dispute freezes only *redemption* (~4–6 days); **the CLOB stays open.** So a dispute is a *directional
jump toward 0/1 + thin exit liquidity* (~5c haircut) — **hedgeable at a cost, not a lock.** The engine's
resolution logic therefore *is* the jump term λ (plus a slow market-selection filter), not a screen.

## 2. The three estimators

### σ — belief-volatility (`estimators/sigma.py`)
- **Method:** logit-return **EWMA** `σ²_t = b·σ²_{t-1} + (1−b)·(dX)²`, winsorized for robustness.
  Memory knob `b` (`ewma_b=0.94`): high = smooth/slow, low = twitchy/fast.
- **Hard part:** thin / wash-traded markets → noisy σ. **Fix:** wash-filter (drop self-crosses / sub-min
  prints) then **James-Stein shrinkage** toward a (category × price-bucket) prior (`shrink`, weight
  `n/(n+strength)`); below `min_trades` it uses the prior outright.
- **Role:** drives **spread width**. Under-estimate → picked off; over-estimate → never fill.

### λ — jump intensity = the engine (`estimators/lambda_engine.py` + `hazard.py`)
- **Method:** a hazard / logistic model on **structural** signals → `P(dispute within dt)`.
- `SAFE_FEATURES` = `category_base_rate · market_size · proposer_reliability · latency_anomaly`.
  Subjective "ambiguity" is dropped; "voter concentration" is **excluded** (only known *after* a dispute →
  lookahead leakage). The **deployed** model is size-only (`category_base_rate + market_size`);
  `proposer_reliability` / `latency_anomaly` ship at 0 (a proven null).
- **Two outputs:** `lambda_select` (slow, for selection/sizing) and `lambda_jump` (for the pricing
  premium + exit trigger), plus a **Wilson CI** and `jump_drift` (direction).
- **Jump cost `e_loss`:** directional repricing + a ~5c exit **haircut** (not `lock_days`), weighted by
  inventory downstream.

### fair value — model mid (`estimators/fair_value.py`)
- **Method:** **depth-weighted book mid** (not last trade — wash-prone) + a light **favorite-longshot
  tilt** (`strength=0.02`, tapered near resolution). No lookahead — `t` uses only data known at `t`.
- **Role:** the center your quotes sit around; a biased mid makes both quotes wrong.

## 3. Pricing core — Avellaneda-Stoikov + jump augmentation (`pricing/quote.py`)

All in log-odds, then mapped back to price via an endpoint-sigmoid.

```
reservation:   r = s − q·γ·σ²·(T−t)                         [long q → r below mid → quote lower to shed inventory]
diffusion d:   d = γ·σ²·(T−t) + (2/γ)·ln(1 + γ/k)           [inventory-risk term + liquidity term]
augment:       d_total = d + κ·λ·E[loss|jump]               [jump premium; vanishes when λ low]
skew:          r += κ·λ·jump_drift                          [jumps are DIRECTIONAL — skew r, not just widen d]
quote:         bid = r − d_total/2 ,  ask = r + d_total/2   (in price space, bid < ask)
```

Guards: `T_eff = max(T−t, min_horizon=0.02)` (the `(T−t)→0` floor) and a boundary-tightened inventory cap
near 0/1. The Jacobian `p(1−p)` maps a log-odds half-spread back to price (`price_half_spread_via_jacobian`,
used for intuition/tests; the main path uses the exact sigmoid endpoints).

**Honest caveat (Panel E):** there is no clean closed form for jump-diffusion MM with *directional* jumps
— the jump handling is a **principled, empirically-tuned heuristic**, not a theorem. The diffusion A-S
base is the rigorous part.

## 4. Decisions, all from the model (Panels F/G)

- **QUOTE** — `bid/ask = r ∓ d_total/2`; refresh on σ / λ / inventory / time.
- **SIZE** — ∝ 1/risk (`risk_scale = clamp(σ_ref/σ, size_floor, 1) / (1 + size_lambda_k·λ_jump)`); hard
  time-to-resolution position cap (`pos_cap = base_cap · min(1, (T−t)/horizon)`) → ~0 at resolution.
- **EXIT-ON-RISK** (the defining move) — `execution/loop.py:should_exit`:

  ```
  if (proposal_detected OR lambda_jump > lambda_star)
     and E[jump_loss] > forgone_rewards + spread:
         cancel/replace resting orders
         REDUCE inventory (reduce_fraction=0.5, taker at touch) before the window
         re-quote LIGHTER (light_factor=0.3), not zero, until resolved
  ```

  It is **reward-aware** — it trims only when the expected jump loss beats the liquidity rewards you'd
  forgo by pulling. `E[jump_loss] = |inventory| · e_loss · mid · (1−mid)`. No hard lock — you exit a ~5c
  haircut, not a freeze. `proposal_detected` is a v2 stub today (always-False), so live the trigger is
  `lambda_jump > lambda_star` only.

## 5. Worked example (Panel G, political market, p=0.85, 10 days left)

1. fair value ≈ 0.85 (X≈1.73), small favorite tilt.
2. σ: thin data → shrink to the politics prior → wider spread.
3. λ: reliable proposer, big pot → modest λ but HIGH jump cost (0.85→~0 directional + thin exit), **not**
   a 5-day lock.
4. quote wider than naive; skew as long inventory builds.
5. earn spread + rewards, stay ~flat by design.
6. proposal lands → cancel + flatten near ~0.97 (not the last 3c).
7. market disputes → redemption frozen, exit liquidity thins; the naive maker is stuck on a bad side at a
   haircut, while you are light & liquid, quoting three other markets.

## 6. Limitations (Panel I — read twice)

Model risk is now dominant: bad σ/λ → *systematically* wrong (worse than naive-wrong), and estimation is
weakest exactly where it matters (thin, contested, long-horizon markets). The edge is **conditional on
calibration**. Structural risks (reward normalization erodes APY as TVL grows; custody/audit burden;
CFTC/securities exposure) are deferred, not solved.

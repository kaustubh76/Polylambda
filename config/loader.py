"""
loader — the frozen-config loader: model.yaml -> Config, with env overrides.

model.yaml is the FREEZE-doc (DECISIONS.md #11: freeze before the forward-test; don't tune 5 knobs
on ~0-3 live dispute events). Until now nothing read it — QuoteParams defaults and function kwargs
were the de-facto config. This loader makes the freeze real: precedence is

    dataclass defaults  <  config/model.yaml  <  environment (MODE, MAX_CAPITAL_USDC,
                                                              POSITIONING, LAMBDA_STAR)

pyyaml is deliberately NOT a dependency — model.yaml is repo-controlled flat `key: value` (one
nesting level for the data: block), parsed by the ~20-line _parse_simple_yaml below. Import is
network- and filesystem-free (loading happens only when load_config() is called).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from pricing.quote import QuoteParams

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "model.yaml")

# lambda_star is a CATEGORY DISPUTE BASE RATE threshold (~0.0004-0.021, DATASET.md 5b). Anything
# above this bound is the old 0.15-style scale bug — it would simply never fire (silent no-op λ).
LAMBDA_STAR_SCALE_BOUND = 0.05


@dataclass
class Config:
    quote: QuoteParams = field(default_factory=QuoteParams)
    # sigma
    ewma_b: float = 0.94
    shrinkage_strength: float = 0.5
    min_trades_for_sigma: int = 20
    # lambda / exit
    lambda_star: float = 0.002
    kappa_loss: float = 0.76           # E[loss|jump] scaling, calibrated (data/calibrate.py)
    # execution sizing + inventory (execution/loop.py)
    sigma_ref: float = 0.15            # reference belief-vol for inverse-risk sizing
    size_floor: float = 0.25           # min size multiplier from the sigma term
    size_lambda_k: float = 20.0        # lambda sensitivity of quote size
    inventory_cap_horizon_days: float = 3.0  # position cap ramps over this horizon; ~0 at resolution
    # positioning + mode + risk
    positioning: str = "both"          # reward_farmer | jump_avoid | both
    mode: str = "paper"                # paper | paper-live | testnet | live (env MODE)
    max_capital_usdc: float = 0.0      # hard notional cap for any live order (env MAX_CAPITAL_USDC)
    # risk governor (execution/risk.py — gates every signed tx in testnet mode)
    max_daily_loss_usd: float = 25.0   # halt signing when today's mark-to-market loss exceeds this
    portfolio_gross_cap: float = 200.0 # halt signing when sum(|inventory|) across the fleet exceeds this
    kill_switch_path: str = ".data_cache/risk/KILL"  # file existence = halt (cross-process)
    max_consecutive_errors: int = 5    # RPC/send error breaker: open after N in a row
    max_tx_per_day: int = 200          # signed-tx budget per UTC day
    max_gas_pol_per_day: float = 0.6   # gas budget per UTC day (POL)
    # testnet adapter (execution/testnet_clob.py — gas/spam debounce + reorg buffer)
    min_requote_delta: float = 0.005   # re-post only when bid or ask moved by at least this
    max_quote_age_s: float = 900.0     # ... or the standing quote is older than this
    dispute_confirmations: int = 30    # proposal detector: accept disputes at least this many blocks deep
    # data block
    fill_limit: int = 5000
    control_ratio: int = 3
    # loop knobs (not in model.yaml; env-overridable here for the forward-test)
    quote_size: float = 10.0           # outcome tokens per side
    reduce_fraction: float = 0.5       # inventory fraction taker-reduced on exit
    light_factor: float = 0.3          # re-quote size multiplier while defensive


def _parse_simple_yaml(text: str) -> dict:
    """Flat `key: value` parser with ONE nesting level (the data: block). No pyyaml dep.

    Comments (#...) stripped; scalars coerced int -> float -> str. Only what model.yaml needs.
    """
    out: dict = {}
    section: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        indented = line.startswith((" ", "\t"))
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not indented:
            section = None
        if not val:                       # a block opener like `data:`
            section = key
            out[key] = {}
            continue
        for cast in (int, float):
            try:
                val = cast(val)
                break
            except ValueError:
                continue
        if indented and section:
            out[section][key] = val
        else:
            out[key] = val
    return out


def load_config(path: str = DEFAULT_PATH) -> Config:
    """model.yaml -> Config with env overrides. Raises on the lambda_star scale bug."""
    y: dict = {}
    if os.path.exists(path):
        with open(path) as f:
            y = _parse_simple_yaml(f.read())
    data = y.get("data") or {}

    quote = QuoteParams(
        gamma=float(y.get("gamma", 0.5)),
        k=float(y.get("k", 5.0)),
        kappa=float(y.get("kappa", 1.0)),
        min_horizon=float(y.get("min_horizon", 0.02)),
        boundary_floor=float(y.get("boundary_floor", 0.002)),
        base_inventory_cap=float(y.get("base_inventory_cap", 100.0)),
    )
    cfg = Config(
        quote=quote,
        ewma_b=float(y.get("ewma_b", 0.94)),
        shrinkage_strength=float(y.get("shrinkage_strength", 0.5)),
        min_trades_for_sigma=int(y.get("min_trades_for_sigma", 20)),
        lambda_star=float(y.get("lambda_star", 0.002)),
        kappa_loss=float(y.get("kappa_loss", 0.76)),
        sigma_ref=float(y.get("sigma_ref", 0.15)),
        size_floor=float(y.get("size_floor", 0.25)),
        size_lambda_k=float(y.get("size_lambda_k", 20.0)),
        inventory_cap_horizon_days=float(y.get("inventory_cap_horizon_days", 3.0)),
        positioning=str(y.get("positioning", "both")),
        fill_limit=int(data.get("fill_limit", 5000)),
        control_ratio=int(data.get("control_ratio", 3)),
        max_daily_loss_usd=float(y.get("max_daily_loss_usd", 25.0)),
        portfolio_gross_cap=float(y.get("portfolio_gross_cap", 200.0)),
        kill_switch_path=str(y.get("kill_switch_path", ".data_cache/risk/KILL")),
        max_consecutive_errors=int(y.get("max_consecutive_errors", 5)),
        max_tx_per_day=int(y.get("max_tx_per_day", 200)),
        max_gas_pol_per_day=float(y.get("max_gas_pol_per_day", 0.6)),
        min_requote_delta=float(y.get("min_requote_delta", 0.005)),
        max_quote_age_s=float(y.get("max_quote_age_s", 900.0)),
        dispute_confirmations=int(y.get("dispute_confirmations", 30)),
    )
    # env wins (the runtime switches; model params stay frozen in the yaml)
    cfg.mode = os.environ.get("MODE", cfg.mode)
    cfg.max_capital_usdc = float(os.environ.get("MAX_CAPITAL_USDC", cfg.max_capital_usdc) or 0.0)
    cfg.positioning = os.environ.get("POSITIONING", cfg.positioning)
    if os.environ.get("LAMBDA_STAR"):
        cfg.lambda_star = float(os.environ["LAMBDA_STAR"])

    if cfg.lambda_star > LAMBDA_STAR_SCALE_BOUND:
        raise ValueError(
            f"lambda_star={cfg.lambda_star} is on the wrong scale: the lambda signal is the category "
            f"dispute base rate (~0.0004-0.021, DATASET.md 5b), so a threshold above "
            f"{LAMBDA_STAR_SCALE_BOUND} never fires. The replay's sensitivity grid is 0.0005-0.01."
        )
    if cfg.mode not in ("paper", "paper-live", "testnet", "live"):
        raise ValueError(f"MODE={cfg.mode!r} must be paper | paper-live | testnet | live")
    return cfg

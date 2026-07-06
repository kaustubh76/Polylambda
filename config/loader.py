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
    # positioning + mode + risk
    positioning: str = "both"          # reward_farmer | jump_avoid | both
    mode: str = "paper"                # paper | paper-live | live (env MODE)
    max_capital_usdc: float = 0.0      # hard notional cap for any live order (env MAX_CAPITAL_USDC)
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
        positioning=str(y.get("positioning", "both")),
        fill_limit=int(data.get("fill_limit", 5000)),
        control_ratio=int(data.get("control_ratio", 3)),
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
    if cfg.mode not in ("paper", "paper-live", "live"):
        raise ValueError(f"MODE={cfg.mode!r} must be paper | paper-live | live")
    return cfg

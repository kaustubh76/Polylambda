"""execution/loop.py — the Panel-F execution decisions that were wired in: SIZE ∝ 1/risk, the hard
time-to-resolution inventory cap, and honoring the config knobs (reduce_fraction / light_factor /
shrinkage_strength). Drives tick() directly with a controlled book + a capture clob (offline)."""
from config.loader import load_config
from execution.loop import MarketState, tick
from execution.paper import SIM_MICRO
from estimators.lambda_engine import LambdaOutput


class CaptureClob:
    """Minimal adapter: fixed book, empty tape (so σ == state.sigma_prior), records placed sizes."""

    def __init__(self, book, tape=None):
        self.book = book
        self._tape = tape or []
        self.placed = []

    def get_book(self, token_id):
        return self.book

    def get_micro(self, token_id):
        return dict(SIM_MICRO)

    def tape(self, token_id):
        return self._tape

    def place(self, token_id, side, price, size, *, now_ts=0.0):
        self.placed.append((side, round(size, 4)))
        return f"o{len(self.placed)}"

    def cancel(self, ids):
        pass

    def step(self, now):
        return []


BOOK = {"bids": [(0.49, 200.0), (0.48, 150.0)], "asks": [(0.51, 200.0), (0.52, 150.0)]}


def _lam(lambda_jump=0.0):
    return LambdaOutput(lambda_select=lambda_jump, lambda_jump=lambda_jump,
                        jump_drift=0.0, e_loss=0.0, ci_low=0.0, ci_high=0.0)


def _state(*, sigma_prior=0.15, inventory=0.0, lam=None, T_days=5.0, defensive=False):
    # now=0 in tick → T_t = end_date_ts / 86400 = T_days
    return MarketState(cid="0x", token_id="t", category="politics", arm="lambda_on",
                       end_date_ts=T_days * 86400.0, inventory=inventory, sigma_prior=sigma_prior,
                       lam=lam, defensive=defensive)


def _placed_size(clob, side):
    return next((s for sd, s in clob.placed if sd == side), 0.0)


def test_size_shrinks_with_higher_sigma():
    cfg = load_config()
    quiet, hot = CaptureClob(BOOK), CaptureClob(BOOK)
    tick(_state(sigma_prior=cfg.sigma_ref), BOOK, 0.0, cfg, quiet)     # σ == sigma_ref → full size
    tick(_state(sigma_prior=cfg.sigma_ref * 3), BOOK, 0.0, cfg, hot)   # σ high → smaller size
    assert _placed_size(hot, "BUY") < _placed_size(quiet, "BUY")


def test_size_shrinks_with_higher_lambda():
    cfg = load_config()
    lo, hi = CaptureClob(BOOK), CaptureClob(BOOK)
    tick(_state(lam=_lam(0.0)), BOOK, 0.0, cfg, lo)
    tick(_state(lam=_lam(0.02)), BOOK, 0.0, cfg, hi)   # high jump intensity → smaller size
    assert _placed_size(hi, "BUY") < _placed_size(lo, "BUY")


def test_inventory_cap_blocks_the_growing_side_over_the_cap():
    cfg = load_config()
    cfg.quote.base_inventory_cap = 20.0              # small cap so compute_quote stays well-posed
    clob = CaptureClob(BOOK)
    # far from resolution → full cap (20); inventory over it → BUY blocked, SELL (reducing) still quotes
    tick(_state(inventory=25.0, T_days=cfg.inventory_cap_horizon_days * 2), BOOK, 0.0, cfg, clob)
    assert _placed_size(clob, "BUY") == 0.0          # cannot grow the long past the cap
    assert _placed_size(clob, "SELL") > 0.0          # reducing side still quotes


def test_inventory_cap_tightens_toward_resolution():
    cfg = load_config()
    cfg.quote.base_inventory_cap = 20.0
    inv = 12.0                                       # 60% of the full cap
    far, near = CaptureClob(BOOK), CaptureClob(BOOK)
    tick(_state(inventory=inv, T_days=cfg.inventory_cap_horizon_days * 2), BOOK, 0.0, cfg, far)
    tick(_state(inventory=inv, T_days=cfg.inventory_cap_horizon_days * 0.3), BOOK, 0.0, cfg, near)
    assert _placed_size(far, "BUY") > 0.0            # far out: cap is full → can still add
    assert _placed_size(near, "BUY") == 0.0         # near resolution: cap shrank below inventory → blocked


def test_light_factor_config_is_applied_when_defensive():
    cfg = load_config()
    base, defo = CaptureClob(BOOK), CaptureClob(BOOK)
    tick(_state(sigma_prior=cfg.sigma_ref, defensive=False), BOOK, 0.0, cfg, base)
    tick(_state(sigma_prior=cfg.sigma_ref, defensive=True), BOOK, 0.0, cfg, defo)
    # defensive re-quotes lighter by cfg.light_factor
    assert _placed_size(defo, "BUY") == round(_placed_size(base, "BUY") * cfg.light_factor, 4) or \
           _placed_size(defo, "BUY") < _placed_size(base, "BUY")


def test_shrinkage_strength_config_flows_into_sigma():
    # a non-trivial tape so shrinkage matters; different strength → different σ → different size
    cfg = load_config()
    tape = [{"price": 0.5 + 0.02 * ((i % 7) - 3), "size": 20.0, "maker": "a", "taker": "b"}
            for i in range(60)]
    weak, strong = CaptureClob(BOOK, tape), CaptureClob(BOOK, tape)
    cfg.shrinkage_strength = 0.5
    tick(_state(sigma_prior=0.6), BOOK, 0.0, cfg, weak)
    cfg.shrinkage_strength = 200.0                   # heavy pull toward the 0.6 prior → higher σ → smaller size
    tick(_state(sigma_prior=0.6), BOOK, 0.0, cfg, strong)
    assert _placed_size(strong, "BUY") != _placed_size(weak, "BUY")

"""Published PolyLambda results, baked in so the dashboard renders with ZERO external calls.

Every number here is the honest, published figure from the repo's own docs — used as a graceful
fallback when a live artifact / HF query is unavailable. Sources are cited inline. Services prefer
live-computed values (from the real engine over cached inputs) and fall back to these; the API tags
each payload with `source: "live" | "published"` so the UI is never silently stale.
"""
from __future__ import annotations

# --- one-line product identity (Readme.md:3) --------------------------------------------------
THESIS = (
    "A belief-volatility market-making bot for Polymarket that treats disputes as jumps — "
    "and exits before they lock your capital."
)
# the corrected thesis of record (Readme.md:9-26, DECISIONS.md): a dispute is a directional price
# JUMP with degraded-but-present exit liquidity (~5c haircut), NOT an un-hedgeable lock.
THESIS_NUANCE = (
    "A dispute doesn't freeze the order book — only redemption. So it's a directional price jump "
    "you can model and defend against, not a lock. The engine folds that jump intensity (λ) "
    "straight into the pricing math, and only pulls liquidity when E[jump loss] > forgone rewards."
)
JUMP_DIFFUSION = "dX = μ·dt + σ·dW + J·dN"  # log-odds jump-diffusion (METHODOLOGY.md)

# --- category dispute base rates: THE λ_select signal (DATASET.md §5b) -----------------------
# all adapters, 1,527 disputed markets over HF resolved denominators, Wilson 95% CI.
# rate/ci are fractions (0-1). "politics is ~22× more dispute-prone than crypto."
BASE_RATES_PUBLISHED = [
    {"category": "entertainment", "disputes": 59,  "resolved": 2793,   "rate": 0.0211,   "ci_low": 0.0164,   "ci_high": 0.0272},
    {"category": "politics",      "disputes": 292, "resolved": 15953,  "rate": 0.0183,   "ci_low": 0.0163,   "ci_high": 0.0205},
    {"category": "economics",     "disputes": 13,  "resolved": 1014,   "rate": 0.0128,   "ci_low": 0.0075,   "ci_high": 0.0218},
    {"category": "geopolitics",   "disputes": 73,  "resolved": 8026,   "rate": 0.0091,   "ci_low": 0.0072,   "ci_high": 0.0114},
    {"category": "tech-ai",       "disputes": 54,  "resolved": 10298,  "rate": 0.0052,   "ci_low": 0.0040,   "ci_high": 0.0068},
    {"category": "sports",        "disputes": 150, "resolved": 87854,  "rate": 0.0017,   "ci_low": 0.0015,   "ci_high": 0.0020},
    {"category": "other",         "disputes": 742, "resolved": 563325, "rate": 0.0013,   "ci_low": 0.0012,   "ci_high": 0.0014},
    {"category": "crypto",        "disputes": 144, "resolved": 170446, "rate": 0.00085,  "ci_low": 0.00072,  "ci_high": 0.00099},
]

# HF resolved-market denominators per category (the λ DENOMINATOR). Fallback for the live
# `data.base_rates.category_counts_hf()` when the HF cache/query is unavailable — lets the real
# `estimate_lambda` run fully offline. n_markets ≈ n_resolved for display purposes here.
BASE_RATE_COUNTS_FALLBACK = {
    r["category"]: {"n_markets": r["resolved"], "n_resolved": r["resolved"]}
    for r in BASE_RATES_PUBLISHED
}
# released-parquet disputed-market NUMERATOR by category (matches DATASET.md §5b).
DISPUTE_COUNTS_FALLBACK = {r["category"]: r["disputes"] for r in BASE_RATES_PUBLISHED}

# --- the primary edge proof: the λ* ablation (METHODOLOGY.md §5b″ / DATASET.md §5b″) -----------
# powered replay: 1,409 disputed + 2,856 matched controls, all adapters, 2022-2026, net of forgone
# rewards. pnl in USD, sharpe unitless. "surgical exit > blanket avoidance."
ABLATION_META = {"n_disputes": 1409, "n_controls": 2856, "lambda_star_frozen": 0.002,
                 "span": "2022-2026", "adapters": "all"}
ABLATION_PUBLISHED = [
    # arm, lambda_star, pnl_net_of_rewards, sharpe
    {"arm": "lambda_jump",   "lambda_star": 0.0005, "pnl_net_of_rewards": 46975.0, "sharpe": 0.334},
    {"arm": "lambda_jump",   "lambda_star": 0.002,  "pnl_net_of_rewards": 41976.0, "sharpe": 0.289},
    {"arm": "lambda_jump",   "lambda_star": 0.01,   "pnl_net_of_rewards": 41545.0, "sharpe": 0.286},
    {"arm": "diffusion_only", "lambda_star": 0.0005, "pnl_net_of_rewards": 40065.0, "sharpe": 0.274},
    {"arm": "diffusion_only", "lambda_star": 0.002,  "pnl_net_of_rewards": 40065.0, "sharpe": 0.274},
    {"arm": "diffusion_only", "lambda_star": 0.01,   "pnl_net_of_rewards": 40065.0, "sharpe": 0.274},
    {"arm": "lambda_select", "lambda_star": 0.0005, "pnl_net_of_rewards": 0.0,     "sharpe": 0.000},
    {"arm": "lambda_select", "lambda_star": 0.002,  "pnl_net_of_rewards": 23912.0, "sharpe": 0.195},
    {"arm": "lambda_select", "lambda_star": 0.01,   "pnl_net_of_rewards": 29459.0, "sharpe": 0.226},
]
ARM_LABELS = {
    "lambda_jump":   "λ-jump · reward-aware surgical exit",
    "diffusion_only": "diffusion · always hold",
    "lambda_select": "λ-select · blanket avoid",
}

# --- dataset headline (dataset_release/.../stats.json) — fallback if the file is missing ---------
DATASET_STATS_FALLBACK = {
    # total_disputes is the SHIPPED count (the layer runs to chain head); in_window_disputes is what
    # the λ base rates are actually computed on (the HF denominator is frozen at HF_CUTOFF_TS). They
    # diverge by design — never substitute one for the other.
    "total_disputes": 1848, "in_window_disputes": 1794, "hf_joinable_pct": 100.0,
    # The 108→110 rows are keyed by the adapter's RAW ADDRESS, matching the release. They were
    # previously labelled "legacy" here, which is wrong twice over: real `legacy`
    # (0x71392e13…) contributes 0 rows, and this adapter is a distinct contract. The release stores
    # the raw string; renaming it here only hides the mismatch.
    "by_adapter": {"v2": 725, "negrisk": 1013, "0x157ce2d672854c848c9b79c49a8cc6cc89176a49": 110},
    "by_category_joinable": {"other": 857, "politics": 394, "sports": 160, "crypto": 154,
                             "geopolitics": 110, "tech-ai": 73, "entertainment": 66,
                             "economics": 24, "null": 10},
    "by_year": {"2022": 1, "2023": 75, "2024": 394, "2025": 1049, "2026": 329},
    "date_min": "2022-12-30", "date_max": "2026-07-16",
    # Deterministic recon over the HF-aligned universe (the local indexer is processed to ~the HF
    # cutoff, so this covers exactly the HF-comparable set). eligible/matched/no_ground_truth are stable
    # across runs since recon/check.py added `order_by` — the old 28,482 was one draw of an unordered
    # limit/offset scan. recon needs an indexer to recompute; this block is the last such result.
    "recon": {"pass_rate": 1.0, "eligible": 27238, "matched": 27238, "no_ground_truth": 115221},
}

# the honest calibration caveat that must ride alongside the hazard AUC (DECISIONS.md #9).
HAZARD_CAVEAT = (
    "Disputes are ~1% of markets → CALIBRATION-LIMITED. Headline is held-out AUC "
    "(discrimination). The deployed model is size-only; proposer-reputation is a proven null once "
    "liquidity is matched. This is a directional structural signal, NOT a validated edge over the "
    "category base rate — the base rate remains the honest default."
)

# frozen model params (config/model.yaml) surfaced for the UI even if the loader can't read yaml.
FROZEN_PARAMS_FALLBACK = {
    "gamma": 0.5, "k": 5.0, "kappa": 1.0, "lambda_star": 0.002, "kappa_loss": 0.76,
    "ewma_b": 0.94, "sigma_ref": 0.15, "positioning": "both",
}

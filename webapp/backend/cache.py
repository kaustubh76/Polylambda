"""Artifact loaders + offline dependency-injection for the dashboard backend.

Two jobs:
  1. Load the real shipped artifacts (stats.json, hazard_model*.json, sigma_prior.json,
     disputes.parquet) with graceful fallbacks to `constants.py` published values.
  2. `install_offline_di()` — inject the cached HF category denominators into `data.base_rates`
     so the REAL `estimators.lambda_engine.estimate_lambda` runs fully offline (no HF scan per
     request). We replace the module-level `category_counts_hf` with a cached-returning function;
     `estimate_lambda` still owns all the λ math — we only feed it precomputed inputs.
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

from . import constants as K

# repo root = webapp/backend/cache.py -> parents[2]. Ensure the real engine imports resolve even if
# uvicorn is launched from elsewhere.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_CACHE = PROJECT_ROOT / ".data_cache"
WEBAPP_CACHE = DATA_CACHE / "webapp"
RELEASE_DIR = PROJECT_ROOT / "dataset_release" / "polymarket-oov2-disputes-v1"
DISPUTES_PARQUET = RELEASE_DIR / "disputes.parquet"

# keep any accidental HF access offline + quiet (defense in depth; DI should prevent it entirely).
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def _load_json(path: Path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# shipped artifacts (with fallbacks)
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def dataset_stats() -> dict:
    return _load_json(RELEASE_DIR / "stats.json") or dict(K.DATASET_STATS_FALLBACK)


def _with_trained_at(m, path: Path):
    """Ensure a card carries a `trained_at` date. New artifacts embed it at train time
    (estimators.hazard.save_hazard_model); older ones fall back to the file's mtime so the UI can
    always show honest 'trained on <date>' provenance instead of an undated frozen number."""
    if not m:
        return m
    if not m.get("trained_at"):
        try:
            from datetime import datetime, timezone
            m = {**m, "trained_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                 .strftime("%Y-%m-%d")}
        except Exception:
            pass
    return m


@lru_cache(maxsize=1)
def hazard_models() -> dict:
    """The three hazard model cards: deployed, matched-fair-controls, and the matched eval (null).
    Each is stamped with a `trained_at` date (embedded, or mtime fallback) for UI provenance."""
    return {
        "deployed": _with_trained_at(_load_json(DATA_CACHE / "hazard_model.json"),
                                     DATA_CACHE / "hazard_model.json"),
        "matched": _with_trained_at(_load_json(DATA_CACHE / "hazard_model_matched.json"),
                                    DATA_CACHE / "hazard_model_matched.json"),
        "matched_eval": _with_trained_at(_load_json(DATA_CACHE / "hazard_eval_matched.json"),
                                         DATA_CACHE / "hazard_eval_matched.json"),
    }


@lru_cache(maxsize=1)
def sigma_prior() -> list:
    return _load_json(DATA_CACHE / "sigma_prior.json") or []


@lru_cache(maxsize=1)
def base_rate_counts() -> tuple[dict, str]:
    """HF resolved-market denominators per category. (counts, source)."""
    cached = _load_json(WEBAPP_CACHE / "base_rate_counts.json")
    if cached:
        return cached, "live"
    return dict(K.BASE_RATE_COUNTS_FALLBACK), "published"


@lru_cache(maxsize=1)
def dispute_counts_by_category() -> tuple[dict, str]:
    """Disputed-market NUMERATOR by category, from the real released-parquet loader (offline)."""
    try:
        from data.disputes import dispute_counts_by_category as _real
        counts = _real()
        if counts:
            return dict(counts), "live"
    except Exception:
        pass
    return dict(K.DISPUTE_COUNTS_FALLBACK), "published"


@lru_cache(maxsize=1)
def disputes_by_proposer() -> dict:
    """{proposer_lower: total dispute count} for the live proposer-reliability feature."""
    cached = _load_json(WEBAPP_CACHE / "disputes_by_proposer.json")
    if cached:
        return cached
    try:
        from estimators.hazard import _disputes_by_proposer
        return _disputes_by_proposer()
    except Exception:
        return {}


@lru_cache(maxsize=1)
def dispute_names() -> dict:
    """{conditionId: {marketName, marketSlug}} enrichment for the disputes explorer (optional)."""
    return _load_json(WEBAPP_CACHE / "dispute_names.json") or {}


@lru_cache(maxsize=1)
def hf_overview() -> dict:
    """HF backbone overview (resolution mix, markets-by-year, category counts, coverage). {} if absent."""
    return _load_json(WEBAPP_CACHE / "hf_overview.json") or {}


@lru_cache(maxsize=1)
def hf_markets() -> dict:
    """Recent-markets browser payload from HF. {markets:[...], n} — {} if the cache is absent."""
    return _load_json(WEBAPP_CACHE / "hf_markets.json") or {}


@lru_cache(maxsize=1)
def dispute_market_context() -> dict:
    """{conditionId: {category, startDate, endDate, resolved, resolvedOutcome}} HF context for the
    disputed markets — enriches the disputes explorer. {} if absent."""
    return _load_json(WEBAPP_CACHE / "dispute_market_context.json") or {}


@lru_cache(maxsize=1)
def disputes_df():
    """The released dispute dataset as a pandas DataFrame (name-enriched).

    1,848 rows to chain head; 1,794 of them inside the HF window. This is the DISPLAY path, so it
    carries all of them — only the λ numerator (data.disputes.load_disputes) applies the window."""
    import pandas as pd

    if not DISPUTES_PARQUET.exists():
        return pd.DataFrame()
    df = pd.read_parquet(DISPUTES_PARQUET)
    names = dispute_names()
    if names:
        df["marketName"] = df["conditionId"].map(lambda c: (names.get(c) or {}).get("marketName"))
        df["marketSlug"] = df["conditionId"].map(lambda c: (names.get(c) or {}).get("marketSlug"))
    return df


_ARTIFACT_LOADERS = (dataset_stats, hazard_models, sigma_prior, base_rate_counts,
                     dispute_counts_by_category, disputes_by_proposer, dispute_names, disputes_df,
                     hf_overview, hf_markets, dispute_market_context)


def refresh() -> None:
    """Bust every @lru_cache artifact loader so a scheduled regenerate (data.export_disputes +
    precompute + retrain) is picked up WITHOUT a process restart. Called by /api/admin/refresh
    and safe to call anytime — the next access re-reads from disk."""
    for fn in _ARTIFACT_LOADERS:
        try:
            fn.cache_clear()
        except Exception:
            pass
    load_hazard_model.cache_clear()


# ---------------------------------------------------------------------------------------------
# offline DI — make the REAL estimate_lambda network-free
# ---------------------------------------------------------------------------------------------
_DI_INSTALLED = False


def install_offline_di() -> str:
    """Patch data.base_rates.category_counts_hf -> cached denominators. Idempotent.

    Returns the resolved source ("live" cache or "published" fallback). After this, every
    `estimate_lambda` / `category_base_rate` call that would otherwise scan HF uses the cache.
    """
    global _DI_INSTALLED
    counts, source = base_rate_counts()
    try:
        import data.base_rates as br
        br.category_counts_hf = lambda: counts  # dependency injection, not logic replacement
        _DI_INSTALLED = True
    except Exception:
        pass
    return source


@lru_cache(maxsize=1)
def load_hazard_model():
    """The real LoadedHazard predictor (offline; no sklearn/pickle). None if the JSON is missing."""
    try:
        from estimators.hazard import load_hazard_model as _load
        return _load()
    except Exception:
        return None


def frozen_config() -> tuple[dict, str]:
    """config/model.yaml frozen params via the real loader; fallback to published constants.

    Exposes the full knob set (not just the headline nine) so the UI can render the complete
    frozen strategy card.
    """
    try:
        from config.loader import load_config
        cfg = load_config()
        return {
            # quote (Avellaneda–Stoikov) params
            "gamma": cfg.quote.gamma, "k": cfg.quote.k, "kappa": cfg.quote.kappa,
            "min_horizon": cfg.quote.min_horizon, "boundary_floor": cfg.quote.boundary_floor,
            "base_inventory_cap": cfg.quote.base_inventory_cap,
            # signal / exit
            "lambda_star": cfg.lambda_star, "kappa_loss": cfg.kappa_loss,
            # sigma estimator
            "ewma_b": cfg.ewma_b, "sigma_ref": cfg.sigma_ref,
            "shrinkage_strength": cfg.shrinkage_strength, "min_trades_for_sigma": cfg.min_trades_for_sigma,
            # sizing / inventory
            "quote_size": cfg.quote_size, "reduce_fraction": cfg.reduce_fraction,
            "light_factor": cfg.light_factor, "size_floor": cfg.size_floor,
            "size_lambda_k": cfg.size_lambda_k, "inventory_cap_horizon_days": cfg.inventory_cap_horizon_days,
            # data / run
            "control_ratio": cfg.control_ratio, "fill_limit": cfg.fill_limit,
            "positioning": cfg.positioning, "mode": cfg.mode,
        }, "live"
    except Exception:
        return dict(K.FROZEN_PARAMS_FALLBACK), "published"

# PolyLambda — MVP Dashboard

A polished, local **quant-terminal dashboard wired to the real PolyLambda engine**. Nothing here is
a mockup: every figure is either computed live by the actual estimator / execution / forward-test
code, or read from a shipped artifact. The paper engine is deterministic and network-free.

```
webapp/
  backend/          FastAPI app that imports the real modules (paper-mode only)
    main.py         app assembly · CORS · serves the built SPA · lifespan installs the offline DI
    routes.py       9 JSON endpoints → services.py
    services.py     thin wrappers around estimate_lambda / compute_quote / runner.run / …
    scenario.py     the dispute-defense A/B (real tick()/should_exit() + injected gap)
    cache.py        artifact loaders + offline dependency-injection
    precompute.py   one-time cache builder (base-rate denominators, proposer history, market names)
    constants.py    published-result fallbacks (DATASET.md §5b, METHODOLOGY.md §5b″) → zero-network
  frontend/         Vite + React + TS + Tailwind + Recharts SPA (dark quant-terminal theme)
```

## Run it (local)

From the **repo root**, in the project `.venv`:

```bash
pip install -r requirements.txt            # adds fastapi + uvicorn (one-time)
python -m webapp.backend.precompute        # one-time; builds .data_cache/webapp/* (falls back gracefully)
```

**Two-process dev** (hot reload):

```bash
# terminal 1 — API on :8000
uvicorn webapp.backend.main:app --reload --port 8000
# terminal 2 — Vite dev server on :5173 (proxies /api → :8000)
cd webapp/frontend && npm install && npm run dev
# open http://localhost:5173
```

**Single-process demo** (build the SPA; FastAPI serves it):

```bash
cd webapp/frontend && npm install && npm run build
uvicorn webapp.backend.main:app --port 8000     # from repo root
# open http://localhost:8000
```

## What each panel is wired to

| Panel | Real code / artifact it calls |
|---|---|
| **Overview** | `config.loader.load_config` · `stats.json` · `hazard_model.json` |
| **λ signal — base rates** | `data.base_rates.category_base_rate` + `data.disputes.dispute_counts_by_category` |
| **Score a market** | `estimators.hazard.*` → `estimators.lambda_engine.estimate_lambda` → `pricing.quote.compute_quote` → `execution.loop.should_exit` |
| **Paper engine** | `forwardtest.runner.run` (raw quoting) · real `execution.loop.tick`/`should_exit` (dispute defense) |
| **Edge proof — ablation** | published `forwardtest.replay_ablation` powered result (`AblationResult`) |
| **Model card** | `hazard_model.json` + `hazard_model_matched.json` + `hazard_eval_matched.json` |
| **Disputes** | `dataset_release/…/disputes.parquet` + cached market names |
| **Integrity** | `stats.json` recon block |
| **σ surface** | `sigma_prior.json` |

## Offline & honest by construction

- **Offline DI:** `cache.install_offline_di()` injects cached HF denominators into
  `data.base_rates`, so the real `estimate_lambda` runs with no network per request. If a cache is
  missing, services fall back to the published constants and tag the payload `source: "published"`.
- **Paper only:** the gated CLOB write path (`execution.clob.place_order`) is never imported. Every
  simulated figure is stamped `simulated: true`. The dispute-defense scenario is a clearly-labelled
  illustration of the exit mechanism; the powered statistical claim lives in the ablation panel.

## Test

```bash
pytest tests/test_webapp.py -q      # 10 end-to-end tests, fully offline
```

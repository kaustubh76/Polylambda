"""PolyLambda MVP dashboard — a thin, read-only web layer over the real engine.

`webapp.backend` is a FastAPI app that imports the actual estimators / execution / forward-test
modules and renders the real shipped artifacts. It is PAPER-mode only and never imports the gated
CLOB write path (execution.clob.place_order). See webapp/README.md.
"""

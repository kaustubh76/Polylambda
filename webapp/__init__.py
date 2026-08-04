"""PolyLambda MVP dashboard — a thin, read-only web layer over the real engine.

`webapp.backend` is a FastAPI app that imports the actual estimators / execution / forward-test
modules and renders the real shipped artifacts. It surfaces read-only analytics plus the live on-chain
testnet keeper (Polygon Amoy); the gated MAINNET CLOB write path (execution.clob.place_order) is never
imported. See webapp/README.md.
"""

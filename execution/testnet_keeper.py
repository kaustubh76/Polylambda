"""testnet_keeper — the continuous testnet execution engine (the long-lived runtime).

Wires the REAL production loop (execution/loop.py tick/run_loop — byte-identical to paper mode)
to the Amoy fleet through the TestnetClob adapter:

    estimators (λ via estimate_lambda, σ prior) -> MarketState fleet
    -> run_loop(mode="testnet", clob=TestnetClob, proposal_detector=ConfirmedProposalDetector)
    -> engine-signed postQuote / flagDispute on Polygon Amoy, RiskGovernor-gated
    -> honest session logs: mode="testnet", simulated=False, fills carry tx hashes

Defense chain: a confirmed live dispute (or the manual trigger file) fires the loop's reward-aware
exit gate; the `exit` log record triggers clob.flag_dispute_for(cid) — a REAL flagDispute() that
halts buys on that market — and tick() re-quotes light (state.defensive / light_factor).

reduce_fraction is forced to 0.0 in this mode: the exit's taker-reduce has no counterparty on the
demo contract (the engine cannot trade against its own quote), so pretending to fill it would be
paper-mode dishonesty. The on-chain defense IS flagDispute + light re-quote; inventory/cash stay
exactly reconcilable against Traded events.

Runtimes: (a) CLI `python -m execution.testnet_keeper --ticks 60 --interval 60`; (b) background
thread in the webapp (KEEPER_AUTOSTART=1, webapp/backend/main.py) — one process with the demo
routes so the signer's nonce lock is global; (c) burst endpoint POST /api/testnet/keeper/run for
the GH-cron watchdog.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time

_DAY = 86400.0


def _mark_mid(book: dict) -> float | None:
    """The on-chain mid, or None when there is no standing quote (undeployed / resolved / inverted).
    None is honest: callers must NOT substitute a placeholder — a fabricated mark would pollute the
    equity / P&L / daily-loss numbers. Inventory that can't be marked is simply left un-marked
    (in practice inventory only moves via real fills on a market that therefore has a live quote)."""
    if book.get("bids") and book.get("asks"):
        return 0.5 * (book["bids"][0][0] + book["asks"][0][0])
    return None


class TestnetKeeper:
    __test__ = False  # not a pytest class, despite the name

    def __init__(self, cfg=None, *, fleet_path: str | None = None, interval_s: float = 60.0,
                 out_path: str | None = None, clob=None, detector=None, risk=None,
                 horizon_days: float = 14.0):
        self.cfg = cfg
        self.fleet_path = fleet_path
        self.interval_s = interval_s
        self.out_path = out_path
        self.clob = clob
        self.detector = detector
        self.risk = risk
        self.horizon_days = horizon_days
        self.markets: list | None = None
        self.ticks_done = 0
        self.last_tick_ts: float = 0.0
        self.last_error: str = ""
        self._fh = None
        self._started_log = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()          # one run() at a time (bursts vs background)

    # -- lazy construction ------------------------------------------------------------------------
    def _ensure(self) -> None:
        if self.cfg is None:
            from config.loader import load_config
            self.cfg = load_config()
        # the exit's taker-reduce has no on-chain counterparty — flagDispute + light re-quote IS
        # the defense; a fictional reduce fill would desync inventory from Traded events
        self.cfg.reduce_fraction = 0.0
        if self.risk is None:
            from execution.risk import RiskGovernor, RiskLimits
            self.risk = RiskGovernor(RiskLimits.from_config(self.cfg))
        if self.clob is None:
            from execution.testnet_chain import AmoySigner, ChainReader, load_fleet, make_w3
            from execution.testnet_clob import TestnetClob
            fleet, abi = load_fleet(self.fleet_path)
            managed = [m for m in fleet if m.keeper_managed]
            if not managed:
                raise RuntimeError("no keeper_managed markets in the fleet registry — "
                                   "run scripts/deploy_fleet.py first")
            w3 = make_w3()
            self.clob = TestnetClob(managed, AmoySigner(w3), ChainReader(w3, abi),
                                    risk=self.risk,
                                    min_requote_delta=self.cfg.min_requote_delta,
                                    max_quote_age_s=self.cfg.max_quote_age_s)
        if self.detector is None:
            from execution.proposal_feed import ConfirmedProposalDetector
            managed = [mb.m for mb in self.clob.by_token.values() if mb.m.keeper_managed]
            self.detector = ConfirmedProposalDetector(
                managed, confirmations=self.cfg.dispute_confirmations)
        if self.markets is None:
            self.markets = self.build_markets()

    def build_markets(self) -> list:
        """MarketState fleet from the registry + REAL estimators (runner.build_markets pattern)."""
        from estimators.lambda_engine import estimate_lambda
        from execution.loop import MarketState

        dispute_counts = None
        try:
            from data.disputes import dispute_counts_by_category
            dispute_counts = dispute_counts_by_category()
        except Exception as e:  # noqa: BLE001 — degrade to estimate_lambda's real defaults, but LOUDLY
            self._log("degraded", reason="dispute_counts_unavailable", detail=str(e)[:200])
        sigma_corpus = None
        try:
            from data.prior_corpus import load_sigma_prior
            sigma_corpus = load_sigma_prior()
        except Exception as e:  # noqa: BLE001 — σ falls back to cfg.sigma_ref (a real configured prior)
            self._log("degraded", reason="sigma_corpus_unavailable", detail=str(e)[:200])

        now = time.time()
        markets = []
        managed = [mb.m for mb in self.clob.by_token.values() if mb.m.keeper_managed]
        for i, fm in enumerate(managed):
            book = self.clob.get_book(fm.token_id)
            # estimator INPUT price: the on-chain mid, or the max-entropy prior (0.5) for a market
            # with no standing quote yet. This is a model input (the estimator output stays real) —
            # distinct from the reported equity MARK, which never uses a placeholder (see _mark_mid).
            price = _mark_mid(book)
            if price is None:
                price = 0.5
            cid = fm.tracks_cid or fm.token_id
            features = {"category": fm.category, "price": price}
            lam = estimate_lambda(cid, features, dispute_counts=dispute_counts,
                                  kappa_loss=self.cfg.kappa_loss)
            sig_prior = self.cfg.sigma_ref
            if sigma_corpus:
                try:
                    from estimators.sigma import category_price_prior
                    sig_prior = category_price_prior(sigma_corpus, fm.category, price)
                except Exception:  # noqa: BLE001
                    pass
            arm = "lambda_on" if i % 2 == 0 else "lambda_off"
            end_ts = fm.end_date_ts if fm.end_date_ts > now else now + self.horizon_days * _DAY
            markets.append(MarketState(cid=cid, token_id=fm.token_id, category=fm.category,
                                       arm=arm, end_date_ts=end_ts, sigma_prior=sig_prior,
                                       lam=lam if arm == "lambda_on" else None))
            # λ/σ display fields emitted on-chain with every postQuote (0 on the off arm: honest)
            self.clob.set_display(fm.token_id,
                                  int(round(lam.lambda_jump * 10000)) if arm == "lambda_on" else 0,
                                  int(round(sig_prior * 10000)))
        return markets

    # -- session logging --------------------------------------------------------------------------
    def _log_fh(self):
        from forwardtest import session_log
        if self._fh is None or self._fh.closed:
            path = self.out_path or os.path.join(
                ".data_cache", "sessions",
                f"session-testnet-{time.strftime('%Y%m%d', time.gmtime())}.jsonl")
            self._fh = session_log.open_log(path)
            self.out_path = path
        return self._fh

    def _log(self, record_type: str, **fields):
        from forwardtest import session_log
        rec = session_log.append(self._log_fh(), record_type, mode="testnet",
                                 simulated=False, **fields)
        if record_type == "exit" and fields.get("trigger") == "proposal":
            # THE defense: a CONFIRMED dispute on the tracked market signs a real flagDispute().
            # λ-only exits (elevated hazard, no detected dispute) go defensive/light but must NOT
            # burn the market — flagDispute is irreversible until resolve, and with zero Amoy
            # rewards the λ gate fires readily.
            out = self.clob.flag_dispute_for(fields["cid"])
            if out is not None:
                session_log.append(self._log_fh(), "dispute_flagged", mode="testnet",
                                   simulated=False, cid=fields["cid"], **out)
        return rec

    def _session_start(self) -> None:
        if self._started_log:
            return
        self._started_log = True
        self._log("session_start",
                  config={"lambda_star": self.cfg.lambda_star, "quote_size": self.cfg.quote_size,
                          "positioning": self.cfg.positioning, "gamma": self.cfg.quote.gamma,
                          "k": self.cfg.quote.k, "kappa": self.cfg.quote.kappa,
                          "reduce_fraction": self.cfg.reduce_fraction,
                          "min_requote_delta": self.cfg.min_requote_delta,
                          "max_quote_age_s": self.cfg.max_quote_age_s},
                  arm_rule="lambda_on evaluates the reward-aware exit gate; lambda_off never does",
                  interval_s=self.interval_s,
                  markets=[{"cid": m.cid, "token_id": m.token_id, "category": m.category,
                            "end_date_ts": m.end_date_ts, "arm": m.arm,
                            "lambda_select": (m.lam.lambda_select if m.lam else None),
                            "lambda_jump": (m.lam.lambda_jump if m.lam else None),
                            "ci_low": (m.lam.ci_low if m.lam else None),
                            "ci_high": (m.lam.ci_high if m.lam else None),
                            "micro": self.clob.get_micro(m.token_id)} for m in self.markets])

    def _rollup(self) -> dict:
        per_market, per_arm = [], {}
        for m in self.markets:
            mid = _mark_mid(self.clob.get_book(m.token_id))
            # no live quote → mark only cash (never a placeholder mid); inventory stays un-marked
            equity = m.cash + (m.inventory * mid if mid is not None else 0.0)
            per_market.append({"cid": m.cid, "token_id": m.token_id, "arm": m.arm,
                               "category": m.category, "inventory": m.inventory, "cash": m.cash,
                               "mark_mid": mid, "equity_mark": equity, "pnl": equity,
                               "n_exits": m.n_exits})
            a = per_arm.setdefault(m.arm, {"n_markets": 0, "equity_mark": 0.0, "pnl": 0.0,
                                           "cash": 0.0, "inventory": 0.0, "n_exits": 0})
            a["n_markets"] += 1
            a["equity_mark"] += equity
            a["pnl"] += equity
            a["cash"] += m.cash
            a["inventory"] += m.inventory
            a["n_exits"] += m.n_exits
        return {"per_market": per_market, "per_arm_totals": per_arm}

    # -- the run loop -----------------------------------------------------------------------------
    def run(self, n_ticks: int = 10, *, stop_event: threading.Event | None = None) -> dict:
        """Run N ticks of the production loop (one run_loop call per tick so a stop is responsive).

        Safe to call repeatedly: MarketState (inventory/cash) persists across bursts, the session
        file accumulates one continuous day-log, and the debounce means an unchanged quote signs
        nothing. Returns the session rollup.
        """
        from execution.loop import run_loop

        with self._lock:
            self._ensure()
            self._session_start()
            stop = stop_event or self._stop
            for i in range(n_ticks):
                if stop.is_set():
                    break
                try:
                    run_loop(self.markets, mode="testnet", n_ticks=1, interval_s=0.0,
                             clob=self.clob, log=self._log, proposal_detector=self.detector,
                             cfg=self.cfg, start_ts=None)
                    self.last_error = ""
                except Exception as e:  # noqa: BLE001 — a tick must never kill the keeper
                    self.last_error = str(e)[:300]
                    self.risk.record_error(f"tick: {e}")
                self.ticks_done += 1
                self.last_tick_ts = time.time()
                self.risk.mark_equity(sum(
                    m.cash + (m.inventory * mid if (mid := _mark_mid(self.clob.get_book(m.token_id)))
                              is not None else 0.0)
                    for m in self.markets))
                if i < n_ticks - 1 and self.interval_s > 0:
                    stop.wait(self.interval_s)
            roll = self._rollup()
            self._log("session_end", **roll,
                      n_disputes_witnessed=sum(m.n_exits for m in self.markets),
                      ticks_done=self.ticks_done)
            return {"mode": "testnet", "ticks_done": self.ticks_done,
                    "out_path": self.out_path, **roll}

    # -- background runtime -----------------------------------------------------------------------
    def start_background(self, *, burst_ticks: int = 10_000) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()

        def _target():
            try:
                self.run(n_ticks=burst_ticks)
            except Exception as e:  # noqa: BLE001 — boot failure must surface in status, not stderr
                self.last_error = str(e)[:300]

        self._thread = threading.Thread(target=_target, name="testnet-keeper", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout)
        return not (t and t.is_alive())

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        from execution.testnet_chain import engine_key
        st = {"running": self.running, "ticks_done": self.ticks_done,
              "last_tick_ts": self.last_tick_ts, "interval_s": self.interval_s,
              "out_path": self.out_path, "last_error": self.last_error,
              "n_markets": len(self.markets) if self.markets else 0,
              # why the live keeper is (not) signing — both must be true in prod:
              "autostart": os.environ.get("KEEPER_AUTOSTART") == "1",
              "engine_ready": engine_key() is not None}
        if self.risk is not None:
            st["risk"] = self.risk.status()
        if self.clob is not None:
            st["clob"] = self.clob.status()
            signer = getattr(self.clob, "signer", None)
            reader = getattr(self.clob, "reader", None)
            if signer is not None and reader is not None and getattr(signer, "address", None):
                try:
                    st["engine"] = {"address": signer.address, **reader.balances(signer.address)}
                except Exception as e:  # noqa: BLE001
                    st["engine"] = {"address": signer.address, "error": str(e)[:120]}
        if self.detector is not None and hasattr(self.detector, "status"):
            st["detector"] = self.detector.status()
        if self.markets:
            st["markets"] = self._rollup()["per_market"]
        return st


# module singleton for the webapp (one keeper per process — the nonce-lock argument)
_keeper: TestnetKeeper | None = None
_keeper_lock = threading.Lock()


def get_keeper() -> TestnetKeeper:
    global _keeper
    with _keeper_lock:
        if _keeper is None:
            _keeper = TestnetKeeper(interval_s=float(os.environ.get("KEEPER_INTERVAL_S", "60")))
        return _keeper


def main() -> None:
    ap = argparse.ArgumentParser(description="PolyLambda testnet keeper (Polygon Amoy)")
    ap.add_argument("--ticks", type=int, default=10)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--fleet", default=None, help="markets.json path (default: webapp registry)")
    ap.add_argument("--out", default=None, help="session log path (default: daily testnet file)")
    args = ap.parse_args()
    k = TestnetKeeper(fleet_path=args.fleet, interval_s=args.interval, out_path=args.out)
    summary = k.run(n_ticks=args.ticks)
    print(json.dumps({**summary, "risk": k.risk.status() if k.risk else None}, indent=2))


if __name__ == "__main__":
    main()

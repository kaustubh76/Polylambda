"""risk — the RiskGovernor: hard limits between the trading loop and the engine's hot wallet.

ROADMAP Phase 3, built for testnet mode. Consulted by the TestnetClob immediately before EVERY
signed transaction (adapter-level, not in loop.tick — the loop stays pure/offline and no signing
path can bypass risk, because on testnet the tx IS the order).

Halts (allow_tx -> (False, reason); the keeper keeps ticking but posts nothing):
  - kill-switch file exists (cross-process: the webapp endpoint or a manual `touch` both work)
  - today's mark-to-market loss exceeds max_daily_loss_usd
  - signed-tx or gas budget for the UTC day exhausted
  - portfolio gross exposure over portfolio_gross_cap
  - error breaker open (max_consecutive_errors RPC/send failures in a row; closes on success)

State is a JSONL ledger under .data_cache/risk/ledger-YYYYMMDD.jsonl, replayed on construction so
tx/gas counters and the day's opening equity survive restarts. Clock is injected for tests.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RiskLimits:
    max_daily_loss_usd: float = 25.0
    portfolio_gross_cap: float = 200.0
    kill_switch_path: str = ".data_cache/risk/KILL"
    max_consecutive_errors: int = 5
    max_tx_per_day: int = 200
    max_gas_pol_per_day: float = 0.6

    @classmethod
    def from_config(cls, cfg) -> "RiskLimits":
        return cls(max_daily_loss_usd=cfg.max_daily_loss_usd,
                   portfolio_gross_cap=cfg.portfolio_gross_cap,
                   kill_switch_path=cfg.kill_switch_path,
                   max_consecutive_errors=cfg.max_consecutive_errors,
                   max_tx_per_day=cfg.max_tx_per_day,
                   max_gas_pol_per_day=cfg.max_gas_pol_per_day)


class RiskGovernor:
    def __init__(self, limits: RiskLimits | None = None, *,
                 ledger_dir: str = ".data_cache/risk", clock=time.time):
        self.limits = limits or RiskLimits()
        self.ledger_dir = Path(ledger_dir)
        self.clock = clock
        self._day: str = ""
        self._tx_count = 0
        self._gas_pol = 0.0
        self._equity_open: float | None = None
        self._equity_last: float | None = None
        self._consecutive_errors = 0
        self._inventory: dict[str, float] = {}
        self._fh = None
        self._roll_day()

    # -- day / ledger plumbing ------------------------------------------------------------------
    def _utc_day(self) -> str:
        return time.strftime("%Y%m%d", time.gmtime(self.clock()))

    def _ledger_path(self, day: str) -> Path:
        return self.ledger_dir / f"ledger-{day}.jsonl"

    def _roll_day(self) -> None:
        day = self._utc_day()
        if day == self._day:
            return
        if self._fh:
            self._fh.close()
        self._day = day
        self._tx_count, self._gas_pol = 0, 0.0
        self._equity_open = self._equity_last = None
        path = self._ledger_path(day)
        if path.exists():  # replay today's ledger so budgets/loss survive restart
            for line in path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # torn trailing line from a crash
                if rec.get("type") == "tx":
                    self._tx_count += 1
                    self._gas_pol += float(rec.get("gas_pol") or 0.0)
                elif rec.get("type") == "equity":
                    if self._equity_open is None:
                        self._equity_open = float(rec["equity_usd"])
                    self._equity_last = float(rec["equity_usd"])
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a")

    def _write(self, rec: dict) -> None:
        rec = {"t": self.clock(), **rec}
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()

    # -- the gate -------------------------------------------------------------------------------
    def allow_tx(self, kind: str, *, market: str = "") -> tuple[bool, str]:
        self._roll_day()
        if os.path.exists(self.limits.kill_switch_path):
            return False, "kill-switch file present"
        if self._consecutive_errors >= self.limits.max_consecutive_errors:
            return False, f"error breaker open ({self._consecutive_errors} consecutive errors)"
        if self._tx_count >= self.limits.max_tx_per_day:
            return False, f"daily tx budget exhausted ({self._tx_count})"
        if self._gas_pol >= self.limits.max_gas_pol_per_day:
            return False, f"daily gas budget exhausted ({self._gas_pol:.3f} POL)"
        loss = self.daily_loss()
        if loss > self.limits.max_daily_loss_usd:
            return False, f"daily loss limit breached ({loss:.2f} USD)"
        if self.gross_exposure() > self.limits.portfolio_gross_cap:
            return False, f"gross exposure over cap ({self.gross_exposure():.1f})"
        return True, ""

    # -- recorders ------------------------------------------------------------------------------
    def record_tx(self, kind: str, tx_hash: str, gas_pol: float, *, market: str = "") -> None:
        self._roll_day()
        self._tx_count += 1
        self._gas_pol += gas_pol
        self._write({"type": "tx", "kind": kind, "tx": tx_hash, "gas_pol": gas_pol,
                     "market": market})

    def record_fill(self, market: str, side: str, price: float, size: float) -> None:
        signed = size if side == "BUY" else -size
        self._inventory[market] = self._inventory.get(market, 0.0) + signed
        self._write({"type": "fill", "market": market, "side": side,
                     "price": price, "size": size})

    def mark_equity(self, equity_usd: float) -> None:
        self._roll_day()
        if self._equity_open is None:
            self._equity_open = equity_usd
        self._equity_last = equity_usd
        self._write({"type": "equity", "equity_usd": equity_usd})

    def record_error(self, err: str) -> None:
        self._consecutive_errors += 1
        self._write({"type": "error", "err": str(err)[:200],
                     "consecutive": self._consecutive_errors})

    def record_success(self) -> None:
        self._consecutive_errors = 0

    # -- views ----------------------------------------------------------------------------------
    def daily_loss(self) -> float:
        if self._equity_open is None or self._equity_last is None:
            return 0.0
        return max(0.0, self._equity_open - self._equity_last)

    def gross_exposure(self) -> float:
        return sum(abs(v) for v in self._inventory.values())

    def kill(self, reason: str) -> None:
        p = Path(self.limits.kill_switch_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{reason} @ {self.clock()}\n")
        self._write({"type": "kill", "reason": reason})

    def unkill(self) -> bool:
        p = Path(self.limits.kill_switch_path)
        if p.exists():
            p.unlink()
            self._write({"type": "unkill"})
            return True
        return False

    def status(self) -> dict:
        self._roll_day()
        allowed, reason = self.allow_tx("status-probe")
        return {"day": self._day, "tx_count": self._tx_count,
                "gas_pol": round(self._gas_pol, 6),
                "daily_loss_usd": round(self.daily_loss(), 4),
                "gross_exposure": round(self.gross_exposure(), 4),
                "consecutive_errors": self._consecutive_errors,
                "killed": os.path.exists(self.limits.kill_switch_path),
                "halted": not allowed, "halt_reason": reason,
                "limits": {"max_daily_loss_usd": self.limits.max_daily_loss_usd,
                           "portfolio_gross_cap": self.limits.portfolio_gross_cap,
                           "max_tx_per_day": self.limits.max_tx_per_day,
                           "max_gas_pol_per_day": self.limits.max_gas_pol_per_day,
                           "max_consecutive_errors": self.limits.max_consecutive_errors}}

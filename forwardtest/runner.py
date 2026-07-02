"""
runner — forward-test harness (paper / paper-live).

  paper       : fully simulated book + fills.
  paper-live  : read the REAL live book (public WS/REST, no auth), SIMULATE fills locally, place
                NO real orders. Works under any jurisdiction outcome.
  live        : real orders (tiny capital) — JURISDICTION-GATED, see ../JURISDICTION.md.

⚠ Honesty (DECISIONS.md): paper-live CANNOT observe true queue position, fill probability, or
realized rewards/rebates (those need real resting orders). Treat it as LOGIC/microstructure
validation only — never report simulated rewards as P&L. Model a conservative fill (assume you
sit behind all existing same-price depth) calibrated from the real OrderFilled tape.

Start this loop EARLY (~day 9) so 9-10 days of tape accrue before any live ablation.
"""
from __future__ import annotations


def run(mode: str = "paper", markets: list[str] | None = None) -> None:
    """TODO: drive execution.loop.run_loop; log every quote/fill/exit; compute P&L, inventory,
    reward accrual (labeled clearly as simulated in paper modes)."""
    raise NotImplementedError("runner.run: paper/paper-live harness with P&L + inventory logging")


if __name__ == "__main__":
    import sys
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "paper"
    run(mode=mode)

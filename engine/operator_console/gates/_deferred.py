"""Stub modules for Gates 2 / 4 / 7 / 8 — DEFERRED to Phase 2 polish.

Each returns a GateResult with status=DEFERRED so the framework keeps
the full 8-gate skeleton visible to the UI + audit trail. When a gate
ships for real, replace its stub with the real check module and update
the registry.

Why a single file instead of 4 modules:
  - Each stub is 5 lines; 4 separate modules would be 4×30-line files
    of boilerplate
  - Real gate implementations get their own module (see gate1, 3, 5, 6)
  - This file's expected lifetime is short — every entry here is a
    promise to ship the real check later
"""
from __future__ import annotations

from typing import Any

from engine.operator_console.gates import GateResult, GateStatus


def _deferred(gate_id: str, title: str, missing_check: str) -> GateResult:
    return GateResult(
        gate_id = gate_id,
        title   = title,
        status  = GateStatus.DEFERRED,
        summary = (f"DEFERRED to Phase 2 polish. {missing_check} not yet "
                   f"implemented in the gate framework — human reviewer "
                   f"must verify manually before approving at Gate 9."),
        detail  = {"phase": "2.x deferred"},
    )


def gate2_check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    """Cost-robust (Almgren-Chriss cost-robust Sharpe survives realistic
    execution cost). Phase 2 polish — requires cost-model integration."""
    return _deferred("gate2_cost_robust",
                     "Cost-robust (Almgren-Chriss)",
                     "Cost-robust Sharpe survival check")


def gate4_check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    """Replication (γ persona LLM confirms paper finding replicates).
    Phase 2 polish — needs persona-driven replication runner."""
    return _deferred("gate4_replication",
                     "Replication (γ persona)",
                     "Paper-replication confirmation by γ persona")


def gate7_check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    """Cross-sleeve correlation (variant's PnL correlation to each
    deployed sleeve is low enough that it adds diversification).
    Phase 2 polish — needs WRDS-derived per-sleeve PnL series at
    promote time, which doesn't ship in the public snapshot."""
    return _deferred("gate7_cross_sleeve_corr",
                     "Cross-sleeve correlation",
                     "Per-sleeve PnL correlation check (needs WRDS)")


def gate8_check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    """Capacity (Pastor-Stambaugh / Berk-Green capacity ceiling
    supports the target weight). Phase 2 polish — needs capacity
    model + AUM estimate."""
    return _deferred("gate8_capacity",
                     "Capacity (Pastor-Stambaugh)",
                     "Capacity-ceiling check via Pastor-Stambaugh")

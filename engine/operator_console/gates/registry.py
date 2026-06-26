"""Gate registry — the canonical execution order.

S7's execute() iterates this list, calling each gate's check()
in turn. preflight() runs the same checks for UI feedback before
trigger.

Adding a new gate or replacing a stubbed-DEFERRED one:
  1. Implement engine/operator_console/gates/gateN_<name>.py with
     a top-level `check(verdict_event, config) -> GateResult`.
  2. Replace the corresponding entry below with the new module.
  3. Verify execution order matches the docstring in
     engine/operator_console/gates/__init__.py.

Gate 9 is NOT in this list — it's the human handoff (writes the
proposal row + routes to /approvals), implemented in the S7 station
body since it has side effects rather than a check.
"""
from __future__ import annotations

from typing import Any, Callable

from engine.operator_console.gates import GateResult
from engine.operator_console.gates import gate1_verdict_green
from engine.operator_console.gates import gate3_pit_clean
from engine.operator_console.gates import gate5_multi_period
from engine.operator_console.gates import gate6_anchor_residual
from engine.operator_console.gates import _deferred


GateCheck = Callable[[dict[str, Any], dict[str, Any]], GateResult]


GATES: list[tuple[str, str, GateCheck]] = [
    # (gate_id,                title,                                      check fn)
    ("gate1_verdict_green",   "Verdict is GREEN",                          gate1_verdict_green.check),
    ("gate2_cost_robust",     "Cost-robust (Almgren-Chriss)",              _deferred.gate2_check),
    ("gate3_pit_clean",       "PIT clean (look-ahead audit)",              gate3_pit_clean.check),
    ("gate4_replication",     "Replication (γ persona)",                   _deferred.gate4_check),
    ("gate5_multi_period",    "Multi-period stability",                    gate5_multi_period.check),
    ("gate6_anchor_residual", "Anchor-residual α t-stat",                  gate6_anchor_residual.check),
    ("gate7_cross_sleeve_corr","Cross-sleeve correlation",                 _deferred.gate7_check),
    ("gate8_capacity",        "Capacity (Pastor-Stambaugh)",               _deferred.gate8_check),
]


def run_all_gates(
    verdict_event: dict[str, Any],
    config: dict[str, Any],
) -> list[GateResult]:
    """Run every gate in execution order, returning the full list of
    results. Caller decides whether any FAIL halts the workflow."""
    out: list[GateResult] = []
    for _gate_id, _title, fn in GATES:
        try:
            r = fn(verdict_event, config)
        except Exception as e:  # noqa: BLE001 — never let a gate crash the chain
            from engine.operator_console.gates import GateStatus
            r = GateResult(
                gate_id = _gate_id,
                title   = _title,
                status  = GateStatus.FAIL,
                summary = f"Gate check raised {type(e).__name__}: {str(e)[:200]}",
                detail  = {"exception": type(e).__name__},
            )
        out.append(r)
    return out

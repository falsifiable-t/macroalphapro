"""S7 PROMOTE 9-gate framework.

Each gate is a focused statistical / provenance check between a GREEN
FORWARD verdict and a deployed sleeve. Per CLAUDE.md doctrine:

  - Capital decisions stay HUMAN at Gate 9. S7 NEVER auto-deploys.
  - Gates 1-8 are deterministic checks; Gate 9 is the human handoff.
  - A FAIL at any gate halts promotion. SOFT_PASS is informational
    (yellow warning, doesn't block). DEFERRED means the gate's check
    isn't implemented yet — the human reviewer must verify manually.

## Gate inventory (matches docs/architecture/operator_console.md §5)

  1. Verdict GREEN              — verify the source event is a GREEN
                                  factor_verdict_filed (deterministic)
  2. Cost-robust                — Almgren-Chriss cost-robust Sharpe
                                  survives realistic execution cost
                                  (Phase 2 polish — DEFERRED)
  3. PIT clean                  — evidence_doc + PIT audit trail
                                  resolvable; no look-ahead suspects
  4. Replication (γ persona)    — γ replication persona confirms paper
                                  result matches verdict
                                  (Phase 2 polish — DEFERRED)
  5. Multi-period stability     — subsample_stability metrics show
                                  institutional-grade cross-period
                                  consistency
  6. Anchor-residual            — post-FF5+MOM (and possibly post-
                                  industry) residual α t-stat above
                                  threshold; verdict isn't a beta
                                  exposure dressed up as alpha
  7. Cross-sleeve correlation   — variant's PnL correlation to each
                                  deployed sleeve is low enough that
                                  it adds diversification
                                  (Phase 2 polish — DEFERRED)
  8. Capacity (Pastor-Stambaugh)— estimated capacity ceiling supports
                                  the target weight
                                  (Phase 2 polish — DEFERRED)
  9. HUMAN approval             — write proposal to /approvals queue
                                  (not in this module; lives in S7
                                  station body since it's the human
                                  handoff, not a statistical check)

## Contract

Each gate module under engine/operator_console/gates/gateN_*.py
exports a `check(verdict_event, config) -> GateResult` function.
The registry lists them in execution order.

A GateResult is immutable. detail dict carries gate-specific
metrics for UI rendering.

Adding a new gate:
  1. Implement engine/operator_console/gates/gateN_<name>.py with
     a top-level `check(verdict_event, config) -> GateResult`.
  2. Add an entry to GATES in registry.py — preserving execution
     order (Gate N must follow Gate N-1).
  3. Add a unit test under tests/test_operator_console_gates.py.
  4. If your gate consumes a new metric, update the docstring above.

Reading: a downstream consumer asks "did all gates pass?":
    from engine.operator_console.gates import run_all_gates
    results = run_all_gates(verdict_event, config)
    blocked = [r for r in results if r.status == GateStatus.FAIL]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GateStatus(str, Enum):
    """Result of a single gate check."""

    PASS      = "pass"      # check ran, passed — does not block promote
    SOFT_PASS = "soft_pass" # check ran, passed with caveats — yellow info
    FAIL      = "fail"      # check ran, FAILED — blocks promote
    DEFERRED  = "deferred"  # check not implemented (Phase 2 polish);
                            # human reviewer must verify manually
    SKIPPED   = "skipped"   # check intentionally not run (missing
                            # upstream data, etc.) — yellow info


@dataclass(frozen=True)
class GateResult:
    """Single gate's verdict + supporting detail.

    Used for SSE progress emit, preflight check rendering, and the
    promote_proposal audit trail."""

    gate_id:    str
    title:      str
    status:     GateStatus
    summary:    str                          # 1-2 sentences for UI
    detail:     dict[str, Any] = field(default_factory=dict)


def is_blocking(status: GateStatus) -> bool:
    """True if this status prevents promotion (only FAIL today)."""
    return status == GateStatus.FAIL


def is_pass(status: GateStatus) -> bool:
    """True if this status counts as 'check ran and passed' (PASS
    or SOFT_PASS)."""
    return status in (GateStatus.PASS, GateStatus.SOFT_PASS)

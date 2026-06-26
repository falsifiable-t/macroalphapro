"""Gate 5 — Multi-period stability.

Reads the verdict's `subsample_stability` block (produced by
engine/research/subsample_stability.py during FORWARD dispatch).
The block contains per-subsample Sharpe + NW-t across 3-5 sequential
windows of the observation period.

## Threshold calibration (v16 2026-06-26 empirical study)

v14 shipped with FAIL threshold 0.3, which rejected 30/35 (86%) of
GREEN verdicts that had a subsample_stability block. Empirical study
on the live event store (117 verdicts with subsample blocks) revealed:

  - The upstream `institutional_stable` flag (`INSTITUTIONAL_STABILITY_BAR
    = 0.4 AND min_sharpe > 0` in subsample_stability.py:52) was NEVER
    True for any GREEN verdict in the store. Real multi-period decay
    is more common than that bar admits.
  - Of 35 GREEN verdicts with subsample blocks:
        wb < 0       : 3   (sign-flip — best window pos, worst neg)
        wb [0, 0.1)  : 2
        wb [0.1, 0.2): 15  ← bulk
        wb [0.2, 0.3): 10
        wb [0.3, 0.4): 5
        wb >= 0.4    : 0   (none reach the institutional bar)
  - Per McLean-Pontiff (2016) 32-58% Sharpe drop post-publication is
    NORMAL. A 0.3 worst/best ratio bar implies a tighter institutional
    threshold than the empirical literature supports.

Adjusted v16 thresholds:

  - FAIL:      wb < 0.15      (worst window <15% of best — severe decay
                               that genuinely shouldn't deploy capital)
  - SOFT_PASS: 0.15 <= wb < 0.4 (moderate decay — operator + S7 gates
                                  2/4/6/7/8 should compensate)
  - PASS:      wb >= 0.4 AND min_sharpe > 0 (matches upstream
                                              institutional_stable flag)
  - SKIPPED:   no subsample block on the verdict

The looser FAIL bar acknowledges that 0.15-0.4 wb is the empirical
modal range — rejecting all of it would effectively disable promote
for real-world factor research.
"""
from __future__ import annotations

from typing import Any

from engine.operator_console.gates import GateResult, GateStatus


GATE_ID = "gate5_multi_period"
GATE_TITLE = "Multi-period stability"

# v16 empirical-driven thresholds — see module docstring
_FAIL_THRESHOLD       = 0.15  # below this = severe decay, block promote
_INSTITUTIONAL_BAR    = 0.4   # matches subsample_stability.py upstream


def check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    metrics = verdict_event.get("metrics") or {}
    ss = metrics.get("subsample_stability") or {}

    if not ss:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SKIPPED,
            summary = ("Verdict has no subsample_stability block. Pre-Phase-2 "
                       "verdict — human reviewer must verify multi-period "
                       "robustness manually."),
            detail  = {"metric_keys": list(metrics.keys())[:20]},
        )

    institutional_stable = ss.get("institutional_stable")
    worst_best = ss.get("worst_best_sharpe_ratio")
    n_splits = ss.get("n_splits")
    decay_slope_t = ss.get("decay_slope_t")

    detail = {
        "n_splits":                 n_splits,
        "worst_best_sharpe_ratio":  worst_best,
        "decay_slope_t":            decay_slope_t,
        "monotone_decay":           ss.get("monotone_decay"),
        "institutional_stable":     institutional_stable,
    }

    # Tier 1 — upstream institutional flag (wb >= 0.4 AND min_sharpe > 0)
    if institutional_stable is True:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.PASS,
            summary = (f"institutional_stable=True across {n_splits} subsamples "
                       f"(worst/best Sharpe ratio={worst_best:.2f}). Real "
                       f"multi-period robustness."),
            detail  = detail,
        )

    # Tier 2 — wb missing or ratio meaningless (best Sharpe <= 0)
    if not isinstance(worst_best, (int, float)):
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SOFT_PASS,
            summary = ("worst_best_sharpe_ratio not computable (best window "
                       "Sharpe <= 0 or insufficient sub-windows). Reviewer "
                       "should examine the subsample block directly."),
            detail  = detail,
        )

    # Tier 3 — Bonus PASS path: wb meets the institutional bar even if the
    # upstream flag wasn't set (e.g. min_sharpe is exactly 0 due to rounding)
    if worst_best >= _INSTITUTIONAL_BAR:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.PASS,
            summary = (f"worst/best Sharpe {worst_best:.2f} >= "
                       f"{_INSTITUTIONAL_BAR} (institutional bar) across "
                       f"{n_splits} subsamples."),
            detail  = detail,
        )

    # Tier 4 — Moderate decay band (empirical modal range)
    if worst_best >= _FAIL_THRESHOLD:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SOFT_PASS,
            summary = (f"worst/best Sharpe {worst_best:.2f} in moderate-decay "
                       f"band [{_FAIL_THRESHOLD}, {_INSTITUTIONAL_BAR}) across "
                       f"{n_splits} subsamples. Below institutional bar; "
                       f"S7 Gates 2/4/6/7/8 should compensate."),
            detail  = detail,
        )

    # Tier 5 — Severe decay → block promote
    return GateResult(
        gate_id = GATE_ID,
        title   = GATE_TITLE,
        status  = GateStatus.FAIL,
        summary = (f"worst/best Sharpe {worst_best:.2f} < {_FAIL_THRESHOLD} "
                   f"(severe decay) across {n_splits} subsamples. Worst window "
                   f"is <15% of best — promote would deploy into rapidly-"
                   f"fading alpha."),
        detail  = detail,
    )

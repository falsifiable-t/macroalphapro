"""Gate 5 — Multi-period stability.

Reads the verdict's `subsample_stability` block (produced by
engine/research/subsample_stability.py during FORWARD dispatch).
The block contains per-subsample Sharpe + NW-t across 3-5 sequential
windows of the observation period.

Gate logic:

  - If `institutional_stable` flag is True → PASS
  - If flag is False but `worst_best_sharpe_ratio` >= 0.3 → SOFT_PASS
    (some decay but not catastrophic; reviewer should verify)
  - If flag is False and worst/best <= 0.3 → FAIL
    (severe decay across periods; promote would be deploying into
    fading alpha)
  - If `subsample_stability` block absent from metrics → SKIPPED
    (pre-Phase-2 verdicts; human reviewer should verify manually)

Doesn't recompute Mann-Kendall — that's already done upstream in
subsample_stability.py. Gate 5's job is to consume the existing
verdict and apply the promotion threshold.
"""
from __future__ import annotations

from typing import Any

from engine.operator_console.gates import GateResult, GateStatus


GATE_ID = "gate5_multi_period"
GATE_TITLE = "Multi-period stability"

_WORST_BEST_THRESHOLD = 0.3


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

    if institutional_stable is True:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.PASS,
            summary = (f"institutional_stable=True across {n_splits} subsamples "
                       f"(worst/best Sharpe ratio={worst_best:.2f})." if worst_best
                       else f"institutional_stable=True across {n_splits} subsamples."),
            detail  = {
                "n_splits":                 n_splits,
                "worst_best_sharpe_ratio":  worst_best,
                "decay_slope_t":            decay_slope_t,
                "monotone_decay":           ss.get("monotone_decay"),
            },
        )

    # Not flagged institutional_stable — drill into severity
    if isinstance(worst_best, (int, float)) and worst_best >= _WORST_BEST_THRESHOLD:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SOFT_PASS,
            summary = (f"institutional_stable=False but worst/best Sharpe "
                       f"{worst_best:.2f} >= {_WORST_BEST_THRESHOLD}. Some decay "
                       f"across {n_splits} subsamples — reviewer should verify."),
            detail  = {
                "n_splits":                 n_splits,
                "worst_best_sharpe_ratio":  worst_best,
                "decay_slope_t":            decay_slope_t,
                "monotone_decay":           ss.get("monotone_decay"),
            },
        )

    if isinstance(worst_best, (int, float)):
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = (f"institutional_stable=False AND worst/best Sharpe "
                       f"{worst_best:.2f} < {_WORST_BEST_THRESHOLD}. Severe "
                       f"multi-period decay — promote would deploy into "
                       f"fading alpha."),
            detail  = {
                "n_splits":                 n_splits,
                "worst_best_sharpe_ratio":  worst_best,
                "decay_slope_t":            decay_slope_t,
            },
        )

    return GateResult(
        gate_id = GATE_ID,
        title   = GATE_TITLE,
        status  = GateStatus.SOFT_PASS,
        summary = ("institutional_stable=False but no worst/best ratio "
                   "available to assess severity. Reviewer should drill in."),
        detail  = {"subsample_stability": ss},
    )

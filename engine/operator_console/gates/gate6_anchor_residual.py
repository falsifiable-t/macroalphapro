"""Gate 6 — Anchor-residual α t-stat above threshold.

Reads `anchor_orthogonality` from the verdict metrics (produced by
engine/research/anchor_regression.py — the FF5+MOM spanning step
in FORWARD dispatch). After projecting out FF5+MOM beta exposures,
the residual α has its own t-stat.

Gate logic:

  - If `alpha_nw_t` >= 2.0 → PASS (real residual alpha, beta-clean)
  - If 1.0 <= `alpha_nw_t` < 2.0 → SOFT_PASS (marginal residual;
    reviewer should verify this isn't a beta exposure dressed as
    alpha)
  - If `alpha_nw_t` < 1.0 → FAIL (residual alpha not distinguishable
    from zero after FF5+MOM; the apparent Sharpe is a beta exposure)
  - If `anchor_orthogonality` block absent → SKIPPED

Also reports the `industry_extension.alpha_full_nw_t` if present (the
post-industry residual), as an additional layer of beta-cleanness.
"""
from __future__ import annotations

from typing import Any

from engine.operator_console.gates import GateResult, GateStatus


GATE_ID = "gate6_anchor_residual"
GATE_TITLE = "Anchor-residual α t-stat"

_T_PASS_THRESHOLD = 2.0
_T_SOFT_THRESHOLD = 1.0


def check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    metrics = verdict_event.get("metrics") or {}
    ao = metrics.get("anchor_orthogonality") or {}

    if not ao:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SKIPPED,
            summary = ("Verdict has no anchor_orthogonality block. Pre-Phase-2 "
                       "verdict (FF5+MOM spanning not stored) — human reviewer "
                       "must verify beta-cleanness manually."),
            detail  = {"metric_keys": list(metrics.keys())[:20]},
        )

    alpha_nw_t = ao.get("alpha_nw_t")
    alpha_annual = ao.get("alpha_annual")
    anchor_names = ao.get("anchor_names") or []
    n_overlap = ao.get("n_overlap")
    r2_adj = ao.get("r2_adj")

    # Industry extension (if FORWARD ran the industry+anchor stack)
    ie = metrics.get("industry_extension") or {}
    industry_alpha_nw_t = ie.get("alpha_full_nw_t")

    detail = {
        "alpha_nw_t":           alpha_nw_t,
        "alpha_annual":         alpha_annual,
        "anchor_names":         list(anchor_names),
        "n_overlap":            n_overlap,
        "r2_adj":               r2_adj,
        "industry_alpha_nw_t":  industry_alpha_nw_t,
    }

    if alpha_nw_t is None:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SKIPPED,
            summary = "anchor_orthogonality present but alpha_nw_t missing.",
            detail  = detail,
        )

    try:
        t = float(alpha_nw_t)
    except (TypeError, ValueError):
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SKIPPED,
            summary = f"alpha_nw_t not numeric: {alpha_nw_t!r}.",
            detail  = detail,
        )

    abs_t = abs(t)

    # Negative-alpha case: even if |t| is large, a negative alpha after
    # FF5+MOM means the verdict is short-alpha. Caller's responsibility
    # to handle sign; gate just reports.
    if t < 0:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = (f"Residual α after {','.join(anchor_names) or 'FF5+MOM'} "
                       f"is NEGATIVE (t={t:.2f}, annual α={alpha_annual:.2%} "
                       f"if available). Promote would deploy a short-alpha "
                       f"strategy disguised by beta exposure."
                       if alpha_annual is not None else
                       f"Residual α t-stat is NEGATIVE ({t:.2f}). Promote "
                       f"would deploy a short-alpha strategy disguised by "
                       f"beta exposure."),
            detail  = detail,
        )

    if abs_t >= _T_PASS_THRESHOLD:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.PASS,
            summary = (f"Residual α t={t:.2f} >= {_T_PASS_THRESHOLD} after "
                       f"{','.join(anchor_names) or 'FF5+MOM'} spanning. "
                       f"Real beta-clean alpha."),
            detail  = detail,
        )

    if abs_t >= _T_SOFT_THRESHOLD:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.SOFT_PASS,
            summary = (f"Residual α t={t:.2f} in marginal band "
                       f"[{_T_SOFT_THRESHOLD}, {_T_PASS_THRESHOLD}). "
                       f"Reviewer should verify this isn't a beta exposure "
                       f"dressed up as alpha."),
            detail  = detail,
        )

    return GateResult(
        gate_id = GATE_ID,
        title   = GATE_TITLE,
        status  = GateStatus.FAIL,
        summary = (f"Residual α t={t:.2f} < {_T_SOFT_THRESHOLD}. After "
                   f"FF5+MOM spanning, α is NOT distinguishable from zero. "
                   f"The Sharpe is beta, not alpha."),
        detail  = detail,
    )

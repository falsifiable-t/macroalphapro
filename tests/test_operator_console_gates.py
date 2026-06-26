"""Unit tests for the S7 PROMOTE 9-gate framework.

Covers:
  - Framework: GateResult + GateStatus + run_all_gates skeleton
  - Gate 1: bug-regression test (verdict at top-level, not payload)
  - Gate 3: PIT clean — doc-exists / doc-missing / doc-has-section
  - Gate 5: multi-period — institutional_stable=True / worst-best severe / missing
  - Gate 6: anchor-residual — strong t / marginal / weak / negative
  - DEFERRED stubs return GateStatus.DEFERRED
"""
from __future__ import annotations

import pytest

from engine.operator_console.gates import (
    GateResult, GateStatus, is_blocking, is_pass,
)
from engine.operator_console.gates import (
    gate1_verdict_green,
    gate3_pit_clean,
    gate5_multi_period,
    gate6_anchor_residual,
    _deferred,
)
from engine.operator_console.gates.registry import GATES, run_all_gates


# ── Framework primitives ─────────────────────────────────────────


def test_gatestatus_enum_values():
    """Every status string is stable — UI + audit log consume these."""
    assert GateStatus.PASS.value      == "pass"
    assert GateStatus.SOFT_PASS.value == "soft_pass"
    assert GateStatus.FAIL.value      == "fail"
    assert GateStatus.DEFERRED.value  == "deferred"
    assert GateStatus.SKIPPED.value   == "skipped"


def test_is_blocking_only_fail():
    """Only FAIL halts promote. SOFT_PASS / DEFERRED / SKIPPED are
    yellow info, not blockers."""
    assert is_blocking(GateStatus.FAIL) is True
    for s in [GateStatus.PASS, GateStatus.SOFT_PASS,
              GateStatus.DEFERRED, GateStatus.SKIPPED]:
        assert is_blocking(s) is False


def test_is_pass_includes_soft_pass():
    assert is_pass(GateStatus.PASS) is True
    assert is_pass(GateStatus.SOFT_PASS) is True
    assert is_pass(GateStatus.FAIL) is False
    assert is_pass(GateStatus.DEFERRED) is False


# ── Gate 1: verdict-GREEN regression test ────────────────────────


def test_gate1_passes_top_level_green_verdict():
    """Pre-v14 bug: gate read payload.verdict (nested), but research_store
    stores verdict at the TOP LEVEL of the event row. Regression test
    locks the schema."""
    ev = {
        "event_type": "factor_verdict_filed",
        "verdict":    "GREEN",            # ← top-level, NOT nested
        "subject_id": "test_subject",
    }
    r = gate1_verdict_green.check(ev, {})
    assert r.status == GateStatus.PASS
    assert "GREEN" in r.summary


def test_gate1_pre_v14_bug_does_not_recur():
    """If verdict is only in payload (the pre-v14 wrong schema), Gate 1
    must FAIL — not YELLOW. Soft-fallback masked 70 real GREEN events
    pre-fix; we explicitly want FAIL now so a misshaped event halts
    promote loudly."""
    ev = {
        "event_type": "factor_verdict_filed",
        "payload":    {"verdict": "GREEN"},   # WRONG schema; verdict NOT at top
    }
    r = gate1_verdict_green.check(ev, {})
    assert r.status == GateStatus.FAIL


def test_gate1_fails_marginal_and_red():
    for verdict in ("MARGINAL", "RED"):
        ev = {"event_type": "factor_verdict_filed", "verdict": verdict}
        r = gate1_verdict_green.check(ev, {})
        assert r.status == GateStatus.FAIL


def test_gate1_fails_wrong_event_type():
    ev = {"event_type": "memory_doctrine_locked", "verdict": "GREEN"}
    r = gate1_verdict_green.check(ev, {})
    assert r.status == GateStatus.FAIL


# ── Gate 3: PIT clean ────────────────────────────────────────────


def test_gate3_fails_no_evidence_doc():
    ev = {"event_type": "factor_verdict_filed", "verdict": "GREEN",
          "artifacts": {}, "metrics": {}}
    r = gate3_pit_clean.check(ev, {})
    assert r.status == GateStatus.FAIL
    assert "no artifacts.evidence_doc" in r.summary or "evidence_doc" in r.summary


def test_gate3_fails_missing_file(tmp_path):
    ev = {"event_type": "factor_verdict_filed", "verdict": "GREEN",
          "artifacts": {"evidence_doc": str(tmp_path / "nonexistent.md")},
          "metrics": {}}
    r = gate3_pit_clean.check(ev, {})
    assert r.status == GateStatus.FAIL


def test_gate3_passes_with_pit_section(tmp_path):
    doc = tmp_path / "evidence.md"
    doc.write_text("# Subject\n\n## PIT audit\nLooks clean.\n",
                   encoding="utf-8")
    ev = {"event_type": "factor_verdict_filed", "verdict": "GREEN",
          "artifacts": {"evidence_doc": str(doc)},
          "metrics": {"n_obs_months": 100,
                      "anchor_orthogonality": {"n_overlap": 90}}}
    r = gate3_pit_clean.check(ev, {})
    assert r.status == GateStatus.PASS
    assert r.detail["has_pit_section"] is True


def test_gate3_soft_pass_without_pit_section(tmp_path):
    doc = tmp_path / "evidence.md"
    doc.write_text("# Subject\nNo PIT section here.\n", encoding="utf-8")
    ev = {"event_type": "factor_verdict_filed", "verdict": "GREEN",
          "artifacts": {"evidence_doc": str(doc)}, "metrics": {}}
    r = gate3_pit_clean.check(ev, {})
    assert r.status == GateStatus.SOFT_PASS
    assert r.detail["has_pit_section"] is False


def test_gate3_fails_on_lookahead_red_flag(tmp_path):
    """n_overlap > n_obs_months = lookahead red flag."""
    doc = tmp_path / "evidence.md"
    doc.write_text("# Subject\n## PIT audit\nClaim is clean.\n",
                   encoding="utf-8")
    ev = {"event_type": "factor_verdict_filed", "verdict": "GREEN",
          "artifacts": {"evidence_doc": str(doc)},
          "metrics": {"n_obs_months": 100,
                      "anchor_orthogonality": {"n_overlap": 120}}}  # impossible
    r = gate3_pit_clean.check(ev, {})
    assert r.status == GateStatus.FAIL
    assert "Look-ahead" in r.summary or "look-ahead" in r.summary or "lookahead" in r.summary.lower()


# ── Gate 5: multi-period ─────────────────────────────────────────


def test_gate5_pass_when_institutional_stable():
    """Upstream institutional_stable=True is the gold-standard PASS path."""
    ev = {"metrics": {"subsample_stability": {
        "institutional_stable": True,
        "worst_best_sharpe_ratio": 0.65,
        "n_splits": 4,
    }}}
    r = gate5_multi_period.check(ev, {})
    assert r.status == GateStatus.PASS


def test_gate5_pass_when_wb_meets_institutional_bar():
    """v16: wb >= 0.4 is the bonus-PASS path even if upstream flag is False
    (e.g. rounding edge cases where min_sharpe is exactly 0)."""
    ev = {"metrics": {"subsample_stability": {
        "institutional_stable": False,
        "worst_best_sharpe_ratio": 0.45,
        "n_splits": 4,
    }}}
    r = gate5_multi_period.check(ev, {})
    assert r.status == GateStatus.PASS


def test_gate5_soft_pass_moderate_decay():
    """v16: 0.15 <= wb < 0.4 is the modal empirical range — SOFT_PASS,
    not FAIL. Empirical study showed 86% of GREEN events sit here."""
    ev = {"metrics": {"subsample_stability": {
        "institutional_stable": False,
        "worst_best_sharpe_ratio": 0.20,
        "n_splits": 4,
    }}}
    r = gate5_multi_period.check(ev, {})
    assert r.status == GateStatus.SOFT_PASS


def test_gate5_fail_severe_decay():
    """v16: only wb < 0.15 (worst <15% of best — severe decay) FAILs."""
    ev = {"metrics": {"subsample_stability": {
        "institutional_stable": False,
        "worst_best_sharpe_ratio": 0.10,
        "n_splits": 4,
    }}}
    r = gate5_multi_period.check(ev, {})
    assert r.status == GateStatus.FAIL


def test_gate5_soft_pass_when_wb_uncomputable():
    """If worst_best_sharpe_ratio is None (best window Sharpe <= 0), the
    ratio is meaningless — return SOFT_PASS with a reviewer prompt
    rather than crashing or auto-failing."""
    ev = {"metrics": {"subsample_stability": {
        "institutional_stable": False,
        "worst_best_sharpe_ratio": None,
        "n_splits": 3,
    }}}
    r = gate5_multi_period.check(ev, {})
    assert r.status == GateStatus.SOFT_PASS


def test_gate5_skipped_no_subsample_block():
    ev = {"metrics": {}}
    r = gate5_multi_period.check(ev, {})
    assert r.status == GateStatus.SKIPPED


# ── Gate 6: anchor-residual ──────────────────────────────────────


def test_gate6_pass_strong_residual():
    ev = {"metrics": {"anchor_orthogonality": {
        "alpha_nw_t":     2.5,
        "alpha_annual":   0.05,
        "anchor_names":   ["MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM"],
    }}}
    r = gate6_anchor_residual.check(ev, {})
    assert r.status == GateStatus.PASS


def test_gate6_soft_pass_marginal_residual():
    ev = {"metrics": {"anchor_orthogonality": {
        "alpha_nw_t":     1.4,    # between 1.0 and 2.0
        "alpha_annual":   0.03,
        "anchor_names":   ["MKT_RF"],
    }}}
    r = gate6_anchor_residual.check(ev, {})
    assert r.status == GateStatus.SOFT_PASS


def test_gate6_fail_weak_residual():
    ev = {"metrics": {"anchor_orthogonality": {
        "alpha_nw_t":     0.5,    # < 1.0
        "alpha_annual":   0.01,
    }}}
    r = gate6_anchor_residual.check(ev, {})
    assert r.status == GateStatus.FAIL


def test_gate6_fail_negative_residual():
    """Even if |t| is huge, negative alpha after FF5+MOM means the
    candidate is SHORT alpha. Promote would deploy a negative-α book."""
    ev = {"metrics": {"anchor_orthogonality": {
        "alpha_nw_t":     -2.5,
        "alpha_annual":   -0.03,
    }}}
    r = gate6_anchor_residual.check(ev, {})
    assert r.status == GateStatus.FAIL
    assert "NEGATIVE" in r.summary


def test_gate6_skipped_no_anchor_block():
    ev = {"metrics": {}}
    r = gate6_anchor_residual.check(ev, {})
    assert r.status == GateStatus.SKIPPED


# ── DEFERRED stubs ───────────────────────────────────────────────


@pytest.mark.parametrize("fn,gate_id", [
    (_deferred.gate2_check, "gate2_cost_robust"),
    (_deferred.gate4_check, "gate4_replication"),
    (_deferred.gate7_check, "gate7_cross_sleeve_corr"),
    (_deferred.gate8_check, "gate8_capacity"),
])
def test_deferred_gates_return_deferred(fn, gate_id):
    r = fn({}, {})
    assert r.status == GateStatus.DEFERRED
    assert r.gate_id == gate_id


# ── Registry + run_all_gates ─────────────────────────────────────


def test_registry_has_all_8_gates_in_order():
    expected_ids = [
        "gate1_verdict_green",
        "gate2_cost_robust",
        "gate3_pit_clean",
        "gate4_replication",
        "gate5_multi_period",
        "gate6_anchor_residual",
        "gate7_cross_sleeve_corr",
        "gate8_capacity",
    ]
    actual_ids = [gid for gid, _title, _fn in GATES]
    assert actual_ids == expected_ids


def test_run_all_gates_on_real_green_event_shape():
    """End-to-end: feed a realistic GREEN event through every gate.
    Should produce 8 results with reasonable status distribution
    (PASS for gate 1, varied for others, DEFERRED for stubs)."""
    ev = {
        "event_id":   "test123",
        "event_type": "factor_verdict_filed",
        "verdict":    "GREEN",
        "subject_id": "test_subject",
        "artifacts":  {},   # no evidence_doc → Gate 3 FAIL
        "metrics":    {},   # no subsample / anchor → Gates 5, 6 SKIPPED
    }
    results = run_all_gates(ev, {})
    assert len(results) == 8
    by_id = {r.gate_id: r for r in results}
    assert by_id["gate1_verdict_green"].status == GateStatus.PASS
    assert by_id["gate3_pit_clean"].status == GateStatus.FAIL
    assert by_id["gate5_multi_period"].status == GateStatus.SKIPPED
    assert by_id["gate6_anchor_residual"].status == GateStatus.SKIPPED
    for stub_id in ("gate2_cost_robust", "gate4_replication",
                    "gate7_cross_sleeve_corr", "gate8_capacity"):
        assert by_id[stub_id].status == GateStatus.DEFERRED


def test_run_all_gates_swallows_per_gate_exceptions():
    """A buggy gate must not crash the chain — it should produce a
    FAIL result and the rest of the chain runs."""
    def boom(_ev, _cfg):
        raise ValueError("synthetic gate bug")
    from engine.operator_console.gates import registry as reg_module
    original = reg_module.GATES
    try:
        reg_module.GATES = [
            ("test_boom_gate", "Boom", boom),
            ("gate1_verdict_green", "Verdict GREEN", gate1_verdict_green.check),
        ]
        results = reg_module.run_all_gates(
            {"event_type": "factor_verdict_filed", "verdict": "GREEN"}, {})
        assert len(results) == 2
        assert results[0].status == GateStatus.FAIL
        assert "ValueError" in results[0].detail.get("exception", "")
        assert results[1].status == GateStatus.PASS
    finally:
        reg_module.GATES = original

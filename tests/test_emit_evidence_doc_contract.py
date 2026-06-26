"""Tests for the v15 factor_verdict evidence_doc contract.

Before v15, factor_verdict's docstring said evidence_doc was
required but the code didn't enforce it. Result: 15 GREEN verdicts
shipped with empty evidence_doc and later failed S7 Gate 3 (PIT
clean) at promote time. v15 makes the contract executable:

  - Default: requires_evidence_doc=True; missing evidence_doc raises
    InvalidEventError.
  - Opt-out: requires_evidence_doc=False MUST be paired with a
    non-empty evidence_doc_exempt_reason; the reason is auto-tagged
    on the event so audit queries can filter.

These tests lock the contract so future refactors can't silently
re-introduce the pre-v15 behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.research_store import emit, registry as subject_registry
from engine.research_store.emit import InvalidEventError
from engine.research_store.schema import SubjectType


_SUBJECT_ID = "test_emit_contract_subject_v15"


@pytest.fixture(autouse=True)
def _register_subject():
    """Ensure the test subject is in the registry before each test."""
    try:
        subject_registry.register_subject(
            _SUBJECT_ID,
            subject_type=SubjectType.factor,
            family="test_family",
            description="Test subject for v15 evidence_doc contract tests",
        )
    except Exception:
        # Already registered from a prior test — fine
        pass


@pytest.fixture
def evidence_doc(tmp_path):
    """Create a real evidence doc on disk so it survives _validate_artifacts."""
    p = tmp_path / "evidence.md"
    p.write_text("# Evidence\n\n## PIT audit\nOK.\n", encoding="utf-8")
    return str(p)


# ── Default: evidence_doc required ───────────────────────────────


def test_factor_verdict_requires_evidence_doc_by_default():
    """Calling without evidence_doc raises InvalidEventError."""
    with pytest.raises(InvalidEventError) as exc:
        emit.factor_verdict(
            subject_id = _SUBJECT_ID,
            verdict    = "RED",
            metrics    = {"n_months": 100},
            artifacts  = {},   # ← no evidence_doc
            summary    = "test summary",
        )
    assert "evidence_doc" in str(exc.value)
    assert "requires_evidence_doc=False" in str(exc.value)  # helpful escape-hatch hint


def test_factor_verdict_requires_evidence_doc_with_other_artifacts():
    """Having OTHER artifacts (like source_ledger) doesn't satisfy
    the contract — evidence_doc specifically is what's required."""
    with pytest.raises(InvalidEventError):
        emit.factor_verdict(
            subject_id = _SUBJECT_ID,
            verdict    = "RED",
            metrics    = {},
            artifacts  = {"source_ledger": "/tmp/somewhere"},   # not evidence_doc
            summary    = "test",
        )


def test_factor_verdict_requires_non_empty_evidence_doc_path():
    """Empty-string evidence_doc fails the same way as missing key."""
    with pytest.raises(InvalidEventError) as exc:
        emit.factor_verdict(
            subject_id = _SUBJECT_ID,
            verdict    = "RED",
            metrics    = {},
            artifacts  = {"evidence_doc": ""},   # empty string
            summary    = "test",
        )
    assert "evidence_doc" in str(exc.value)


def test_factor_verdict_succeeds_with_valid_evidence_doc(evidence_doc):
    """Happy path: evidence_doc points at an existing file → emit OK."""
    event_id = emit.factor_verdict(
        subject_id = _SUBJECT_ID,
        verdict    = "GREEN",
        metrics    = {"n_months": 100, "sharpe": 1.2},
        artifacts  = {"evidence_doc": evidence_doc},
        summary    = "Test verdict with real evidence doc.",
        actor      = "test_v15_contract",
    )
    assert event_id and isinstance(event_id, str)


# ── Opt-out: requires_evidence_doc=False + reason ────────────────


def test_opt_out_without_reason_raises():
    """requires_evidence_doc=False alone is NOT enough — must give reason."""
    with pytest.raises(InvalidEventError) as exc:
        emit.factor_verdict(
            subject_id = _SUBJECT_ID,
            verdict    = "RED",
            metrics    = {},
            artifacts  = {},
            summary    = "test",
            requires_evidence_doc       = False,
            evidence_doc_exempt_reason  = "",   # ← empty reason
        )
    assert "evidence_doc_exempt_reason" in str(exc.value)


def test_opt_out_with_reason_succeeds_and_auto_tags(tmp_path):
    """Shadow-emit / backfill case: opt out with reason → emit
    succeeds + reason is auto-appended to tags."""
    fake_ledger = tmp_path / "factory_ledger.jsonl"
    fake_ledger.write_text("", encoding="utf-8")

    event_id = emit.factor_verdict(
        subject_id = _SUBJECT_ID,
        verdict    = "RED",
        metrics    = {},
        artifacts  = {"source_ledger": str(fake_ledger)},
        summary    = "Shadow-emit from factory_ledger; no per-event evidence doc by design.",
        actor      = "shadow_emit:test",
        tags       = ("shadow_emit", "from_factory_ledger"),
        requires_evidence_doc       = False,
        evidence_doc_exempt_reason  = "shadow_emit_from_factory_ledger",
    )
    assert event_id and isinstance(event_id, str)

    # Verify the auto-tag landed on the persisted event (read raw jsonl)
    import json
    from engine.research_store.store import _EVENTS_PATH
    latest = None
    with _EVENTS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            d = json.loads(line)
            if d.get("event_id") == event_id:
                latest = d
                break
    assert latest is not None, f"event {event_id} not in store"
    tags_on_event = latest.get("tags") or []
    assert any(str(t).startswith("evidence_doc_exempt:") for t in tags_on_event), (
        f"expected evidence_doc_exempt:<reason> tag, got {tags_on_event}"
    )


def test_opt_out_reason_truncated_to_80_chars():
    """Long opt-out reasons get truncated in the tag (UI rendering)."""
    long_reason = "x" * 200
    event_id = emit.factor_verdict(
        subject_id = _SUBJECT_ID,
        verdict    = "RED",
        metrics    = {},
        artifacts  = {},
        summary    = "Long-reason test.",
        actor      = "test_v15_truncation",
        requires_evidence_doc       = False,
        evidence_doc_exempt_reason  = long_reason,
    )
    import json
    from engine.research_store.store import _EVENTS_PATH
    latest = None
    with _EVENTS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            d = json.loads(line)
            if d.get("event_id") == event_id:
                latest = d
                break
    assert latest is not None
    exempt_tags = [t for t in (latest.get("tags") or [])
                   if str(t).startswith("evidence_doc_exempt:")]
    assert exempt_tags
    reason_part = str(exempt_tags[0]).split(":", 1)[1]
    assert len(reason_part) <= 80

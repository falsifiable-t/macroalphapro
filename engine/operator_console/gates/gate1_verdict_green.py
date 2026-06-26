"""Gate 1 — Verdict is GREEN.

Deterministic check that the source event is a `factor_verdict_filed`
event with verdict == "GREEN". This is the entry condition for the
promote pipeline; MARGINAL and RED verdicts must not be promotable.

## Bug history (2026-06-25)

Pre-v14 S7 read `ev.get("payload", {}).get("verdict")` — but the
research_store event schema stores `verdict` at the TOP level of the
event row, NOT nested under a "payload" key. The result: Gate 1
hit the YELLOW "legacy event?" fallback for every input event,
including all 70 GREEN events in the store. Promote was never
actually gating on verdict color.

This module reads `verdict_event.get("verdict")` directly, matching
the actual schema.
"""
from __future__ import annotations

from typing import Any

from engine.operator_console.gates import GateResult, GateStatus


GATE_ID = "gate1_verdict_green"
GATE_TITLE = "Verdict is GREEN"


def check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    """Deterministic check. No side effects."""
    et = verdict_event.get("event_type")
    if et != "factor_verdict_filed":
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = (f"Event type is {et!r}, not factor_verdict_filed. "
                       f"PROMOTE only applies to FORWARD verdicts."),
            detail  = {"event_type": et},
        )

    verdict = verdict_event.get("verdict", "")
    if verdict == "GREEN":
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.PASS,
            summary = f"Verdict=GREEN (subject_id={verdict_event.get('subject_id', '?')}).",
            detail  = {"verdict": verdict, "subject_id": verdict_event.get("subject_id", "")},
        )
    if verdict in ("MARGINAL", "RED"):
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = f"Verdict={verdict!r} — only GREEN verdicts can be PROMOTED.",
            detail  = {"verdict": verdict},
        )
    return GateResult(
        gate_id = GATE_ID,
        title   = GATE_TITLE,
        status  = GateStatus.FAIL,
        summary = (f"Verdict field missing or unexpected value {verdict!r}. "
                   f"Event may be from a pre-schema-stable run; do not promote."),
        detail  = {"verdict": verdict},
    )

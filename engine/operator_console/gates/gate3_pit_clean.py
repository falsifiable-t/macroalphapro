"""Gate 3 — PIT clean.

Point-in-time integrity audit. Checks:

  (a) The verdict event's `artifacts.evidence_doc` exists on disk.
      No evidence doc = no audit trail = blocks promote.
  (b) The evidence doc has at least one PIT-relevant section header
      (PIT audit / look-ahead / point-in-time / data-vintage check).
      Absence = SOFT_PASS (yellow info) — old evidence docs from
      pre-PIT-discipline era may legitimately not have this section.
  (c) The verdict metrics, if present, do not contain a known
      look-ahead red flag (e.g. n_overlap above n_obs_months;
      future-data signature).

A FAIL here halts promote — capital can't deploy on a verdict
whose backing evidence document is missing or whose data audit
shows look-ahead. SOFT_PASS surfaces the gap without blocking
(human reviewer can examine on /approvals).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from engine.operator_console.gates import GateResult, GateStatus


GATE_ID = "gate3_pit_clean"
GATE_TITLE = "PIT clean (look-ahead audit)"

# Match common header patterns used in capability_evidence/*.md
_PIT_SECTION_PATTERNS = [
    r"##\s*PIT\b",
    r"##\s*Point-in-time",
    r"##\s*Look-?ahead",
    r"##\s*Data\s+vintage",
    r"###\s*PIT\b",
    r"###\s*Point-in-time",
    r"###\s*Look-?ahead",
]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def check(verdict_event: dict[str, Any], config: dict[str, Any]) -> GateResult:
    artifacts = verdict_event.get("artifacts") or {}
    doc_path_raw = (artifacts.get("evidence_doc") or "").strip()

    if not doc_path_raw:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = ("Verdict event has no artifacts.evidence_doc. "
                       "Cannot audit PIT-cleanness without an evidence trail."),
            detail  = {"artifacts": list(artifacts.keys())},
        )

    doc_path = Path(doc_path_raw)
    if not doc_path.is_absolute():
        doc_path = _REPO_ROOT / doc_path
    if not doc_path.is_file():
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = (f"evidence_doc path resolves to a non-existent file: "
                       f"{doc_path_raw!r}."),
            detail  = {"resolved_path": str(doc_path)},
        )

    try:
        text = doc_path.read_text(encoding="utf-8")
    except OSError as e:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = f"evidence_doc unreadable: {e}.",
            detail  = {"resolved_path": str(doc_path)},
        )

    has_pit_section = any(re.search(p, text, re.IGNORECASE | re.MULTILINE)
                          for p in _PIT_SECTION_PATTERNS)

    # Sanity check on metrics if present (cheap structural look-ahead probe)
    metrics = verdict_event.get("metrics") or {}
    n_obs = metrics.get("n_obs_months") or metrics.get("n_months")
    # anchor_orthogonality.n_overlap should be <= n_obs (can't have more
    # overlap months than the underlying observation window)
    ao = metrics.get("anchor_orthogonality") or {}
    n_overlap = ao.get("n_overlap")
    overlap_red_flag = False
    if isinstance(n_obs, (int, float)) and isinstance(n_overlap, (int, float)):
        if n_overlap > n_obs:
            overlap_red_flag = True

    if overlap_red_flag:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.FAIL,
            summary = (f"anchor_orthogonality.n_overlap={n_overlap} > "
                       f"n_obs_months={n_obs}. Look-ahead red flag — "
                       f"anchor window appears to contain future data."),
            detail  = {
                "n_obs_months":     n_obs,
                "anchor_n_overlap": n_overlap,
                "evidence_doc":     doc_path_raw,
            },
        )

    if has_pit_section:
        return GateResult(
            gate_id = GATE_ID,
            title   = GATE_TITLE,
            status  = GateStatus.PASS,
            summary = (f"evidence_doc resolves + has PIT/look-ahead section "
                       f"({doc_path.name})."),
            detail  = {
                "evidence_doc":     doc_path_raw,
                "has_pit_section":  True,
                "n_obs_months":     n_obs,
                "anchor_n_overlap": n_overlap,
            },
        )

    return GateResult(
        gate_id = GATE_ID,
        title   = GATE_TITLE,
        status  = GateStatus.SOFT_PASS,
        summary = (f"evidence_doc resolves ({doc_path.name}) but no PIT/"
                   f"look-ahead section header found. Pre-PIT-discipline "
                   f"vintage doc? Human reviewer should verify."),
        detail  = {
            "evidence_doc":     doc_path_raw,
            "has_pit_section":  False,
            "n_obs_months":     n_obs,
        },
    )

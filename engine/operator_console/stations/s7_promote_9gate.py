"""S7 — PROMOTE 9-gate (institutional capital-decision discipline).

The most-rigorous Pipeline Station: 9 sequential gates between a GREEN
FORWARD verdict and a deployed sleeve. Per CLAUDE.md doctrine, capital
decisions stay HUMAN-only — even when all 8 deterministic gates pass,
Gate 9 (human approval) NEVER auto-fires.

Gate inventory (real-implementation status; v14 refactor 2026-06-25):
  Gate 1  ✅ Verdict is GREEN          — IMPLEMENTED (gate1_verdict_green.py)
  Gate 2  ⏳ Cost-robust                — DEFERRED to Phase 2.x
  Gate 3  ✅ PIT clean                  — IMPLEMENTED (gate3_pit_clean.py)
  Gate 4  ⏳ Replication (γ persona)    — DEFERRED to Phase 2.x
  Gate 5  ✅ Multi-period stability     — IMPLEMENTED (gate5_multi_period.py)
  Gate 6  ✅ Anchor-residual            — IMPLEMENTED (gate6_anchor_residual.py)
  Gate 7  ⏳ Cross-sleeve correlation   — DEFERRED (needs WRDS PnL)
  Gate 8  ⏳ Capacity (Pastor-Stambaugh)— DEFERRED to Phase 2.x
  Gate 9  ✅ HUMAN approval              — IMPLEMENTED in this station
                                          (writes promote_proposal row,
                                          routes to /approvals)

Each gate is a focused module under engine/operator_console/gates/.
This station orchestrates them via the registry; the gates themselves
own the statistical / provenance logic. See gates/__init__.py for
the framework + how to add a new gate.

Architectural note (v14): pre-v14 S7 had a critical bug — Gate 1 read
verdict from `ev.get("payload", {}).get("verdict")` but the
research_store schema stores `verdict` at top level of the event row.
Result: all 70 GREEN events in the store hit Gate 1's YELLOW "legacy
event?" fallback, meaning PROMOTE never actually gated on verdict
color. The framework refactor reads the correct field via
gate1_verdict_green.check().

Design reference: docs/architecture/operator_console.md §5 (S7 spec).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import uuid
from pathlib import Path

from engine.operator_console.pipeline_station import (
    PipelineStation,
    SSEEmitter,
    Session,
)
from engine.operator_console.schema import (
    CancellationToken,
    CostEstimate,
    DataTier,
    NextStationHint,
    PreflightCheck,
    PreflightResult,
    PreflightStatus,
    SessionType,
    StationResult,
    StationSpec,
)
from engine.operator_console import emit as opcon_emit
from engine.operator_console import registry


logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVENTS_PATH       = _REPO_ROOT / "data" / "research_store" / "events.jsonl"
_PROPOSALS_PATH    = _REPO_ROOT / "data" / "operator_console" / "promote_proposals.jsonl"


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _find_verdict_event(verdict_event_id: str) -> dict | None:
    if not _EVENTS_PATH.is_file():
        return None
    for line in _EVENTS_PATH.read_text(encoding='utf-8').splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except json.JSONDecodeError:
            continue
        if row.get("event_id") == verdict_event_id:
            return row
    return None


def _write_promote_proposal(proposal: dict) -> str:
    """Append a promote-proposal row + return proposal_id. Drives the
    /approvals UI surface for human review per the capital-decision
    doctrine."""
    _PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _PROPOSALS_PATH.open("a", encoding='utf-8') as f:
        f.write(json.dumps(proposal, ensure_ascii=False) + "\n")
    return proposal["proposal_id"]


# ── The station ──────────────────────────────────────────────────


class Promote9Gate(PipelineStation):
    """S7 — PROMOTE a GREEN verdict to deployment via 9-gate workflow.

    Capital decision lives at Gate 9. S7 prepares the proposal +
    routes to /approvals; the principal hits APPROVE there to actually
    deploy."""

    STATION_SPEC = StationSpec(
        station_id              = "S7_promote_9gate",
        title                   = "PROMOTE 9-gate",
        description             = (
            "Route a GREEN FORWARD verdict through the 9-gate promote "
            "workflow. Gate 1 (GREEN verify) + Gate 9 (HUMAN approval) "
            "are wired; Gates 2-8 (cost-robust / PIT / replication / "
            "multi-period / anchor-residual / cross-sleeve correlation / "
            "capacity) surface as deferred-info in MVP. Capital decision "
            "ALWAYS stays human — S7 never auto-deploys."
        ),
        data_tier               = DataTier.SNAPSHOT_DATA,
        requires_session_types  = {SessionType.RESEARCH_NEW},
        estimated_minutes       = 3,
        estimated_cost_usd      = 0.0,
        icon                    = "ShieldCheck",
        title_key               = "console.station.s7.title",
        description_key         = "console.station.s7.description",
        mutates_capital         = True,   # routes to /approvals via promote_proposals.jsonl
    )

    def preflight(self, session: Session, config: dict) -> PreflightResult:
        checks: list[PreflightCheck] = []

        if not session or not getattr(session, "session_id", ""):
            checks.append(PreflightCheck("session_active", PreflightStatus.RED,
                                         "No active session."))
        else:
            checks.append(PreflightCheck("session_active", PreflightStatus.GREEN,
                                         f"Session {session.session_id} ready."))

        verdict_event_id = str((config or {}).get("verdict_event_id", "")).strip()
        if not verdict_event_id:
            checks.append(PreflightCheck("verdict_event_id_provided", PreflightStatus.RED,
                                         "Provide verdict_event_id."))
            return PreflightResult.from_checks(checks)

        ev = _find_verdict_event(verdict_event_id)
        if ev is None:
            checks.append(PreflightCheck(
                "verdict_event_resolvable", PreflightStatus.RED,
                f"verdict_event_id '{verdict_event_id}' not in events.jsonl.",
            ))
            return PreflightResult.from_checks(checks)

        # Run all 8 gates via the registry for UI feedback. Map each
        # GateResult.status onto a PreflightStatus the launchpad UI
        # already knows how to render.
        from engine.operator_console.gates import GateStatus
        from engine.operator_console.gates.registry import run_all_gates
        gate_status_map = {
            GateStatus.PASS:      PreflightStatus.GREEN,
            GateStatus.SOFT_PASS: PreflightStatus.YELLOW,
            GateStatus.FAIL:      PreflightStatus.RED,
            GateStatus.DEFERRED:  PreflightStatus.YELLOW,
            GateStatus.SKIPPED:   PreflightStatus.YELLOW,
        }
        for gr in run_all_gates(ev, config or {}):
            checks.append(PreflightCheck(
                gr.gate_id,
                gate_status_map.get(gr.status, PreflightStatus.YELLOW),
                gr.summary,
            ))

        # Gate 9: human approval — the GATE (writes proposal), not a check
        checks.append(PreflightCheck(
            "gate9_human_approval", PreflightStatus.YELLOW,
            "Gate 9 = HUMAN approval (per CLAUDE.md capital-decision doctrine). "
            "S7 will NEVER auto-promote — it writes a proposal to /approvals "
            "and requires you to click APPROVE there.",
        ))

        return PreflightResult.from_checks(checks)

    def estimate_cost(self, config: dict) -> CostEstimate:
        return CostEstimate(llm_cost_usd_est=0.0, confidence="exact")

    def render_config_form(self) -> dict:
        return {
            "type": "object",
            "title": "PROMOTE 9-gate input",
            "description": (
                "Promote a GREEN FORWARD verdict to deployment. The "
                "actual deploy happens only after human APPROVE on the "
                "/approvals page — S7 prepares the proposal."
            ),
            "properties": {
                "verdict_event_id": {
                    "type": "string",
                    "title": "verdict_event_id (must be GREEN factor_verdict_filed)",
                    "description": "Pick from S4 result artifacts or /research/lessons.",
                    "x-ui-widget": "text",
                    "x-ui-placeholder": "e.g. 86b4ebac-ef9d-...",
                },
                "target_weight": {
                    "type": "number",
                    "title": "Target weight in book (0.0 — 1.0)",
                    "description": "Suggested allocation; final weight set by human at /approvals.",
                    "default": 0.05,
                    "minimum": 0.0,
                    "maximum": 0.50,
                    "x-ui-widget": "text",
                },
                "role": {
                    "type": "string",
                    "title": "Sleeve role classification",
                    "description": "Per Markowitz/Frazzini-Pedersen baseline: insurance evaluated by crisis_payoff not Sharpe.",
                    "enum": ["alpha", "insurance", "regime_premium", "trend"],
                    "default": "alpha",
                    "x-ui-widget": "select",
                },
                "rationale": {
                    "type": "string",
                    "title": "Promotion rationale (mandatory; goes to /approvals)",
                    "description": "Why does the human reviewer benefit from approving? Lands in the proposal audit trail.",
                    "x-ui-widget": "text-area",
                    "x-ui-rows": 3,
                },
            },
            "required": ["verdict_event_id", "rationale"],
        }

    async def execute(
        self,
        session: Session,
        config: dict,
        emitter: SSEEmitter,
        cancellation: CancellationToken,
    ) -> StationResult:
        started_ts = _utc_iso()
        actor_id = getattr(session, "actor_id", "principal")
        session_id = getattr(session, "session_id", "")
        c = config or {}
        verdict_event_id = str(c.get("verdict_event_id", "")).strip()
        target_weight    = float(c.get("target_weight", 0.05) or 0.05)
        role             = str(c.get("role", "alpha")).strip()
        rationale        = str(c.get("rationale", "")).strip()

        # ── Resolve the verdict event upfront ────────────────────
        ev = _find_verdict_event(verdict_event_id)
        if ev is None:
            return self._failed(session, started_ts, "resolve_verdict",
                                f"verdict_event_id '{verdict_event_id}' not in events.jsonl")

        # ── Gates 1-8: iterate the registry ──────────────────────
        from engine.operator_console.gates import GateStatus, is_blocking
        from engine.operator_console.gates.registry import GATES
        gate_results: list[dict] = []
        blocking_failure: tuple[str, str] | None = None
        for gate_id, title, fn in GATES:
            if cancellation.cancelled:
                return self._cancelled(session, started_ts, gate_id)
            emitter.stage_started(gate_id, expected_seconds=2)
            try:
                r = fn(ev, c)
            except Exception as e:
                emitter.stage_failed(gate_id, f"{type(e).__name__}: {str(e)[:200]}")
                return self._failed(session, started_ts, gate_id,
                                    f"{type(e).__name__}: {str(e)[:200]}")
            gate_results.append({
                "gate_id": gate_id,
                "title":   title,
                "status":  r.status.value,
                "summary": r.summary,
                "detail":  r.detail,
            })
            emitter.stage_completed(gate_id, {
                "status":  r.status.value,
                "summary": r.summary,
            })
            if is_blocking(r.status) and blocking_failure is None:
                # First blocking gate aborts the chain. Return REFUSED
                # immediately — capital decision cannot proceed.
                blocking_failure = (gate_id, r.summary)
                break

        if blocking_failure is not None:
            gate_id, reason = blocking_failure
            return self._refused(session, started_ts, gate_id,
                                 f"blocked at {gate_id}: {reason}",
                                 gate_results=gate_results)

        # ── Gate 9: HUMAN approval — write proposal to /approvals ─
        if cancellation.cancelled:
            return self._cancelled(session, started_ts, "gate9_human_approval")
        emitter.stage_started("gate9_human_approval", expected_seconds=1)
        proposal_id = f"promote_{uuid.uuid4().hex[:12]}"
        # Gates 1-8 audit trail goes into the proposal so /approvals UI
        # + downstream consumers can see exactly what passed / soft-passed
        # / was deferred before the human reviewer was asked
        deferred_gates = [g["gate_id"] for g in gate_results
                          if g["status"] in ("deferred", "skipped")]
        proposal = {
            "proposal_id":      proposal_id,
            "ts":               _utc_iso(),
            "verdict_event_id": verdict_event_id,
            "subject_id":       ev.get("subject_id", ""),
            "target_weight":    target_weight,
            "role":             role,
            "rationale":        rationale[:1000],
            "session_id":       session_id,
            "actor_id":         actor_id,
            "state":            "pending_human_approval",
            "deferred_gates":   deferred_gates,
            "gates_audit":      gate_results,
        }
        try:
            _write_promote_proposal(proposal)
        except Exception as e:
            emitter.stage_failed("gate9_human_approval", str(e)[:300])
            return self._failed(session, started_ts, "gate9_human_approval", str(e)[:300])

        emitter.stage_completed("gate9_human_approval", {
            "proposal_id":     proposal_id,
            "state":           "pending_human_approval",
            "approvals_link":  "/approvals",
            "note":            "Capital decision stays HUMAN. Go to /approvals and click APPROVE to actually deploy.",
        })

        # ── Emit + return ─────────────────────────────────────────
        try:
            opcon_emit.station_completed(
                session_id      = session_id,
                actor_id        = actor_id,
                job_id          = "",
                station_id      = self.STATION_SPEC.station_id,
                cost_actual_usd = 0.0,
                artifacts       = {
                    "proposal_id":      proposal_id,
                    "verdict_event_id": verdict_event_id,
                    "state":            "pending_human_approval",
                },
            )
        except Exception:
            logger.exception("operator_console: failed to emit station_completed")

        return StationResult(
            job_id          = "",
            station_id      = self.STATION_SPEC.station_id,
            session_id      = session_id,
            actor_id        = actor_id,
            started_ts      = started_ts,
            completed_ts    = _utc_iso(),
            success         = True,
            artifacts       = {
                "outcome":          "AWAITING_HUMAN_APPROVAL",
                "proposal_id":      proposal_id,
                "verdict_event_id": verdict_event_id,
                "subject_id":       ev.get("subject_id", ""),
                "target_weight":    str(target_weight),
                "role":             role,
                "next_action":      "Visit /approvals and click APPROVE to deploy",
            },
            events_emitted  = [],
            next_stations   = [],   # human action next, not another station
            cost_actual_usd = 0.0,
        )

    def result_lineage(self, result: StationResult) -> list[NextStationHint]:
        return []

    def _refused(self, session: Session, started_ts: str, stage: str, reason: str,
                 gate_results: list[dict] | None = None) -> StationResult:
        """Refusal = successful execution that hit a gate. Not a failure.

        gate_results is the partial audit trail up to and including the
        blocking gate. Surfaced in artifacts so the UI + /approvals row
        can show exactly what passed before the chain halted."""
        return StationResult(
            job_id          = "",
            station_id      = self.STATION_SPEC.station_id,
            session_id      = getattr(session, "session_id", ""),
            actor_id        = getattr(session, "actor_id", "principal"),
            started_ts      = started_ts,
            completed_ts    = _utc_iso(),
            success         = True,
            artifacts       = {
                "outcome":         "REFUSED_AT_GATE",
                "refused_at":      stage,
                "refusal_reason":  reason,
                "gates_audit":     gate_results or [],
            },
            events_emitted  = [],
            next_stations   = [],
            cost_actual_usd = 0.0,
        )

    def _cancelled(self, session: Session, started_ts: str, stage: str) -> StationResult:
        return StationResult(
            job_id        = "",
            station_id    = self.STATION_SPEC.station_id,
            session_id    = getattr(session, "session_id", ""),
            actor_id      = getattr(session, "actor_id", "principal"),
            started_ts    = started_ts,
            completed_ts  = _utc_iso(),
            success       = False,
            error_message = f"Cancelled at stage '{stage}'.",
        )

    def _failed(self, session: Session, started_ts: str, stage: str, err: str) -> StationResult:
        return StationResult(
            job_id        = "",
            station_id    = self.STATION_SPEC.station_id,
            session_id    = getattr(session, "session_id", ""),
            actor_id      = getattr(session, "actor_id", "principal"),
            started_ts    = started_ts,
            completed_ts  = _utc_iso(),
            success       = False,
            error_message = f"Stage '{stage}' failed: {err}",
        )


registry.register(Promote9Gate)

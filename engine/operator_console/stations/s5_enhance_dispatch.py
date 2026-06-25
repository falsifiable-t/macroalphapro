"""S5 — ENHANCE Dispatch.

Operator Console wrapper around
`engine.research.enhance.dispatcher.dispatch_enhance_hypothesis` —
the second half of the FORWARD vs ENHANCE statistical-separation
doctrine (CLAUDE.md §Forward vs Enhance Statistical Separation).

Counterpart to S4 FORWARD Dispatch. Where S4 answers "is X a real
alpha?" via spanning regressions + DSR, S5 answers "does variant X'
strictly improve deployed sleeve X?" via Politis-Romano paired block
bootstrap + Jobson-Korkie Sharpe-diff.

## The variant_returns wiring (read this BEFORE editing)

`dispatch_enhance_hypothesis` requires `variant_returns: pd.Series` —
a monthly returns series for the proposed modification, aligned by
datetime index to the baseline. Per CLAUDE.md, the LLM-driven variant
builder is Phase 2.2 / 3 deferred (not yet shipped). S5 therefore
takes a **CSV-path input**: the operator prepares a CSV (date,return)
separately and passes the path. S5 loads it, parses to a Series,
calls the dispatcher.

This is the honest MVP — the alternative would be faking a builder
that doesn't exist. When Phase 2.2 ships the LLM builder, S5's
config can gain a `auto_build_from_hypothesis: bool` toggle and the
CSV path becomes optional.

## Cost profile

ZERO LLM cost — the dispatcher is pure stats (bootstrap + correlation
+ verdict classifier). Wall time ~100-500ms depending on
n_iterations. The session cost cap is essentially decorative here;
the trigger is gated by sample-size + correlation checks inside the
dispatcher, not by spend.

## Capital-decision doctrine

mutates_capital=False. S5 writes to
`data/research/enhance_verdicts.jsonl` and emits a
station_completed event. IMPROVEMENT verdicts surface in lineage as
a hint to manually route the proposal to /approvals — there is NO
auto-deploy path. Per CLAUDE.md "Auto-deploying IMPROVEMENT — capital
decisions stay HUMAN; routes to /approvals only."

(S7 PROMOTE currently only branches on FORWARD GREEN verdicts; the
ENHANCE IMPROVEMENT branch is Phase 3 work — see audit_2026-06-24
deferred backlog.)

Design reference: docs/architecture/operator_console.md §5 (S5 spec).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any

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
_HYPOTHESES_PATH = _REPO_ROOT / "data" / "research_store" / "hypotheses.jsonl"


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _load_hypothesis_row(hypothesis_id: str) -> dict[str, Any] | None:
    """Read hypotheses.jsonl, return the latest matching row or None.
    Multiple rows with the same id are possible (amendments); the
    last one wins."""
    if not _HYPOTHESES_PATH.is_file():
        return None
    found: dict[str, Any] | None = None
    try:
        with _HYPOTHESES_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    row = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if row.get("hypothesis_id") == hypothesis_id:
                    found = row
    except OSError:
        return None
    return found


class S5EnhanceDispatch(PipelineStation):
    """Trigger ENHANCE paired-bootstrap dispatch from the console."""

    STATION_SPEC: Any = StationSpec(
        station_id              = "S5_enhance_dispatch",
        title                   = "Dispatch ENHANCE (paired bootstrap)",
        description             = (
            "Run Politis-Romano paired block bootstrap + Jobson-Korkie "
            "Sharpe-diff on a variant returns series vs the deployed sleeve "
            "baseline. Returns IMPROVEMENT / NOISE / DEGRADATION / REFUSED. "
            "Variant returns CSV must be prepared by the operator (LLM "
            "variant builder is Phase 2.2 deferred). No LLM cost — pure stats."
        ),
        data_tier               = DataTier.SNAPSHOT_DATA,
        requires_session_types  = {SessionType.RESEARCH_NEW, SessionType.AUDIT},
        estimated_minutes       = 1,
        estimated_cost_usd      = 0.0,
        icon                    = "Activity",
        title_key               = "console.station.s5.title",
        description_key         = "console.station.s5.description",
        # mutates_capital=False — writes verdict log; IMPROVEMENT routes to
        # /approvals (human) per capital doctrine. Lineage hint surfaces the
        # /approvals path; no auto-promotion code anywhere.
    )

    def preflight(self, session: Session, config: dict[str, Any]) -> PreflightResult:
        checks: list[PreflightCheck] = []

        # ── Session sanity
        if not session or not getattr(session, "session_id", ""):
            checks.append(PreflightCheck(
                "session_active", PreflightStatus.RED,
                "No active session."))
            return PreflightResult.from_checks(checks)
        if getattr(session, "session_type", "") not in {"research_new", "audit"}:
            checks.append(PreflightCheck(
                "session_type", PreflightStatus.RED,
                f"S5 requires research_new or audit session; got "
                f"{getattr(session, 'session_type', '?')}."))
            return PreflightResult.from_checks(checks)
        checks.append(PreflightCheck(
            "session_type", PreflightStatus.GREEN,
            f"{getattr(session, 'session_type')} session active."))

        # ── Required: hypothesis_id
        hypothesis_id = (config.get("hypothesis_id") or "").strip()
        if not hypothesis_id:
            checks.append(PreflightCheck(
                "hypothesis_id", PreflightStatus.RED,
                "hypothesis_id is required."))
            return PreflightResult.from_checks(checks)

        h = _load_hypothesis_row(hypothesis_id)
        if h is None:
            checks.append(PreflightCheck(
                "hypothesis_id", PreflightStatus.RED,
                f"hypothesis_id {hypothesis_id!r} not found in "
                f"hypotheses.jsonl."))
            return PreflightResult.from_checks(checks)

        # ── Classifier doctrine check: must NOT be factor_proposal
        # (those belong in S4 FORWARD per CLAUDE.md doctrine)
        try:
            from engine.research_store.hypothesis.classifier import (
                classify_hypothesis_type,
            )
            htype = classify_hypothesis_type(h)
        except Exception as e:
            htype = "unknown"
            logger.warning("S5 preflight: classifier failed: %s", e)
        if htype == "factor_proposal":
            checks.append(PreflightCheck(
                "hypothesis_class", PreflightStatus.RED,
                f"Hypothesis classifies as 'factor_proposal'; route to S4 "
                f"FORWARD instead. Per CLAUDE.md FORWARD vs ENHANCE doctrine, "
                f"new-alpha claims do NOT belong in the paired-bootstrap "
                f"pipeline (would give false IMPROVEMENT on uncorrelated "
                f"strategies)."))
            return PreflightResult.from_checks(checks)
        if htype == "sleeve_improvement":
            checks.append(PreflightCheck(
                "hypothesis_class", PreflightStatus.GREEN,
                f"Classifier: sleeve_improvement — correct route for S5."))
        else:
            checks.append(PreflightCheck(
                "hypothesis_class", PreflightStatus.YELLOW,
                f"Classifier returned {htype!r}; S5 will run anyway, but "
                f"verify this hypothesis is an enhance-class proposal."))

        # ── sleeve_id: from config override OR from hypothesis.addresses_decay_in
        sleeve_id = (config.get("sleeve_id") or "").strip()
        if not sleeve_id:
            sleeve_id = (h.get("addresses_decay_in") or "").strip()
        if not sleeve_id:
            checks.append(PreflightCheck(
                "sleeve_id", PreflightStatus.RED,
                "sleeve_id not in config AND hypothesis has no "
                "addresses_decay_in field. Either set sleeve_id in config "
                "OR ensure the hypothesis targets a specific deployed sleeve."))
            return PreflightResult.from_checks(checks)
        checks.append(PreflightCheck(
            "sleeve_id", PreflightStatus.GREEN,
            f"sleeve_id={sleeve_id} (source: {'override' if config.get('sleeve_id') else 'hypothesis.addresses_decay_in'})."))

        # ── Required: variant_returns_csv path exists + parseable
        csv_path_raw = (config.get("variant_returns_csv") or "").strip()
        if not csv_path_raw:
            checks.append(PreflightCheck(
                "variant_csv", PreflightStatus.RED,
                "variant_returns_csv path is required. Prepare a CSV with "
                "two columns (date, return); date in YYYY-MM-DD or month-end "
                "format; return as decimal monthly return (e.g. 0.015 for "
                "+1.5%). The LLM variant builder is Phase 2.2 deferred — "
                "until then the operator supplies the series directly."))
            return PreflightResult.from_checks(checks)

        csv_path = Path(csv_path_raw)
        if not csv_path.is_absolute():
            csv_path = _REPO_ROOT / csv_path
        if not csv_path.is_file():
            checks.append(PreflightCheck(
                "variant_csv", PreflightStatus.RED,
                f"CSV not found at {csv_path}."))
            return PreflightResult.from_checks(checks)
        checks.append(PreflightCheck(
            "variant_csv", PreflightStatus.GREEN,
            f"CSV present: {csv_path.name} (will be parsed at execute time)."))

        # ── Bootstrap knobs sanity
        n_iter = config.get("n_iterations", 2000)
        block_size = config.get("block_size", 6)
        if not isinstance(n_iter, int) or not 100 <= n_iter <= 20000:
            checks.append(PreflightCheck(
                "bootstrap_knobs", PreflightStatus.RED,
                f"n_iterations must be int 100-20000; got {n_iter!r}."))
            return PreflightResult.from_checks(checks)
        if not isinstance(block_size, int) or not 1 <= block_size <= 36:
            checks.append(PreflightCheck(
                "bootstrap_knobs", PreflightStatus.RED,
                f"block_size must be int 1-36 (months); got {block_size!r}."))
            return PreflightResult.from_checks(checks)
        checks.append(PreflightCheck(
            "bootstrap_knobs", PreflightStatus.GREEN,
            f"n_iterations={n_iter}, block_size={block_size}mo."))

        return PreflightResult.from_checks(checks)

    def estimate_cost(self, config: dict[str, Any]) -> CostEstimate:
        # Pure stats; the dispatcher does paired bootstrap (CPU) only.
        return CostEstimate(llm_cost_usd_est=0.0, confidence="exact")

    def render_config_form(self) -> dict[str, Any]:
        return {
            "type":  "object",
            "title": "ENHANCE Dispatch configuration",
            "properties": {
                "hypothesis_id": {
                    "type":        "string",
                    "title":       "Hypothesis ID",
                    "description": "UUID from hypotheses.jsonl. Must be a "
                                   "sleeve_improvement hypothesis (NOT a "
                                   "factor_proposal — those go to S4).",
                    "x-ui-widget": "text",
                    "x-ui-placeholder": "e.g. 29e7338c-c2f4-47cb-b3b7-...",
                },
                "sleeve_id": {
                    "type":        "string",
                    "title":       "Sleeve ID (optional)",
                    "description": "Override the target sleeve. Defaults to "
                                   "hypothesis.addresses_decay_in if unset.",
                    "default":     "",
                    "x-ui-widget": "text",
                    "x-ui-placeholder": "e.g. carry_g10",
                },
                "variant_returns_csv": {
                    "type":        "string",
                    "title":       "Variant returns CSV path",
                    "description": "Path to CSV with columns date,return "
                                   "(monthly). Absolute path OR relative to "
                                   "repo root. The operator prepares this "
                                   "separately; LLM variant builder is "
                                   "Phase 2.2 deferred.",
                    "x-ui-widget": "text",
                    "x-ui-placeholder": "e.g. data/variants/my_variant.csv",
                },
                "n_iterations": {
                    "type":        "integer",
                    "title":       "Bootstrap iterations",
                    "description": "Politis-Romano paired bootstrap "
                                   "iterations (default 2000).",
                    "default":     2000,
                    "minimum":     100,
                    "maximum":     20000,
                    "x-ui-widget": "number",
                },
                "block_size": {
                    "type":        "integer",
                    "title":       "Block size (months)",
                    "description": "Circular block size for the paired "
                                   "bootstrap (default 6mo).",
                    "default":     6,
                    "minimum":     1,
                    "maximum":     36,
                    "x-ui-widget": "number",
                },
            },
            "required": ["hypothesis_id", "variant_returns_csv"],
        }

    async def execute(
        self,
        session: Session,
        config: dict[str, Any],
        emitter: SSEEmitter,
        cancellation: CancellationToken,
    ) -> StationResult:
        started_ts = _utc_iso()
        session_id = getattr(session, "session_id", "")
        actor_id   = getattr(session, "actor_id", "principal")
        hypothesis_id = (config.get("hypothesis_id") or "").strip()

        # ── Stage 1: load hypothesis + parse variant CSV
        if cancellation.cancelled:
            return self._cancelled(session, started_ts, "load_inputs")
        emitter.stage_started("load_inputs", expected_seconds=2)

        h = _load_hypothesis_row(hypothesis_id)
        if h is None:
            emitter.stage_failed("load_inputs",
                                 f"hypothesis_id {hypothesis_id!r} not found")
            return self._failed(session, started_ts, "load_inputs",
                                f"hypothesis_id {hypothesis_id!r} not found")

        sleeve_id = ((config.get("sleeve_id") or "").strip()
                     or (h.get("addresses_decay_in") or "").strip())

        csv_path_raw = (config.get("variant_returns_csv") or "").strip()
        csv_path = Path(csv_path_raw)
        if not csv_path.is_absolute():
            csv_path = _REPO_ROOT / csv_path

        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            # Be tolerant about column names — accept any (date, return)
            # pair regardless of case
            cols_lc = {c.lower(): c for c in df.columns}
            date_col = cols_lc.get("date") or list(df.columns)[0]
            ret_col  = cols_lc.get("return") or cols_lc.get("returns") or list(df.columns)[1]
            variant_returns = pd.Series(
                df[ret_col].astype(float).values,
                index=pd.to_datetime(df[date_col]),
                name="variant",
            )
        except Exception as e:
            emitter.stage_failed("load_inputs", f"CSV parse failed: {e}")
            return self._failed(session, started_ts, "load_inputs",
                                f"CSV parse failed: {e}")

        n_obs = len(variant_returns.dropna())
        emitter.stage_completed("load_inputs", {
            "hypothesis_id":     hypothesis_id,
            "sleeve_id":         sleeve_id,
            "variant_csv":       str(csv_path),
            "n_obs_variant":     n_obs,
            "date_range":        (
                f"{variant_returns.index.min().date()} → "
                f"{variant_returns.index.max().date()}"
                if n_obs > 0 else "empty"
            ),
        })

        # ── Stage 2: dispatch (paired bootstrap)
        if cancellation.cancelled:
            return self._cancelled(session, started_ts, "dispatch")
        emitter.stage_started("dispatch", expected_seconds=10)
        emitter.log_line(
            f"Politis-Romano paired bootstrap (n_iter="
            f"{config.get('n_iterations', 2000)}, "
            f"block_size={config.get('block_size', 6)}mo). "
            f"Pure stats — zero LLM cost."
        )

        try:
            from engine.research.enhance.dispatcher import (
                dispatch_enhance_hypothesis,
            )
            result = await asyncio.to_thread(
                dispatch_enhance_hypothesis,
                hypothesis_id   = hypothesis_id,
                sleeve_id       = sleeve_id,
                variant_returns = variant_returns,
                n_iterations    = int(config.get("n_iterations", 2000)),
                block_size      = int(config.get("block_size", 6)),
                cron_run_id     = f"opcon:{session_id}",
                cron_source     = "operator_console",
            )
        except Exception as e:
            emitter.stage_failed("dispatch", str(e)[:300])
            return self._failed(session, started_ts, "dispatch", str(e)[:300])

        verdict          = result.verdict
        refusal_reason   = result.refusal_reason
        bootstrap_dict   = result.bootstrap_result or {}
        emitter.stage_completed("dispatch", {
            "verdict":          verdict,
            "refusal_reason":   refusal_reason,
            "n_obs_baseline":   result.n_obs_baseline,
            "n_obs_variant":    result.n_obs_variant,
            "sharpe_diff":      bootstrap_dict.get("sharpe_diff_observed"),
            "p_value":          bootstrap_dict.get("p_value_two_sided"),
            "correlation":      bootstrap_dict.get("correlation"),
            "summary":          result.summary[:200],
        })

        # ── Stage 3: persist station_completed event
        emitter.stage_started("persist_event", expected_seconds=1)
        try:
            opcon_emit.station_completed(
                session_id      = session_id,
                actor_id        = actor_id,
                job_id          = "",
                station_id      = self.STATION_SPEC.station_id,
                cost_actual_usd = 0.0,
                artifacts       = {
                    "hypothesis_id":    hypothesis_id,
                    "sleeve_id":        sleeve_id,
                    "verdict":          verdict,
                    "refusal_reason":   refusal_reason or "",
                    "dispatch_event_id": result.dispatch_event_id,
                    "verdict_log":      "data/research/enhance_verdicts.jsonl",
                },
            )
        except Exception:
            logger.exception("operator_console: failed to emit station_completed")
        emitter.stage_completed("persist_event", {"verdict": verdict})

        completed_ts = _utc_iso()
        return StationResult(
            job_id          = "",
            station_id      = self.STATION_SPEC.station_id,
            session_id      = session_id,
            actor_id        = actor_id,
            started_ts      = started_ts,
            completed_ts    = completed_ts,
            success         = (verdict != "REFUSED"),
            artifacts       = {
                "hypothesis_id":     hypothesis_id,
                "sleeve_id":         sleeve_id,
                "verdict":           verdict,
                "refusal_reason":    refusal_reason or "",
                "dispatch_event_id": result.dispatch_event_id,
                "bootstrap_result":  bootstrap_dict,
                "n_obs_baseline":    result.n_obs_baseline,
                "n_obs_variant":     result.n_obs_variant,
                "summary":           result.summary,
            },
            events_emitted  = [],   # dispatcher writes to enhance_verdicts.jsonl,
                                    # not the research_store events feed
                                    # (Phase 2.2 will add the canonical event)
            next_stations   = self._next_for(verdict, hypothesis_id),
            cost_actual_usd = 0.0,
            error_message   = (
                f"REFUSED: {refusal_reason}" if verdict == "REFUSED" else ""
            ),
        )

    def result_lineage(self, result: StationResult) -> list[NextStationHint]:
        verdict = str(result.artifacts.get("verdict", ""))
        hypothesis_id = str(result.artifacts.get("hypothesis_id", ""))
        return self._next_for(verdict, hypothesis_id)

    def _next_for(self, verdict: str, hypothesis_id: str) -> list[NextStationHint]:
        # IMPROVEMENT → operator should manually route to /approvals.
        # S7 PROMOTE currently only handles FORWARD GREEN; ENHANCE branch
        # is Phase 3 deferred (see audit_2026-06-24 backlog). The lineage
        # hint surfaces /approvals_proposals.jsonl as the route, even
        # though no auto-write path exists yet.
        if verdict == "IMPROVEMENT":
            return [NextStationHint(
                station_id        = "S7_promote_9gate",
                label             = (
                    "IMPROVEMENT — manually route this variant to "
                    "/approvals (S7-enhance branch is Phase 3 deferred)"
                ),
                suggested_config  = {"hypothesis_id": hypothesis_id},
            )]
        # NOISE / DEGRADATION / REFUSED: no productive next-station. The
        # operator should log the outcome (next session: doctrine_lock
        # via S8b is a fit if the lesson is worth keeping).
        return []

    def _cancelled(self, session: Session, started_ts: str, stage: str) -> StationResult:
        return StationResult(
            job_id        = "",
            station_id    = self.STATION_SPEC.station_id,
            session_id    = getattr(session, "session_id", ""),
            actor_id      = getattr(session, "actor_id", "principal"),
            started_ts    = started_ts,
            completed_ts  = _utc_iso(),
            success       = False,
            error_message = f"cancelled at stage={stage}",
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
            error_message = f"{stage}: {err}",
        )


registry.register(S5EnhanceDispatch)

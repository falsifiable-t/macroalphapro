# Sample data fixtures

This directory is the whitelisted home for **hand-vetted minimal sample
data** that ships with the public snapshot. Per `.publishrc.yaml`, the
live `data/` directory is excluded (per-session research artifacts +
copyrighted papers + WRDS-derived state) — but small frozen fixtures
here let the README's Quick Start commands actually reproduce headline
numbers from a fresh clone.

## Files

### `autopsies_sample.jsonl` — n=101 (97 active, 4 superseded)

Snapshot of `data/research/autopsies.jsonl` as of 2026-06-25 cron
state. Each row is one prediction-verdict autopsy: the system's prior
prediction (committed before the verdict ran), the realized verdict
(GREEN / MARGINAL / RED), the Brier component, and the strategy /
claim family lineage.

**Headline number** when `engine.research.belief_track_record_rigor`
runs on this fixture: overall Brier ≈ **0.366** (n=97), 95% CI
[0.325, 0.408]; consistent with the arxiv preprint v0.9 number
(0.374 at the paper's n=94 snapshot, slightly improved as the cron
adds new autopsies).

**Not sensitive**: UUIDs are internal IDs (no PII); strategy_family
/ claim_family are scientific category labels; brier_component is
pure arithmetic. The autopsy ledger is the public face of the
project's epistemic posture — published failures are the
differentiator, not the secret.

**Why frozen instead of live**: the live cron grows the autopsy
count by ~3-5 rows per week. Freezing this fixture means the
reproducibility script gives identical output on any clone, any
time — no cron-drift surprises.

## How the fixtures are wired

`engine.research.belief_track_record_rigor._load_autopsies()` falls
back to `data/_samples/autopsies_sample.jsonl` when
`data/research/autopsies.jsonl` is absent (the public-snapshot case).
Same logic in `engine.research.belief_ensemble_sweep`.

Result: `python scripts/reports/report_belief_track_record_rigor.py`
on a clean clone produces the documented Brier number; no extra
setup required.

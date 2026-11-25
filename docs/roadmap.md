# Roadmap

Status as of v0.2. "Built" items ship in this repository and are covered by the test suite; "planned" items are
described in [architecture.md](architecture.md) but not implemented here yet.

## v0: Scaffolding and documentation (built)

- Repository structure, terminology, and architectural boundaries.
- Record format, validation, provenance fields, and failure-state conventions.

## v1: Reference capture (partly built)

- Built: a Markdown-vault reference implementation with content-addressed records, idempotent retries,
  schema checks, and heuristic secret screening (`scripts/new-memory.py`).
- Planned: scheduled, bounded ingestion from external sources with resumable watermarks.
- Planned: a quarantine area for rejected candidates that never touches canonical records.

## v2: Recall, selection, and supersession (partly built)

- Built: deterministic lexical recall with tag filters and explicit zero-result reporting (`scripts/recall.py`).
- Built: explicit supersession with cycle and missing-target checks; history excluded unless requested.
- Planned: provenance-aware ranking, stale-record detection, and evaluation fixtures for recall quality.

## v3: Multi-agent review gates (planned)

- A configurable gate ladder for risk-based review.
- Separate proposer, reviewer, and promoter responsibilities.
- Recorded decisions, evidence, policy versions, and escalation paths.
- Human approval points for consequential changes.

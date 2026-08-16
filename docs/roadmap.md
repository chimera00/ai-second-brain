# Roadmap

## v0:  Scaffolding and documentation

- Establish repository structure, terminology, and architectural boundaries.
- Publish example schemas and configuration placeholders.
- Define validation, provenance, and failure-state conventions.

## v1:  Reference capture pipeline

- Add a Markdown-vault reference implementation.
- Schedule bounded ingestion with cron.
- Implement watermarks, idempotency, schema checks, and secret screening.
- Quarantine rejected candidates without modifying canonical knowledge.

## v2:  Recall, selection, and supersession

- Rank eligible knowledge for a query with provenance-aware selection.
- Add explicit supersession relationships and temporal history.
- Detect conflicts, stale records, and unresolved candidates.
- Provide evaluation fixtures for recall quality and failure handling.

## v3:  Multi-agent review gates

- Implement a configurable gate ladder for risk-based review.
- Separate proposer, reviewer, and promoter responsibilities.
- Record decisions, evidence, policy versions, and escalation paths.
- Add human approval points for consequential changes.

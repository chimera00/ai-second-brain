# Architecture

A dependable AI-maintained knowledge base is a controlled publishing system, not a folder that automation may edit freely. Its core pipeline is **capture → screen → gate → promote**. Each stage has a narrow responsibility, produces an inspectable result, and can stop the flow without damaging the canonical store.

## Capture

Capture turns an external observation into a candidate record. Sources may include notes, documents, conversations, feeds, or tool output. A candidate should preserve the original text when permitted, a source identifier, capture time, content hash, and ingestion watermark. The watermark marks how far a source has been processed; the hash makes processing idempotent. If a job retries after interruption, previously accepted input is recognized rather than duplicated.

Capture is deliberately non-authoritative. It records what was observed, not what the system has decided is true. Canonical knowledge remains unchanged until later stages succeed.

## Screen

Screening applies inexpensive, deterministic checks before reasoning or human attention is spent. It validates schema, required metadata, supported formats, size limits, duplicate status, and basic content policy. It also scans captured text for secret-like material: credentials, private keys, session tokens, sensitive identifiers, and configuration fragments that should not become durable memory. Matches are quarantined or rejected; they are not copied into logs or review summaries.

Screening should fail closed. A parser error, unavailable source, or uncertain policy result is not permission to continue. The candidate remains outside the canonical store with a clear failure state.

## Gate

The gate ladder assigns review effort according to consequence. A low-risk formatting correction may pass deterministic validation. A new factual claim may require source checks and model review. A deletion, contradiction, or policy-sensitive change may require explicit human approval. Each higher rung adds evidence or authority; no rung silently waives a failed lower check.

Gate decisions should record the policy version, evidence considered, reviewer type, result, and reason. This audit trail makes later maintenance reproducible and exposes disagreements instead of burying them.

## Promote

Promotion is the only stage allowed to update canonical knowledge. It writes an approved record atomically, attaches provenance, and advances the source watermark only after the write succeeds. Failed promotion leaves the prior state intact and safe to retry.

Knowledge changes through supersession, not silent overwrite. When a newer item replaces an older one, both remain addressable and an explicit relationship identifies the successor, the superseded item, the reason, and the effective time. Recall prefers the current item by default while preserving history for audit, rollback, and temporal questions. Conflicting candidates may remain unresolved until a gate supplies enough evidence.

## The two-poles design probe

Every pipeline component should be tested at two extremes.

At **N = 0**, absence is ambiguous. An empty result could mean there was nothing to process, or it could mean authentication failed, a path was wrong, parsing broke, or a query never ran. Therefore, “empty” must carry proof that the read succeeded: source status, completed pagination, parser outcome, and a checked watermark. Without that proof, the pipeline stops loudly.

At **N = huge**, unbounded work threatens reliability and review quality. Inputs must be processed in bounded batches with stable ordering and resumable watermarks. Any truncation must be explicit, measurable, and visible to operators; silent clipping is data loss. Review quality should be treated as a function of batch size, **quality = f(N)**, not as a constant. Larger batches may require sampling limits, stricter thresholds, more review rungs, or smaller promotion units.

Together, these poles expose the failures hidden by comfortable demo-sized inputs. The resulting system favors evidence over assumption, bounded work over wishful scaling, and durable history over convenient mutation.

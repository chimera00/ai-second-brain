# Capture and recall

The scripts are deliberately small. Their value comes from keeping identity, provenance, history, and retrieval behavior inspectable.

## Write useful records

A strong record answers four questions:

1. What durable fact, decision, procedure, or lesson should remain available?
2. Where did it come from?
3. Who may use it?
4. What would make it stale or superseded?

Keep the content narrow enough to retrieve as one unit. Put detailed source and rights information in the content when it matters; the starter `source` field is only a short label.

## Choose stable metadata

- `--source` should be a short public-safe label such as `manual-note`, `project-guide`, or `published-paper`.
- `--title` should identify the record without embedding a secret or personal locator.
- Repeat `--tag` for a small number of durable concepts. Tags are normalized, deduplicated, and sorted.
- Omit `--title` only when the first nonblank line is an adequate title.

The content hash is the identity. A title or tag does not change that identity, so conflicting metadata for existing content is rejected.

## Capture from a file or stdin

File input is easiest to audit:

```bash
python scripts/new-memory.py \
  --vault my-vault \
  --source project-guide \
  --title "Review before publication" \
  --tag review \
  --input review-note.md
```

Standard input is useful in a pipeline:

```bash
python producer.py | python scripts/new-memory.py \
  --vault my-vault \
  --source generated-summary \
  --tag summary
```

Screen the producer's output before trusting this pattern. The starter's unsafe-input detector is a refusal layer, not a complete data-loss-prevention system.

## Recall before reasoning

```bash
python scripts/recall.py --vault my-vault --limit 5 source review
```

Query terms are unique case-folded word tokens. Each content occurrence scores one point, a title occurrence scores three, a source-label occurrence scores one, and a tag match scores two. Results sort by descending score, then case-folded title, then full ID. Zero-score records do not appear.

All repeated `--tag` filters must match:

```bash
python scripts/recall.py \
  --vault my-vault \
  --tag review \
  --tag publication \
  --json approval
```

## Correct without erasing history

Create the correction as new content and pass the prior record's 64-character ID through `--supersedes`. The target must already exist. The command rejects missing targets and cycles.

After a successful correction:

- the new record appears under `Active`;
- the prior record moves under `Superseded`;
- normal recall hides the prior record; and
- `--include-history` makes both available.

Do not use supersession merely to change a tag or title. Because content identity is stable, metadata conflicts require an explicit review rather than a silent rewrite.

## Interpret exit codes

| Code | Meaning |
|---:|---|
| `0` | completed operation, including a zero-hit recall |
| `2` | command-line usage error |
| `3` | input, vault, or stored-state error |
| `4` | credential-shaped or private input refused |

Errors are content-free. Open the input or vault locally to diagnose; do not paste sensitive material into logs merely because an error omitted it.

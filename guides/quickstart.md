# Quickstart

This guide creates one local memory record and retrieves it. Nothing leaves your machine.

## 1. Check Python

Use Python 3.10 or newer:

```bash
python --version
python scripts/new-memory.py --help
python scripts/recall.py --help
```

## 2. Inspect the synthetic vault

```bash
python scripts/recall.py --vault example-vault source identity
python scripts/recall.py --vault example-vault --json correction history
```

The example vault contains three records. `example-vault/MEMORY.md` is generated from them; it is not a separate source of truth.

## 3. Prepare a note

Create `note.md` as strict UTF-8 text without a byte-order marker:

```markdown
Before changing a durable record, retain its source and current audience.
```

Do not use a real credential, private key, personal contact record, or private infrastructure locator as tutorial content.

## 4. Capture it

```bash
python scripts/new-memory.py \
  --vault my-vault \
  --source manual-note \
  --title "Retain source and audience" \
  --tag provenance \
  --tag maintenance \
  --input note.md
```

A successful command prints a single JSON receipt containing `id`, `path`, and `status`. Repeating the exact command returns `status: unchanged` and leaves the record and index byte-stable. Reusing the same content with different metadata fails rather than changing an existing identity.

## 5. Recall it

```bash
python scripts/recall.py --vault my-vault source audience
python scripts/recall.py --vault my-vault --tag provenance --json source
```

Plain output is a compact summary and never prints record bodies. JSON mode returns the validated record metadata and content for programmatic use.

## 6. Prove the zero-result path

```bash
python scripts/recall.py --vault my-vault term-known-to-be-absent
```

The command should exit `0` and print `No results.` A failed or malformed vault exits `3`; do not treat that as an empty search.

## Next

Read [Capture and recall](capture-and-recall.md) before automating ingestion. Read [Safety and maintenance](safety-and-maintenance.md) before placing sensitive or business-relevant material in a vault.

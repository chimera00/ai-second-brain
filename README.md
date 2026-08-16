# AI Second Brain

An AI second brain should give a model the right note at the right time: not pour every note into every prompt.

This repository is a small, inspectable starting point for a local knowledge vault. It creates content-addressed Markdown records, rebuilds a readable index, preserves superseded history, and retrieves records with deterministic lexical scoring. The starter scripts use only the Python standard library and make no network calls.

## Try the example

Python 3.10 or newer is sufficient.

```bash
python scripts/recall.py --vault example-vault capture contract
python scripts/recall.py --vault example-vault --tag retrieval --json recall
```

The first command prints a compact tab-separated result. The second returns canonical JSON. A successful search with no matches exits `0` and explicitly reports zero results; an unreadable or invalid vault exits nonzero.

## Create a vault

Put the note body in a strict UTF-8 text file, then capture it:

```bash
python scripts/new-memory.py \
  --vault my-vault \
  --source manual-note \
  --title "First durable note" \
  --tag getting-started \
  --input note.md
```

The command returns one JSON receipt. It creates:

```text
my-vault/
├── MEMORY.md
└── memories/
    └── <content-sha256>.md
```

You can also pipe the note body through standard input. One leading UTF-8 byte-order-marker sequence is ignored when the shell transmits the bytes without conversion; a file supplied with `--input` must be strict UTF-8 without a byte-order marker. Windows PowerShell 5.1 otherwise uses a legacy native-pipeline encoding, so set its native output encoding before piping non-ASCII text or a marker:

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

Windows PowerShell may add one UTF-8 preamble to the native pipeline; the capture command ignores that single sequence. Do not prepend another marker in the body. If the shell cannot guarantee lossless UTF-8 transport, prefer `--input` with a UTF-8 file that has no byte-order marker.

Search the new vault:

```bash
python scripts/recall.py --vault my-vault --tag getting-started durable note
```

## What the starter does

- Normalizes Unicode composition and line endings before hashing content.
- Uses the full SHA-256 of normalized content as the record ID and filename.
- Stores one-line canonical JSON metadata inside each Markdown record.
- Rebuilds `MEMORY.md` into active and superseded sections.
- Rejects missing supersession targets, cycles, malformed records, linked-path escapes, and conflicting retries.
- Excludes superseded records from recall unless `--include-history` is set.
- Refuses terminal-control input, common credential-shaped content, private source labels, and empty normalized tags with content-free errors.
- Produces stable lexical ranking without a database, embedding service, or model call.

## What it does not claim

This is a reference implementation, not a complete production memory platform.

- Credential screening is heuristic; it cannot prove that arbitrary text is safe.
- The record write and index write are separately atomic, not one transaction. Retrying the same capture rebuilds the index after an interrupted index update.
- The scripts do not coordinate concurrent writers.
- Records are not encrypted.
- Lexical recall does not understand semantic similarity.
- No benchmark result establishes that this layout is best for every vault.

Use version control or another backup system, restrict access to the vault, and review material before making it public.

## Repository map

```text
scripts/
  new-memory.py       deterministic capture and supersession
  recall.py           deterministic local recall
tests/
  test_scripts.py     behavior and mutation regressions
  test_public_package.py exact inventory and public-safety checks
example-vault/
  MEMORY.md           generated active/history index
  memories/           three synthetic records
  doctrine/RULES.md   compact standing-rule example
guides/
  quickstart.md
  capture-and-recall.md
  safety-and-maintenance.md
```

## Guides

- [Quickstart](guides/quickstart.md)
- [Capture and recall](guides/capture-and-recall.md)
- [Safety and maintenance](guides/safety-and-maintenance.md)

## Tests

Run the suite without leaving bytecode files in the working tree:

```bash
python -B -m unittest discover -s tests -p "test_*.py" -v
```

The tests cover deterministic identity, retry behavior, exact record schemas, active/history indexing, lexical ranking, zero-result behavior, path containment, unsafe-input refusals, malformed-state failures, and supersession cycles.

## Record format

Each file under `memories/` contains:

1. a format marker;
2. a title;
3. one canonical JSON metadata object; and
4. the normalized content.

Metadata fields are closed: `content_sha256`, `id`, `source`, `supersedes`, `tags`, `title`, and `version`. Unknown fields poison the vault instead of being silently ignored.

## License

This companion draft does not select, replace, or assert legal metadata. Publication must retain or add the repository-owner-approved license file; until that approval is bound, distribution rights are unknown.

## The book

This repository is the companion project for *The AI Second Brain: Build a Self-Improving Knowledge Base with AI Agents*, the first book in a series on operating agent memory as a working system.

What the book adds beyond this repository: the reasoning behind each design choice, the incident record that produced the rules, and the copyable operating panels (setup, maintenance, schedulers, supporting agents) in context. Nothing in the book is required to use this repository.

Join the waitlist: [noaifear.ai/ai-second-brain](https://noaifear.ai/ai-second-brain/)

## Architecture and roadmap

See [docs/architecture.md](docs/architecture.md) and [docs/roadmap.md](docs/roadmap.md).

## License

Released under the [MIT License](LICENSE).

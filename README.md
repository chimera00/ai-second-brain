# ai-second-brain

An opinionated reference architecture and working scaffolding for a self-improving, self-maintaining personal knowledge base operated by AI agents.

## What and why

Personal knowledge management methods are plentiful. Reliable, agent-maintained memory is not.

An AI assistant can capture notes, summarize sources, and retrieve documents, but those abilities alone do not produce trustworthy long-term memory. Without explicit controls, a knowledge base accumulates duplicates, loses provenance, overwrites useful history, and quietly treats failed reads as empty results.

`ai-second-brain` is a practical companion project for building a different kind of system: one in which automation can propose, organize, and maintain knowledge while review gates preserve quality and accountability.

## Architecture at a glance

```text
capture -> screen -> gate -> promote
```

- **Capture:** ingest candidate knowledge with source metadata and stable watermarks.
- **Screen:** reject secrets, malformed records, duplicates, and policy violations.
- **Gate:** apply increasingly rigorous review before durable knowledge is accepted.
- **Promote:** write approved knowledge into the canonical store with provenance.

The design is fail-closed: uncertain or incomplete candidates do not enter durable memory. Existing knowledge is superseded with an explicit relationship instead of being silently overwritten. Ingestion is watermarked and idempotent, so a retry does not create a second copy. Every promoted item retains enough provenance to explain where it came from and how it was accepted.

## Quickstart

> Placeholder: installation instructions, example configuration, and a local demonstration will be added with the first working scaffold.

## Roadmap

Development progresses from documentation and repository scaffolding to a reference capture pipeline, a recall and supersession engine, and multi-agent review gates. See [docs/roadmap.md](docs/roadmap.md) for the phased plan.

## The Book

This repository is the companion project for *The AI Second Brain — Build a Self-Improving Knowledge Base with AI Agents, Obsidian, and GitHub*.

Join the waitlist: [noaifear.ai/ai-second-brain](https://noaifear.ai/ai-second-brain/)

## License

Released under the [MIT License](LICENSE).

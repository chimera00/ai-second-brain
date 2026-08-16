# Safety and maintenance

A useful memory vault can also preserve mistakes, secrets, and stale conclusions. Treat maintenance as part of the design.

## Before capture

- Decide whether the material should be durable at all.
- Remove credentials, private keys, access tokens, personal contact details, and private infrastructure locators.
- Record a source that can be reopened.
- State audience, validity, and important limitations in the record when they affect later use.
- Prefer a narrow record over an unreviewed conversation dump.

The starter rejects several credential and private-source shapes, but pattern matching cannot identify every sensitive value. A clean result is not proof of safe content.

## Before recall enters a prompt

- Validate the vault with the recall command.
- Check whether the record is current or superseded.
- Fetch only records relevant to the task.
- Treat a failed search differently from an explicit zero-result response.
- Recheck high-consequence facts against their original sources.

## Preserve durability

Use version control or another backup mechanism around the vault. The scripts write a record atomically and write the rebuilt index atomically, but those are two separate operations. If a process stops between them, retry the same capture; the retry validates records and rebuilds the index.

Avoid concurrent writers. The starter checks paths and stored state, but it does not provide a multi-process lock or a database transaction. A concurrent path replacement remains outside its guarantee.

## Review supersession

Periodically run recall with history enabled:

```bash
python scripts/recall.py --vault my-vault --include-history --json review
```

Confirm that every correction points to the intended earlier record and that no active record states a conclusion you now consider obsolete. Preserve old records for auditability unless a separate deletion policy requires removal.

## Prepare a public release

Create an exact file manifest and scan every declared file, not just the visible index. Review commit metadata and history as well as the current tree. Publish only material whose source, rights, audience, and sanitization disposition are understood.

Do not infer that a passing unit suite proves a release is clean. Seed known-safe and known-unsafe controls, verify that the scanner changes state, and report skipped files as incomplete coverage.

## Recovery checklist

If recall rejects the vault:

1. stop new writes;
2. preserve a copy for diagnosis;
3. inspect the content-free error class;
4. compare record bytes with a known-good version;
5. restore or correct through a reviewed process; and
6. rerun recall before resuming use.

Do not weaken validation just to reopen a damaged vault. A loud failure is safer than silently ranking corrupted records.

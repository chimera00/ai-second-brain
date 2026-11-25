# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**Security → Report a vulnerability** on this repository. Do not open a public issue for a security problem.

Include what you found, how to reproduce it, and the version or commit you tested. You should receive an
acknowledgement within a week.

## Scope

The scripts in this repository read and write a local Markdown vault. They make no network calls and use only
the Python standard library. Reports about path handling, unsafe input that is accepted, credential screening
that misses an obvious secret, or anything that writes outside the vault are all in scope.

## Supported versions

Only the latest release and the `main` branch receive fixes.

## How this repository protects itself

- Every push and pull request runs a full-history secret scan and the test suite.
- CodeQL analyses the Python code and the workflows.
- GitHub Actions are pinned to exact commits and updated through Dependabot.
- Secret scanning with push protection is enabled.

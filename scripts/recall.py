#!/usr/bin/env python3
"""Rank local Markdown memory records with deterministic lexical scoring.

CLI contract
------------
Usage:
  recall.py --vault DIR [--tag TAG ...] [--limit N] [--include-history]
            [--json] QUERY [QUERY ...]

The command validates every record, content digest, contained path, and
supersession chain before returning results. Superseded records are excluded by
default; --include-history restores them. Repeated --tag filters use normalized
tags and all filters must match. The default limit is 10.

Query terms are unique case-folded word tokens. Each term scores one point per
content occurrence, three per title occurrence, one per source-label
occurrence, and two when present in a tag. Zero-score records are excluded.
Results sort by descending score, then case-folded title, then full record ID.

Plain output is a deterministic tab-separated summary and does not include
record bodies. --json emits one canonical object containing the query, count,
and full result records. A successful search with no hits exits 0 and emits
"No results." in plain mode or an explicit zero-count JSON object.

Exit codes: 0 completed search; 2 command-line usage or invalid normalized tag;
3 invalid or unreadable vault state; 4 rejected unsafe-control or
credential-shaped query or tag input.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import stat
import sys
import unicodedata
from collections import Counter
from pathlib import Path


class VaultStateError(Exception):
    pass


class ContentFreeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, "E_USAGE\n")


class _GenericKey:
    """prefix_token where the token carries key-like entropy (uppercase or >=30% digits)."""

    _rx = re.compile(r"\b[A-Za-z0-9]{2,12}_([A-Za-z0-9_-]{24,})\b")

    def search(self, value):
        for m in self._rx.finditer(value):
            tail = m.group(1)
            digits = sum(c.isdigit() for c in tail)
            if any(c.isupper() for c in tail) or digits >= 0.3 * len(tail):
                return m
        return None


CREDENTIAL_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|token|secret|password)\b"
        r"\s*[:=]\s*[\"']?[^\s\"']{12,}"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)[?&](?:token|api[_-]?key|secret|password)=[^&#\s]{8,}"),
    # Vendor-prefixed secrets (all-lowercase real shapes such as sk_live_..., rk_test_..., ghp_...).
    re.compile(
        r"\b(?:sk|rk|pk|whsec|ghp|gho|ghu|ghs|ghr|xox[abpr]|glpat|npm|sq0|SG|"
        r"shpat|shpca|shppa|shpss|dop_v1|doo_v1|dor_v1|hf|pypi-AgEIcHlwaS5vcmc)"
        r"[_.-][A-Za-z0-9_.-]{16,}\b"
    ),
    # AWS access-key ids have no separator: AKIA/ASIA + 16 uppercase alphanumerics.
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    # Long lowercase hex/base32-ish tokens after a short prefix (unallowlisted vendors).
    re.compile(r"\b[a-z]{2,8}_[a-f0-9]{32,}\b"),
    # Generic prefix_token shape: the tail must LOOK like a key (uppercase or >=30% digits),
    # so ordinary snake_case identifiers and file names do not trip it.
    _GenericKey(),
)


def has_credential_shape(value: str) -> bool:
    return any(pattern.search(value) for pattern in CREDENTIAL_PATTERNS)


def has_unsafe_control(value: str, *, allow_layout: bool = False) -> bool:
    allowed = {"\n", "\t"} if allow_layout else set()
    return any(
        character not in allowed and unicodedata.category(character).startswith("C")
        for character in value
    )


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VaultStateError
        result[key] = value
    return result


def load_json_strict(value: str) -> object:
    return json.loads(value, object_pairs_hook=reject_duplicate_keys)


def configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n")


def has_private_source_shape(value: str) -> bool:
    if not value or any(unicodedata.category(character).startswith("C") for character in value):
        return True
    if re.search(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", value):
        return True
    if value.startswith(("/", "\\\\")) or re.match(r"(?i)^[A-Z]:[\\/]", value):
        return True
    if re.search(r"(?i)\bfile:(?://)?[\\/]", value):
        return True
    if re.search(r"(?i)\blocalhost\b", value):
        return True
    candidates = re.findall(
        r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
        value,
    )
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local:
            return True
    ipv6_candidates = set(re.findall(r"\[([0-9A-Fa-f:]+)\]", value))
    ipv6_candidates.update(
        re.findall(
            r"(?i)(?<![0-9a-f:])[0-9a-f:]*:[0-9a-f:]+(?![0-9a-f:])",
            value,
        )
    )
    for candidate in ipv6_candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local:
            return True
    return False


def is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)




def require_contained(root: Path, path: Path) -> None:
    if is_link_like(path):
        raise VaultStateError
    resolved = path.resolve(strict=True)
    if resolved != root and not resolved.is_relative_to(root):
        raise VaultStateError


def parse_record(root: Path, path: Path) -> dict[str, object]:
    require_contained(root, path)
    text = path.read_text(encoding="utf-8", errors="strict")
    header, separator, content = text.partition("\n## Content\n\n")
    lines = header.splitlines()
    if not separator or len(lines) != 6:
        raise VaultStateError
    if lines[0] != "<!-- memory-record-v1 -->" or not lines[1].startswith("# "):
        raise VaultStateError
    if lines[3] != "```json" or lines[5] != "```":
        raise VaultStateError
    metadata = load_json_strict(lines[4])
    if not isinstance(metadata, dict):
        raise VaultStateError
    expected_keys = {"content_sha256", "id", "source", "supersedes", "tags", "title", "version"}
    if (
        set(metadata) != expected_keys
        or type(metadata.get("version")) is not int
        or metadata.get("version") != 1
    ):
        raise VaultStateError
    identifier = metadata.get("id")
    if (
        not isinstance(identifier, str)
        or not re.fullmatch(r"[0-9a-f]{64}", identifier)
        or identifier != path.stem
        or metadata.get("content_sha256") != identifier
    ):
        raise VaultStateError
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != identifier:
        raise VaultStateError
    title = metadata.get("title")
    if not isinstance(title, str) or title != lines[1][2:] or has_unsafe_control(title):
        raise VaultStateError
    source = metadata.get("source")
    if (
        not isinstance(source, str)
        or has_private_source_shape(source)
        or has_credential_shape(source)
        or not isinstance(metadata.get("tags"), list)
    ):
        raise VaultStateError
    tags = metadata["tags"]
    if (
        any(not isinstance(tag, str) or not tag for tag in tags)
        or tags != sorted(set(tags))
        or any(normalize_tag(tag) != tag for tag in tags)
    ):
        raise VaultStateError
    if any(
        has_credential_shape(value)
        for value in (content, title, source, *tags)
    ):
        raise VaultStateError
    if has_unsafe_control(content, allow_layout=True) or any(
        has_unsafe_control(value) for value in (title, source, *tags)
    ):
        raise VaultStateError
    supersedes = metadata.get("supersedes")
    if supersedes is not None and (
        not isinstance(supersedes, str) or not re.fullmatch(r"[0-9a-f]{64}", supersedes)
    ):
        raise VaultStateError
    record = dict(metadata)
    record["content"] = content
    record["path"] = f"memories/{identifier}.md"
    return record


def load_records(vault_argument: Path) -> list[dict[str, object]]:
    requested_vault = vault_argument.absolute()
    if not requested_vault.exists():
        raise VaultStateError
    vault = requested_vault.resolve(strict=True)
    if not vault.is_dir():
        raise VaultStateError
    memories_directory = vault / "memories"
    require_contained(vault, memories_directory)
    if not memories_directory.is_dir():
        raise VaultStateError
    return [parse_record(vault, path) for path in sorted(memories_directory.glob("*.md"))]


def validate_supersession(records: list[dict[str, object]]) -> set[str]:
    links: dict[str, str | None] = {}
    for record in records:
        identifier = str(record["id"])
        supersedes = record.get("supersedes")
        if supersedes is not None and not isinstance(supersedes, str):
            raise VaultStateError
        links[identifier] = supersedes
    for target in links.values():
        if target is not None and target not in links:
            raise VaultStateError
    for start in links:
        seen: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in seen:
                raise VaultStateError
            seen.add(current)
            current = links[current]
    return {target for target in links.values() if target is not None}


def tokens(value: str) -> list[str]:
    # Compose before folding so decomposed text matches the NFC query, and again
    # after folding because casefold can emit decomposed sequences.
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())
    return re.findall(r"[^\W_]+", folded, flags=re.UNICODE)


def normalize_tag(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-_")


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def lexical_score(record: dict[str, object], query_terms: set[str]) -> int:
    content_counts = Counter(tokens(str(record["content"])))
    title_counts = Counter(tokens(str(record["title"])))
    source_counts = Counter(tokens(str(record["source"])))
    tag_terms = {term for tag in record["tags"] for term in tokens(str(tag))}
    return sum(
        content_counts[term]
        + (3 * title_counts[term])
        + source_counts[term]
        + (2 if term in tag_terms else 0)
        for term in query_terms
    )


def build_parser() -> argparse.ArgumentParser:
    parser = ContentFreeArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-history", action="store_true")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--limit", type=positive_integer, default=10)
    parser.add_argument("query", nargs="+")
    return parser


def main(argv: list[str]) -> int:
    configure_standard_streams()
    if any(has_credential_shape(argument) for argument in argv[1:]):
        print("E_CREDENTIAL_SHAPED_INPUT", file=sys.stderr)
        return 4
    args = build_parser().parse_args(argv[1:])
    query = unicodedata.normalize("NFC", " ".join(args.query))
    if has_unsafe_control(query) or any(has_unsafe_control(tag) for tag in args.tag):
        print("E_UNSAFE_INPUT", file=sys.stderr)
        return 4
    if has_credential_shape(query) or any(has_credential_shape(tag) for tag in args.tag):
        print("E_CREDENTIAL_SHAPED_INPUT", file=sys.stderr)
        return 4
    query_terms = set(tokens(query))
    normalized_tags = [normalize_tag(raw_tag) for raw_tag in args.tag]
    if any(not tag for tag in normalized_tags):
        print("E_INVALID_TAG", file=sys.stderr)
        return 2
    required_tags = set(normalized_tags)
    if has_unsafe_control(str(args.vault)):
        print("E_VAULT_STATE", file=sys.stderr)
        return 3
    try:
        records = load_records(args.vault)
        superseded_ids = validate_supersession(records)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError, VaultStateError):
        print("E_VAULT_STATE", file=sys.stderr)
        return 3
    results = []
    for record in records:
        if not args.include_history and str(record["id"]) in superseded_ids:
            continue
        if not required_tags.issubset(set(record["tags"])):
            continue
        score = lexical_score(record, query_terms)
        if score > 0:
            result = dict(record)
            result["score"] = score
            results.append(result)
    results.sort(key=lambda item: (-int(item["score"]), str(item["title"]).casefold(), str(item["id"])))
    results = results[: args.limit]
    payload = {"count": len(results), "query": query, "results": results}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    elif not results:
        print("No results.")
    else:
        print("score\tid\ttitle\tpath")
        for result in results:
            print(f"{result['score']}\t{result['id']}\t{result['title']}\t{result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

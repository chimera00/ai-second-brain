#!/usr/bin/env python3
"""Create a deterministic Markdown memory record in a local vault.

CLI contract
------------
Usage:
  new-memory.py --vault DIR --source LABEL [--title TEXT] [--tag TAG ...]
                [--supersedes ID] [--input FILE]

Input comes from standard input unless --input names a regular, non-linked,
strict UTF-8 file. One leading stdin BOM is ignored; files with a BOM are
rejected. Line endings and Unicode composition are normalized. Empty input is
rejected. Tags are case-folded, punctuation-normalized, deduplicated,
and sorted. Source labels must be one-line public labels, not email addresses,
private network addresses, or absolute private paths.

The record ID is the full SHA-256 digest of normalized content. Records live at
memories/<ID>.md and the generated MEMORY.md index separates active records
from preserved superseded history. A retry with identical content and metadata
is byte-stable; different metadata for an existing content ID is a conflict.
Writes use a temporary file in the destination directory followed by atomic
replacement. The record and index are separate atomic writes; after a failure
between them, retrying the same command rebuilds the index from validated
records.

Success writes one canonical JSON object to stdout with id, relative path, and
status (created or unchanged). Errors are content-free and use exit codes:
0 success; 2 command-line usage or invalid normalized tag; 3 input, vault, or
state error; 4 rejected unsafe-control, credential-shaped, or private source
input. No timestamps are generated.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path


class UnsafePathError(Exception):
    pass


class InvalidSupersedesError(Exception):
    pass


class IdentityConflictError(Exception):
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
EXPECTED_METADATA_KEYS = {
    "content_sha256",
    "id",
    "source",
    "supersedes",
    "tags",
    "title",
    "version",
}


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
            raise ValueError("duplicate metadata key")
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
    for candidate in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", value):
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


def normalize_content(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return value.rstrip("\n") + "\n"


def read_stdin_utf8() -> str:
    raw_bytes = sys.stdin.buffer.read()
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]
    if b"\x00" in raw_bytes:
        raise ValueError("unsupported input")
    return raw_bytes.decode("utf-8", errors="strict")


def normalize_tag(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-_")


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
        raise UnsafePathError
    resolved_parent = path.parent.resolve(strict=True)
    if resolved_parent != root and not resolved_parent.is_relative_to(root):
        raise UnsafePathError
    if path.exists():
        resolved_path = path.resolve(strict=True)
        if resolved_path != root and not resolved_path.is_relative_to(root):
            raise UnsafePathError


def atomic_write(root: Path, path: Path, data: bytes) -> bool:
    require_contained(root, path)
    if path.exists() and path.read_bytes() == data:
        return False
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".write-", delete=False) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        require_contained(root, path)
        os.replace(temporary_name, path)
        temporary_name = None
        return True
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def derive_title(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "Untitled memory")
    first_line = " ".join(unicodedata.normalize("NFC", first_line).split())
    return first_line.lstrip("# ")[:80] or "Untitled memory"


def normalize_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())[:120]


def render_record(
    identifier: str,
    content: str,
    source: str,
    tags: list[str],
    supersedes: str | None,
    title: str,
) -> bytes:
    metadata = {
        "content_sha256": identifier,
        "id": identifier,
        "source": source,
        "supersedes": supersedes,
        "tags": tags,
        "title": title,
        "version": 1,
    }
    metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = (
        "<!-- memory-record-v1 -->\n"
        f"# {title}\n\n"
        "```json\n"
        f"{metadata_json}\n"
        "```\n\n"
        "## Content\n\n"
        f"{content}"
    )
    return text.encode("utf-8")


def parse_record(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="strict")
    header, separator, content = text.partition("\n## Content\n\n")
    lines = header.splitlines()
    if not separator or len(lines) != 6:
        raise ValueError("invalid record")
    if lines[0] != "<!-- memory-record-v1 -->" or not lines[1].startswith("# "):
        raise ValueError("invalid record")
    if lines[3] != "```json" or lines[5] != "```":
        raise ValueError("invalid metadata block")
    metadata = load_json_strict(lines[4])
    if (
        not isinstance(metadata, dict)
        or set(metadata) != EXPECTED_METADATA_KEYS
        or type(metadata.get("version")) is not int
        or metadata.get("version") != 1
    ):
        raise ValueError("invalid metadata schema")
    identifier = metadata.get("id")
    if (
        not isinstance(identifier, str)
        or not re.fullmatch(r"[0-9a-f]{64}", identifier)
        or identifier != path.stem
        or metadata.get("content_sha256") != identifier
    ):
        raise ValueError("invalid record identity")
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != identifier:
        raise ValueError("invalid content digest")
    title = metadata.get("title")
    if (
        not isinstance(title, str)
        or title != lines[1][2:]
        or has_unsafe_control(title)
    ):
        raise ValueError("invalid title")
    source = metadata.get("source")
    if (
        not isinstance(source, str)
        or has_private_source_shape(source)
        or has_credential_shape(source)
    ):
        raise ValueError("invalid metadata")
    tags = metadata.get("tags")
    supersedes = metadata.get("supersedes")
    if (
        not isinstance(tags, list)
        or any(not isinstance(tag, str) or not tag for tag in tags)
        or tags != sorted(set(tags))
        or any(normalize_tag(tag) != tag for tag in tags)
    ):
        raise ValueError("invalid tags")
    if any(has_credential_shape(value) for value in (content, title, source, *tags)):
        raise ValueError("unsafe stored value")
    if has_unsafe_control(content, allow_layout=True) or any(
        has_unsafe_control(value) for value in (title, source, *tags)
    ):
        raise ValueError("unsafe stored value")
    if supersedes is not None and (
        not isinstance(supersedes, str) or not re.fullmatch(r"[0-9a-f]{64}", supersedes)
    ):
        raise ValueError("invalid supersedes")
    return metadata


def load_records(root: Path, memories_directory: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(memories_directory.glob("*.md")):
        require_contained(root, path)
        records.append(parse_record(path))
    return records


def validate_supersession(records: list[dict[str, object]]) -> None:
    links = {str(record["id"]): record.get("supersedes") for record in records}
    for target in links.values():
        if target is not None and target not in links:
            raise ValueError("missing supersession target")
    for start in links:
        seen: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in seen:
                raise ValueError("supersession cycle")
            seen.add(current)
            current = links[current]


def escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def render_index(records: list[dict[str, object]]) -> bytes:
    superseded_ids = {str(record["supersedes"]) for record in records if record.get("supersedes")}

    def append_records(lines: list[str], selected: list[dict[str, object]]) -> None:
        for record in sorted(selected, key=lambda item: (str(item["title"]).casefold(), str(item["id"]))):
            tags = record.get("tags", [])
            tag_text = ", ".join(f"`{tag}`" for tag in tags) if tags else "none"
            identifier = str(record["id"])
            title = escape_link_text(str(record["title"]))
            lines.append(f"- [{title}](memories/{identifier}.md) | `{identifier}` | tags: {tag_text}")

    active = [record for record in records if str(record["id"]) not in superseded_ids]
    history = [record for record in records if str(record["id"]) in superseded_ids]
    lines = ["# Memory Index", "", "## Active", ""]
    append_records(lines, active)
    if not active:
        lines.append("_None._")
    lines.extend(["", "## Superseded", ""])
    append_records(lines, history)
    if not history:
        lines.append("_None._")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = ContentFreeArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--supersedes")
    parser.add_argument("--title")
    parser.add_argument("--input", type=Path)
    return parser


def store_memory(
    vault_argument: Path,
    identifier: str,
    content: str,
    source: str,
    tags: list[str],
    supersedes: str | None,
    title: str,
) -> bool:
    requested_vault = vault_argument.absolute()
    requested_vault.mkdir(parents=True, exist_ok=True)
    vault = requested_vault.resolve(strict=True)
    memories_directory = vault / "memories"
    if memories_directory.exists() and is_link_like(memories_directory):
        raise UnsafePathError
    memories_directory.mkdir(exist_ok=True)
    require_contained(vault, memories_directory)
    index_path = vault / "MEMORY.md"
    require_contained(vault, index_path)
    if index_path.exists() and not index_path.is_file():
        raise ValueError("invalid index target")
    existing_records = load_records(vault, memories_directory)
    validate_supersession(existing_records)
    if supersedes is not None:
        known_ids = {str(record["id"]) for record in existing_records}
        if not re.fullmatch(r"[0-9a-f]{64}", supersedes) or supersedes not in known_ids:
            raise InvalidSupersedesError
        if supersedes == identifier:
            raise InvalidSupersedesError
    record_path = memories_directory / f"{identifier}.md"
    require_contained(vault, record_path)
    record_bytes = render_record(identifier, content, source, tags, supersedes, title)
    if record_path.exists() and record_path.read_bytes() != record_bytes:
        raise IdentityConflictError
    created = atomic_write(vault, record_path, record_bytes)
    atomic_write(vault, index_path, render_index(load_records(vault, memories_directory)))
    return created


def main(argv: list[str]) -> int:
    configure_standard_streams()
    if any(has_credential_shape(argument) for argument in argv[1:]):
        print("E_CREDENTIAL_SHAPED_INPUT", file=sys.stderr)
        return 4
    args = build_parser().parse_args(argv[1:])
    if (
        (args.title is not None and has_unsafe_control(args.title))
        or any(has_unsafe_control(tag) for tag in args.tag)
        or (args.supersedes is not None and has_unsafe_control(args.supersedes))
        or (args.input is not None and has_unsafe_control(str(args.input)))
        or has_unsafe_control(str(args.vault))
    ):
        print("E_UNSAFE_INPUT", file=sys.stderr)
        return 4
    if args.input is None:
        try:
            raw_content = read_stdin_utf8()
        except (OSError, UnicodeDecodeError, ValueError):
            print("E_INPUT_READ", file=sys.stderr)
            return 3
    else:
        try:
            input_path = args.input.absolute()
            input_path = input_path.resolve(strict=True)
            if not input_path.is_file():
                raise OSError("not a file")
            raw_bytes = input_path.read_bytes()
            if raw_bytes.startswith(b"\xef\xbb\xbf") or b"\x00" in raw_bytes:
                raise ValueError("unsupported input")
            raw_content = raw_bytes.decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError, ValueError):
            print("E_INPUT_READ", file=sys.stderr)
            return 3
    content = normalize_content(raw_content)
    if not content.strip():
        print("E_EMPTY_INPUT", file=sys.stderr)
        return 3
    if has_unsafe_control(content, allow_layout=True):
        print("E_UNSAFE_INPUT", file=sys.stderr)
        return 4
    source = unicodedata.normalize("NFC", args.source).strip()
    title = normalize_title(args.title) if args.title is not None else derive_title(content)
    if not title:
        print("E_INVALID_TITLE", file=sys.stderr)
        return 2
    if has_credential_shape(content) or has_credential_shape(source) or has_credential_shape(title) or any(
        has_credential_shape(tag) for tag in args.tag
    ):
        print("E_CREDENTIAL_SHAPED_INPUT", file=sys.stderr)
        return 4
    if has_private_source_shape(source):
        print("E_UNSAFE_SOURCE", file=sys.stderr)
        return 4
    identifier = hashlib.sha256(content.encode("utf-8")).hexdigest()
    normalized_tags = [normalize_tag(raw_tag) for raw_tag in args.tag]
    if any(not tag for tag in normalized_tags):
        print("E_INVALID_TAG", file=sys.stderr)
        return 2
    tags = sorted(set(normalized_tags))
    supersedes = args.supersedes
    try:
        created = store_memory(args.vault, identifier, content, source, tags, supersedes, title)
    except UnsafePathError:
        print("E_UNSAFE_PATH", file=sys.stderr)
        return 3
    except InvalidSupersedesError:
        print("E_INVALID_SUPERSEDES", file=sys.stderr)
        return 3
    except IdentityConflictError:
        print("E_ID_CONFLICT", file=sys.stderr)
        return 3
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        print("E_VAULT_STATE", file=sys.stderr)
        return 3
    print(
        json.dumps(
            {
                "id": identifier,
                "path": f"memories/{identifier}.md",
                "status": "created" if created else "unchanged",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

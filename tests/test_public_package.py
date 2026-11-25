import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
NEW_MEMORY = ROOT / "scripts" / "new-memory.py"
RECALL = ROOT / "scripts" / "recall.py"
VAULT = ROOT / "example-vault"
EXPECTED_FILES = {
    "README.md",
    "LICENSE",
    ".gitignore",
    "docs/architecture.md",
    "docs/roadmap.md",
    "scripts/new-memory.py",
    "scripts/recall.py",
    "tests/test_scripts.py",
    "tests/test_public_package.py",
    ".github/workflows/ci.yml",
    "example-vault/MEMORY.md",
    "example-vault/doctrine/RULES.md",
    "example-vault/memories/00af7bb411212e166657a60c062848e780fce0557af474e3edd87acedd170dcb.md",
    "example-vault/memories/0d8eaf9e8a3467cae49a97af83c5a762efb930708fac14db9efcb048f7bed90e.md",
    "example-vault/memories/49f86acc53274a39b60a0a6104c77b61d89cfb998d577c100bae6a67259a38dc.md",
    "guides/quickstart.md",
    "guides/capture-and-recall.md",
    "guides/safety-and-maintenance.md",
}
BIDI = re.compile(
    "[" + chr(0x202A) + "-" + chr(0x202E) + chr(0x2066) + "-" + chr(0x2069) + "]"
)
# Deny-list terms are shipped only as salted SHA-256 digests of the casefolded, NFC-normalized
# token, so this public file does not disclose the terms it guards against.
DENY_SALT = "ai-second-brain/public-deny/v1:"
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def token_digest(token: str) -> str:
    normalized = unicodedata.normalize("NFC", token).casefold()
    return hashlib.sha256((DENY_SALT + normalized).encode("utf-8")).hexdigest()


def contains_denied_token(text: str, digests) -> bool:
    return any(token_digest(token) in digests for token in TOKEN.findall(text))


PROHIBITED_LANGUAGE_DIGESTS = frozenset({
    "68173eb5628931bd0f2ef85b90fed56f297e4ff34e178c780db17f286eae4a2a",
    "35dd0850c2800358abbc87060e6c724674b52d41b5bcfa31a6cb73f0b621ae1b",
    "e39c82fa7464e0a4d80020ad2c61b58a9e3409cd37100c18b3f360d085018219",
    "c84f643755546b501367d4ed44a0a2e23b796fc04b445760712d745c5aa819a5",
    "fb7fc63a2cbe8fb79483e346ca163ddf69c5f60eca0fcac37a756b104e43063b",
    "0e9b06c1027236691432fb36654a1d01f98dc162f27149323445a984e4c460ea",
    "fece9cf3696fc53f62857567dde5cc069710c247a7197f5041fb2085b15d98db",
    "4db1f4bc4b0ea58b0424f7e80da6ced6b3173687d16e11a59ee7fd32f7d4f42c",
    "9e1860ee444cb921756f404f9cedba6ea2af5fd5f3739efed679d1d7bfa7ccbc",
})
ABSOLUTE_PRIVATE_PATH = re.compile(
    r"(?i)(?:[A-Z]:\\" + "Users" + r"\\|/" + "Users/|/" + "home/|/" + "root/)"
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INTERNAL_LABEL_DIGESTS = frozenset({
    "4be98f451a3975930ae5d3630426653f08ba7dc87f253e29f25ee31779f896d3",
    "82cafe328db2eb1ff1380a0fef6a92d944761dbcd741f20e6fdda85e658a4a3a",
    "99ae66749185cb26476618665b00cb9a23cb16d350114b3880cfc2deabdad187",
    "7bcde2dafbb6c665b19197ab8bb5711dbe5907115f59b65db3bb888eb07570ec",
    "1be595b345ca270930ad453352902db3830948f78f1c5747351c65b2a211cec5",
    "96ea0a78f4855858a504e8697fe5f623968e25721b172b3dd9060450c4b7e936",
    "97afe9f32a4043e9e860dbdf4f7aab1f4ffc180164bc03a03b181eb5e576805e",
    "9dbfc3982853ca9479938b7ba55b0258152e45a7eb7735bc0caf7226c08460df",
})
NODE_NAME = re.compile(r"(?i)\bnode\d+\b")
PUBLIC_SAFETY_PATTERNS = {
    "EMAIL": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "PRIVATE_PATH": ABSOLUTE_PRIVATE_PATH,
    "PRIVATE_IPV4": re.compile(
        r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3}\.)\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b"
    ),
    "CREDENTIAL_ASSIGNMENT": re.compile(
        r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{12,}"
    ),
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "TOKEN_PREFIX": re.compile(
        r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,})\b"
    ),
}


def public_safety_findings(text: str):
    findings = {
        label
        for label, pattern in PUBLIC_SAFETY_PATTERNS.items()
        if pattern.search(text)
    }
    if NODE_NAME.search(text) or contains_denied_token(text, INTERNAL_LABEL_DIGESTS):
        findings.add("INTERNAL_LABEL")
    return findings


def package_files():
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
    )


class PublicPackageTests(unittest.TestCase):
    def test_vcs_excluded_but_pycache_is_counted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            (root / "README.md").write_text("safe\n", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("safe\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "note.txt").write_text("unsafe extra\n", encoding="utf-8")
            with mock.patch.object(sys.modules[__name__], "ROOT", root):
                relative = {path.relative_to(root).as_posix() for path in package_files()}
        self.assertEqual({"README.md", "__pycache__/note.txt"}, relative)

    def test_inventory_is_exact(self):
        files = package_files()
        relative = {path.relative_to(ROOT).as_posix() for path in files}
        self.assertEqual(EXPECTED_FILES, relative)
        for path in ROOT.rglob("*"):
            if ".git" in path.relative_to(ROOT).parts:
                continue
            self.assertFalse(path.is_symlink(), path)
        self.assertEqual([], list(ROOT.rglob("__pycache__")))
        self.assertEqual([], list(ROOT.rglob("*.pyc")))

    def test_files_are_strict_utf8(self):
        for path in package_files():
            raw = path.read_bytes()
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\x00", raw)
                text = raw.decode("utf-8", errors="strict")
                self.assertIsNone(BIDI.search(text))

    def test_markdown_is_public_safe(self):
        for path in package_files():
            if path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertFalse(contains_denied_token(text, PROHIBITED_LANGUAGE_DIGESTS))
                self.assertIsNone(ABSOLUTE_PRIVATE_PATH.search(text))
                self.assertNotRegex(text, r"(?i)\b(?:TODO|TBD|COMING SOON)\b")

    def test_distributed_files_pass_safety_scan(self):
        for path in package_files():
            text = path.read_text(encoding="utf-8", errors="strict")
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(set(), public_safety_findings(text))

    def test_safety_scan_detects_canaries(self):
        canaries = {
            "EMAIL": "person" + "@" + "example.invalid",
            "PRIVATE_PATH": "C:" + "\\" + "Users" + "\\" + "sample" + "\\note.txt",
            "PRIVATE_IPV4": "10." + "20.30.40",
            "CREDENTIAL_ASSIGNMENT": "api_" + "key=" + "x" * 24,
            "PRIVATE_KEY": "-----BEGIN " + "PRIVATE KEY-----",
            "TOKEN_PREFIX": "gh" + "p_" + "x" * 36,
            "INTERNAL_LABEL": "canary" + "label review",
        }
        for expected, value in canaries.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, public_safety_findings(value))

    def test_all_relative_markdown_links_resolve(self):
        for path in package_files():
            if path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK.findall(text):
                target = raw_target.split("#", 1)[0]
                if not target or "://" in target:
                    continue
                resolved = (path.parent / target).resolve()
                with self.subTest(path=path.name, target=target):
                    self.assertTrue(resolved.is_relative_to(ROOT.resolve()))
                    self.assertTrue(resolved.exists())

    def test_example_ids_match_content_hashes(self):
        memories = sorted((VAULT / "memories").glob("*.md"))
        self.assertEqual(3, len(memories))
        for path in memories:
            text = path.read_text(encoding="utf-8")
            metadata_text = text.split("```json\n", 1)[1].split("\n```", 1)[0]
            metadata = json.loads(metadata_text)
            content = text.split("\n## Content\n\n", 1)[1]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            with self.subTest(path=path.name):
                self.assertEqual(path.stem, digest)
                self.assertEqual(metadata["id"], digest)
                self.assertEqual(metadata["content_sha256"], digest)

    def test_example_recall_is_deterministic(self):
        arguments = [sys.executable, "-B", str(RECALL), "--vault", str(VAULT), "--json", "recall"]
        first = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", check=False)
        second = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertGreaterEqual(payload["count"], 1)

    def test_example_records_retry_unchanged_in_a_copy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied = pathlib.Path(temporary_directory) / "vault"
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2])",
                    str(VAULT),
                    str(copied),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            before = {
                path.relative_to(copied).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in copied.rglob("*")
                if path.is_file()
            }
            for record_path in sorted((copied / "memories").glob("*.md")):
                text = record_path.read_text(encoding="utf-8")
                metadata = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
                content = text.split("\n## Content\n\n", 1)[1]
                command = [
                    sys.executable,
                    "-B",
                    str(NEW_MEMORY),
                    "--vault",
                    str(copied),
                    "--source",
                    metadata["source"],
                    "--title",
                    metadata["title"],
                ]
                for tag in metadata["tags"]:
                    command.extend(("--tag", tag))
                result = subprocess.run(
                    command,
                    input=content,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("unchanged", json.loads(result.stdout)["status"])
            after = {
                path.relative_to(copied).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in copied.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

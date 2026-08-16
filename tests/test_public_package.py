import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
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
_PROHIBITED_TERMS = (
    "emp" + "ire",
    "emp" + "ires",
    "imper" + "ial",
    "imper" + "ially",
    "imper" + "ialism",
    "imper" + "ialist",
    "imper" + "ialists",
    "dec" + "ree",
    "dec" + "rees",
)
PROHIBITED_PUBLIC_LANGUAGE = re.compile(
    r"(?i)\b(?:" + "|".join(_PROHIBITED_TERMS) + r")\b"
)
ABSOLUTE_PRIVATE_PATH = re.compile(
    r"(?i)(?:[A-Z]:\\" + "Users" + r"\\|/" + "Users/|/" + "home/|/" + "root/)"
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_INTERNAL_LABELS = (
    "A" + "00",
    "A" + "01",
    "A" + "99",
    "C" + "IC",
    "Nemo" + "Claw",
    "pin" + "na",
    "G" + "WN",
)
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
    "INTERNAL_LABEL": re.compile(
        r"(?i)\b(?:" + "|".join(re.escape(value) for value in _INTERNAL_LABELS) + r"|node\d+)\b"
    ),
}


def public_safety_findings(text: str):
    return {
        label
        for label, pattern in PUBLIC_SAFETY_PATTERNS.items()
        if pattern.search(text)
    }


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
                self.assertIsNone(PROHIBITED_PUBLIC_LANGUAGE.search(text))
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
            "INTERNAL_LABEL": "A" + "01 review",
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

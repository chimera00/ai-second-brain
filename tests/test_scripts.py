import contextlib
import io
import json
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
NEW_MEMORY = ROOT / "scripts" / "new-memory.py"
RECALL = ROOT / "scripts" / "recall.py"


def run_script(script: Path, *arguments: str, input_text: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )


def run_script_bytes(script: Path, *arguments: str, input_bytes: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        input=input_bytes,
        capture_output=True,
        text=False,
        check=False,
    )


def make_directory_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            return False
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.returncode == 0
    return True


def load_script_module(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3], ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_raw_record(vault: Path, *, title: str, content: str, source: str = "manual-note") -> str:
    """Write a valid record verbatim, without the composition normalization new-memory.py applies."""
    identifier = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metadata = {
        "content_sha256": identifier,
        "id": identifier,
        "source": source,
        "supersedes": None,
        "tags": [],
        "title": title,
        "version": 1,
    }
    text = (
        "<!-- memory-record-v1 -->\n"
        f"# {title}\n"
        "\n"
        "```json\n"
        f"{json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
        "```\n"
        "\n## Content\n\n"
        f"{content}"
    )
    memories = vault / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    (memories / f"{identifier}.md").write_bytes(text.encode("utf-8"))
    return identifier


class StarterScriptTests(unittest.TestCase):
    def test_create_uses_content_id_and_builds_index(self):
        expected_id = "c77227db91eaa3d149cbe5c3e4aefe6712152c6b3b75c209ffc8af2fb7cab424"
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--tag",
                "Project Notes",
                "--tag",
                "project-notes",
                input_text="Alpha memory.\r\n",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "id": expected_id,
                    "path": f"memories/{expected_id}.md",
                    "status": "created",
                },
            )
            record = (vault / "memories" / f"{expected_id}.md").read_text(encoding="utf-8")
            self.assertIn('"source":"manual-note"', record)
            self.assertIn('"tags":["project-notes"]', record)
            self.assertTrue(record.endswith("Alpha memory.\n"))
            index = (vault / "MEMORY.md").read_text(encoding="utf-8")
            self.assertIn(f"memories/{expected_id}.md", index)

    def test_new_memory_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            arguments = ("--vault", str(vault), "--source", "manual-note", "--tag", "One")
            first = run_script(NEW_MEMORY, *arguments, input_text="Stable body.\n")
            self.assertEqual(first.returncode, 0, first.stderr)
            identifier = json.loads(first.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            before_record = record_path.read_bytes()
            before_index = (vault / "MEMORY.md").read_bytes()

            second = run_script(NEW_MEMORY, *arguments, input_text="Stable body.\r\n")

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["status"], "unchanged")
            self.assertEqual(record_path.read_bytes(), before_record)
            self.assertEqual((vault / "MEMORY.md").read_bytes(), before_index)
            self.assertEqual(list((vault / "memories").glob("*.md")), [record_path])

    def test_one_stdin_bom_preserves_content_id(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plain_vault = root / "plain"
            bom_vault = root / "bom"
            arguments = ("--source", "manual-note", "--title", "BOM boundary")
            plain = run_script(
                NEW_MEMORY,
                "--vault",
                str(plain_vault),
                *arguments,
                input_text="Stable body.\n",
            )
            with_bom = run_script(
                NEW_MEMORY,
                "--vault",
                str(bom_vault),
                *arguments,
                input_text="\ufeffStable body.\n",
            )

            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(with_bom.returncode, 0, with_bom.stderr)
            plain_id = json.loads(plain.stdout)["id"]
            bom_id = json.loads(with_bom.stdout)["id"]
            self.assertEqual(plain_id, bom_id)
            record = (bom_vault / "memories" / f"{bom_id}.md").read_bytes()
            self.assertNotIn(b"\xef\xbb\xbf", record)

    def test_stdin_bom_and_invalid_bytes_boundary(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            common = ("--source", "manual-note", "--title", "Raw UTF-8 boundary")
            plain = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(root / "plain"),
                *common,
                input_bytes=b"Stable body.\n",
            )
            one_bom = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(root / "one-bom"),
                *common,
                input_bytes=b"\xef\xbb\xbfStable body.\n",
            )
            two_boms = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(root / "two-boms"),
                *common,
                input_bytes=b"\xef\xbb\xbf\xef\xbb\xbfStable body.\n",
            )
            invalid_utf8 = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(root / "invalid"),
                *common,
                input_bytes=b"bad-utf8-\xff",
            )
            nul = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(root / "nul"),
                *common,
                input_bytes=b"bad\x00input",
            )

            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(one_bom.returncode, 0, one_bom.stderr)
            self.assertEqual(
                json.loads(plain.stdout.decode("utf-8"))["id"],
                json.loads(one_bom.stdout.decode("utf-8"))["id"],
            )
            for result in (two_boms, invalid_utf8, nul):
                with self.subTest(stderr=result.stderr):
                    self.assertIn(result.returncode, {3, 4})
                    self.assertEqual(result.stdout, b"")
                    self.assertNotIn(b"Stable body", result.stderr)

    def test_duplicate_metadata_keys_poison_readers(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Original body.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            text = record_path.read_text(encoding="utf-8")
            hidden = "api_" + "key=" + "x" * 24
            needle = '"source":"manual-note"'
            self.assertEqual(text.count(needle), 1)
            record_path.write_text(
                text.replace(needle, '"source":"' + hidden + '",' + needle, 1),
                encoding="utf-8",
                newline="\n",
            )

            recalled = run_script(RECALL, "--vault", str(vault), "Original")
            extended = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Another body.\n",
            )
            for result in (recalled, extended):
                with self.subTest(stderr=result.stderr):
                    self.assertEqual(result.returncode, 3)
                    self.assertNotIn(hidden, result.stderr)
                    self.assertEqual(result.stdout, "")

    def test_metadata_version_is_exact_integer(self):
        for replacement in ("true", "1.0"):
            with self.subTest(replacement=replacement):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    vault = Path(temporary_directory) / "vault"
                    created = run_script(
                        NEW_MEMORY,
                        "--vault",
                        str(vault),
                        "--source",
                        "manual-note",
                        input_text="Original body.\n",
                    )
                    self.assertEqual(created.returncode, 0, created.stderr)
                    identifier = json.loads(created.stdout)["id"]
                    record_path = vault / "memories" / f"{identifier}.md"
                    text = record_path.read_text(encoding="utf-8")
                    self.assertEqual(text.count('"version":1'), 1)
                    record_path.write_text(
                        text.replace('"version":1', '"version":' + replacement, 1),
                        encoding="utf-8",
                        newline="\n",
                    )
                    recalled = run_script(RECALL, "--vault", str(vault), "Original")
                    extended = run_script(
                        NEW_MEMORY,
                        "--vault",
                        str(vault),
                        "--source",
                        "manual-note",
                        input_text="Another body.\n",
                    )
                    self.assertEqual(recalled.returncode, 3)
                    self.assertEqual(extended.returncode, 3)
                    self.assertEqual(recalled.stdout, "")
                    self.assertEqual(extended.stdout, "")

    def test_title_terminal_control_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            unsafe = "Visible" + "\x1b" + "[31m"
            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--title",
                unsafe,
                input_text="Safe body.\n",
            )
            self.assertEqual(result.returncode, 4)
            self.assertIn("E_UNSAFE_INPUT", result.stderr)
            self.assertNotIn(unsafe, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_blank_explicit_title_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--title",
                "      ",
                input_text="Safe body.\n",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("E_INVALID_TITLE", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(vault.exists())

    def test_empty_normalized_tag_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--tag",
                "fruit",
                input_text="Apple note.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            recalled = run_script(
                RECALL,
                "--vault",
                str(vault),
                "--tag",
                "!!!",
                "--json",
                "apple",
            )
            rejected_create = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--tag",
                "!!!",
                input_text="Second note.\n",
            )
            for result in (recalled, rejected_create):
                self.assertEqual(result.returncode, 2)
                self.assertIn("E_INVALID_TAG", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_usage_errors_do_not_echo_values(self):
        sensitive = "person" + "@" + "example.invalid"
        new_result = run_script(NEW_MEMORY, "--unknown-option", sensitive)
        recall_result = run_script(
            RECALL,
            "--vault",
            ".",
            "--limit",
            sensitive,
            "query",
        )
        for result in (new_result, recall_result):
            self.assertEqual(result.returncode, 2)
            self.assertIn("E_USAGE", result.stderr)
            self.assertNotIn(sensitive, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_locale_free_utf8_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            environment = dict(os.environ)
            environment.pop("PYTHONIOENCODING", None)
            environment.pop("PYTHONUTF8", None)
            created = subprocess.run(
                [sys.executable, str(NEW_MEMORY), "--vault", str(vault), "--source", "manual-note"],
                input="Mémoire durable.\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                env=environment,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            for extra in ((), ("--json",)):
                result = subprocess.run(
                    [sys.executable, str(RECALL), "--vault", str(vault), *extra, "mémoire"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    env=environment,
                    check=False,
                )
                with self.subTest(extra=extra):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Mémoire", result.stdout)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell only")
    def test_winps_utf8_matches_raw_identity(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            def capture(vault: Path):
                executable = str(Path(sys.executable)).replace("'", "''")
                script = str(NEW_MEMORY).replace("'", "''")
                vault_text = str(vault).replace("'", "''")
                command = (
                    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                    "$body = 'Stable body.'; "
                    f"$body | & '{executable}' -B '{script}' --vault '{vault_text}' "
                    "--source manual-note --title 'PowerShell boundary'"
                )
                return subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    check=False,
                )

            raw = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(root / "raw"),
                "--source",
                "manual-note",
                "--title",
                "PowerShell boundary",
                input_bytes=b"Stable body.\r\n",
            )
            powershell = capture(root / "powershell")
            self.assertEqual(raw.returncode, 0, raw.stderr)
            self.assertEqual(powershell.returncode, 0, powershell.stderr)
            self.assertEqual(
                json.loads(raw.stdout.decode("utf-8"))["id"],
                json.loads(powershell.stdout)["id"],
            )

    def test_cli_output_is_utf8_lf(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script_bytes(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_bytes="Mémoire durable.\n".encode("utf-8"),
            )
            recalled = run_script_bytes(
                RECALL,
                "--vault",
                str(vault),
                "--json",
                "mémoire",
            )
            for result in (created, recalled):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.endswith(b"\n"))
                self.assertNotIn(b"\r\n", result.stdout)
                result.stdout.decode("utf-8", errors="strict")

    def test_unicode_query_normalization(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Café memory.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            nfc = run_script(RECALL, "--vault", str(vault), "--json", "café")
            nfd = run_script(RECALL, "--vault", str(vault), "--json", "cafe\u0301")
            self.assertEqual(nfc.returncode, 0, nfc.stderr)
            self.assertEqual(nfd.returncode, 0, nfd.stderr)
            nfc_payload = json.loads(nfc.stdout)
            nfd_payload = json.loads(nfd.stdout)
            self.assertEqual(nfc_payload["count"], 1)
            self.assertEqual(nfc_payload, nfd_payload)

    def test_decomposed_record_text_matches_either_query_form(self):
        decomposed = "café"
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            identifier = write_raw_record(
                vault,
                title=f"{decomposed} notes",
                content=f"Decomposed {decomposed} memory.\n",
            )
            for query in ("café", decomposed):
                with self.subTest(query=ascii(query)):
                    result = run_script(RECALL, "--vault", str(vault), "--json", query)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["count"], 1)
                    self.assertEqual(payload["results"][0]["id"], identifier)
                    self.assertEqual(payload["results"][0]["score"], 4)

    def test_composed_record_text_matches_decomposed_query(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            identifier = write_raw_record(vault, title="Plain title", content="Composed café memory.\n")
            result = run_script(RECALL, "--vault", str(vault), "--json", "café")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["results"][0]["id"], identifier)
            self.assertEqual(payload["results"][0]["score"], 1)

    def test_tokens_do_not_split_on_combining_marks(self):
        recall = load_script_module("recall.py")
        self.assertEqual(recall.tokens("Café au lait"), ["café", "au", "lait"])
        self.assertEqual(recall.tokens("Café"), recall.tokens("CAFÉ"))

    def test_unsafe_vault_path_is_a_vault_state_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault\u0007bell"
            result = run_script(RECALL, "--vault", str(vault), "memory")
            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")

    def test_unsafe_query_still_wins_over_unsafe_vault_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault\u0007bell"
            result = run_script(RECALL, "--vault", str(vault), "memory\u0007")
            self.assertEqual(result.returncode, 4)
            self.assertEqual(result.stderr, "E_UNSAFE_INPUT\n")

    def test_deeply_nested_metadata_is_a_vault_state_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            identifier = write_raw_record(vault, title="Nested", content="Nested memory.\n")
            path = vault / "memories" / f"{identifier}.md"
            lines = path.read_text(encoding="utf-8").split("\n")
            depth = 100000
            lines[4] = "[" * depth + "]" * depth
            path.write_bytes("\n".join(lines).encode("utf-8"))
            result = run_script(RECALL, "--vault", str(vault), "memory")
            self.assertEqual(result.returncode, 3, result.stderr[-300:])
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")

    def test_metadata_parser_exhaustion_is_a_vault_state_error(self):
        # Covers interpreters whose json parser raises instead of recursing deeply,
        # and the MemoryError a hostile metadata line can provoke.
        recall = load_script_module("recall.py")
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            write_raw_record(vault, title="Plain", content="Plain memory.\n")
            for error in (RecursionError, MemoryError):
                with self.subTest(error=error.__name__):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with mock.patch.object(recall.json, "loads", side_effect=error), \
                            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        code = recall.main(["recall.py", "--vault", str(vault), "memory"])
                    self.assertEqual(code, 3)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "E_VAULT_STATE\n")

    def test_closed_stdout_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            # Larger than any pipe buffer, so the write must fail once the reader closes.
            write_raw_record(vault, title="Large", content="memory " * 100000 + "\n")
            process = subprocess.Popen(
                [sys.executable, str(RECALL), "--vault", str(vault), "--json", "memory"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process.stdout.close()
            stderr = process.stderr.read().decode("utf-8")
            process.stderr.close()
            code = process.wait(timeout=60)
            self.assertEqual(code, 5, stderr)
            self.assertEqual(stderr, "E_OUTPUT_CLOSED\n")

    def test_broken_pipe_during_output_is_reported_once(self):
        recall = load_script_module("recall.py")

        class ClosedStream(io.StringIO):
            def write(self, text):
                raise BrokenPipeError

        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            write_raw_record(vault, title="Plain", content="Plain memory.\n")
            for extra in ((), ("--json",)):
                with self.subTest(extra=extra):
                    stderr = io.StringIO()
                    with contextlib.redirect_stdout(ClosedStream()), contextlib.redirect_stderr(stderr):
                        code = recall.main(["recall.py", "--vault", str(vault), *extra, "memory"])
                    self.assertEqual(code, 5)
                    self.assertEqual(stderr.getvalue(), "E_OUTPUT_CLOSED\n")

    def test_index_is_insertion_order_independent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_vault = root / "first"
            second_vault = root / "second"
            for body in ("Zulu memory.\n", "Alpha memory.\n"):
                result = run_script(
                    NEW_MEMORY,
                    "--vault",
                    str(first_vault),
                    "--source",
                    "manual-note",
                    input_text=body,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            for body in ("Alpha memory.\n", "Zulu memory.\n"):
                result = run_script(
                    NEW_MEMORY,
                    "--vault",
                    str(second_vault),
                    "--source",
                    "manual-note",
                    input_text=body,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            first_index = (first_vault / "MEMORY.md").read_bytes()
            second_index = (second_vault / "MEMORY.md").read_bytes()
            self.assertEqual(first_index, second_index)
            self.assertEqual(first_index.count(b"memories/"), 2)

    def test_credential_content_fails_closed(self):
        credential_shape = "token=" + "a" * 24
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text=f"Do not store {credential_shape}.\n",
            )

            self.assertEqual(result.returncode, 4)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_CREDENTIAL_SHAPED_INPUT\n")
            self.assertNotIn(credential_shape, result.stderr)
            self.assertFalse(vault.exists())

    def test_private_sources_fail_without_echo(self):
        private_sources = (
            "person" + "@" + "example.invalid",
            "C:" + "\\" + "Users" + "\\" + "sample" + "\\" + "note.md",
            "/" + "home" + "/sample/note.md",
            "10." + "20.30.40",
            "[" + "fd00" + ":" + ":1]",
            "host " + "fd00" + ":" + ":1",
            "file:" + "///" + "home" + "/sample/note.md",
            "manual" + "\t" + "note",
        )
        for source in private_sources:
            with self.subTest(source_kind=private_sources.index(source)):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    vault = Path(temporary_directory) / "vault"
                    result = run_script(
                        NEW_MEMORY,
                        "--vault",
                        str(vault),
                        "--source",
                        source,
                        input_text="Public-safe body.\n",
                    )

                    self.assertEqual(result.returncode, 4)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "E_UNSAFE_SOURCE\n")
                    self.assertNotIn(source, result.stderr)
                    self.assertFalse(vault.exists())

    def test_supersession_preserves_history(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            first = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Earlier guidance.\n",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            earlier_id = json.loads(first.stdout)["id"]

            second = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--supersedes",
                earlier_id,
                input_text="Current guidance.\n",
            )

            self.assertEqual(second.returncode, 0, second.stderr)
            current_id = json.loads(second.stdout)["id"]
            self.assertTrue((vault / "memories" / f"{earlier_id}.md").is_file())
            current_record = (vault / "memories" / f"{current_id}.md").read_text(encoding="utf-8")
            self.assertIn(f'"supersedes":"{earlier_id}"', current_record)
            index = (vault / "MEMORY.md").read_text(encoding="utf-8")
            active, superseded = index.split("## Superseded", maxsplit=1)
            self.assertIn(current_id, active)
            self.assertNotIn(earlier_id, active)
            self.assertIn(earlier_id, superseded)

    def test_metadata_conflict_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            first = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "source-one",
                input_text="Shared body.\n",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            identifier = json.loads(first.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            before_record = record_path.read_bytes()
            before_index = (vault / "MEMORY.md").read_bytes()

            conflicting = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "source-two",
                input_text="Shared body.\n",
            )

            self.assertEqual(conflicting.returncode, 3)
            self.assertEqual(conflicting.stdout, "")
            self.assertEqual(conflicting.stderr, "E_ID_CONFLICT\n")
            self.assertEqual(record_path.read_bytes(), before_record)
            self.assertEqual((vault / "MEMORY.md").read_bytes(), before_index)

    def test_input_file_is_deterministic(self):
        expected_id = "c77227db91eaa3d149cbe5c3e4aefe6712152c6b3b75c209ffc8af2fb7cab424"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "candidate.md"
            input_path.write_text("Alpha memory.\r\n", encoding="utf-8", newline="")
            vault = root / "vault"

            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--title",
                "Chosen [Title]",
                "--input",
                str(input_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["id"], expected_id)
            record = (vault / "memories" / f"{expected_id}.md").read_text(encoding="utf-8")
            self.assertIn("# Chosen [Title]\n", record)
            index = (vault / "MEMORY.md").read_text(encoding="utf-8")
            self.assertIn("[Chosen \\[Title\\]]", index)

    def test_linked_memories_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            vault = root / "vault"
            outside = root / "outside"
            vault.mkdir()
            outside.mkdir()
            if not make_directory_link(vault / "memories", outside):
                self.skipTest("directory links and junctions unavailable")

            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Contained body.\n",
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_UNSAFE_PATH\n")
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((vault / "MEMORY.md").exists())

    def test_linked_ancestors_resolve_to_the_real_vault(self):
        # A vault reached through a linked ANCESTOR (macOS /tmp and /var are
        # symlinks) is the same real directory. It must work, and every write
        # must land in the resolved vault, never beside the link.
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside"
            input_directory = outside / "inputs"
            vault = outside / "vault"
            input_directory.mkdir(parents=True)
            input_path = input_directory / "note.md"
            input_path.write_text("Linked input body.\n", encoding="utf-8", newline="\n")
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Existing memory.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            records_before = set((vault / "memories").glob("*.md"))
            alias = root / "alias"
            if not make_directory_link(alias, outside):
                self.skipTest("directory links and junctions unavailable")

            stored = run_script(
                NEW_MEMORY,
                "--vault",
                str(alias / "vault"),
                "--source",
                "manual-note",
                input_text="Second memory via alias.\n",
            )
            self.assertEqual(stored.returncode, 0, stored.stderr)
            records_after = set((vault / "memories").glob("*.md"))
            self.assertEqual(len(records_after - records_before), 1)
            self.assertFalse((root / "vault").exists())

            recalled = run_script(RECALL, "--vault", str(alias / "vault"), "existing")
            self.assertEqual(recalled.returncode, 0, recalled.stderr)
            self.assertIn("Existing memory.", recalled.stdout)

            read_input = run_script(
                NEW_MEMORY,
                "--vault",
                str(root / "fresh-vault"),
                "--source",
                "manual-note",
                "--input",
                str(alias / "inputs" / "note.md"),
            )
            self.assertEqual(read_input.returncode, 0, read_input.stderr)
            self.assertTrue((root / "fresh-vault" / "memories").is_dir())

    def test_invalid_index_target_fails_before_write(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            (vault / "memories").mkdir(parents=True)
            (vault / "MEMORY.md").mkdir()

            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Do not partially write.\n",
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertEqual(list((vault / "memories").iterdir()), [])

    def test_recall_json_ranking_and_zero_exclusion(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            identifiers = []
            for body in ("Orchard apple apple.\n", "Apple banana.\n", "Banana only.\n"):
                created = run_script(
                    NEW_MEMORY,
                    "--vault",
                    str(vault),
                    "--source",
                    "manual-note",
                    input_text=body,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                identifiers.append(json.loads(created.stdout)["id"])

            result = run_script(RECALL, "--vault", str(vault), "--json", "apple")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 2)
            self.assertEqual([item["id"] for item in payload["results"]], identifiers[:2])
            self.assertEqual([item["score"] for item in payload["results"]], [8, 4])
            self.assertEqual(payload["results"][0]["content"], "Orchard apple apple.\n")

    def test_recall_history_filter(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            earlier = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Earlier guidance.\n",
            )
            self.assertEqual(earlier.returncode, 0, earlier.stderr)
            earlier_id = json.loads(earlier.stdout)["id"]
            current = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--supersedes",
                earlier_id,
                input_text="Current guidance.\n",
            )
            self.assertEqual(current.returncode, 0, current.stderr)
            current_id = json.loads(current.stdout)["id"]

            default = run_script(RECALL, "--vault", str(vault), "--json", "guidance")
            history = run_script(
                RECALL,
                "--vault",
                str(vault),
                "--json",
                "--include-history",
                "guidance",
            )

            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertEqual([item["id"] for item in json.loads(default.stdout)["results"]], [current_id])
            self.assertEqual(history.returncode, 0, history.stderr)
            self.assertEqual(
                {item["id"] for item in json.loads(history.stdout)["results"]},
                {earlier_id, current_id},
            )

    def test_recall_zero_results_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Known topic.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            plain = run_script(RECALL, "--vault", str(vault), "absent-term")
            structured = run_script(RECALL, "--vault", str(vault), "--json", "absent-term")

            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(plain.stdout, "No results.\n")
            self.assertEqual(structured.returncode, 0, structured.stderr)
            self.assertEqual(
                json.loads(structured.stdout),
                {"count": 0, "query": "absent-term", "results": []},
            )

    def test_recall_plain_output_is_stable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Orchard apple apple.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]

            result = run_script(RECALL, "--vault", str(vault), "apple")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "score\tid\ttitle\tpath\n"
                f"8\t{identifier}\tOrchard apple apple.\tmemories/{identifier}.md\n",
            )

    def test_recall_tag_filter_and_limit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            identifiers = []
            rows = (
                ("Apple one.\n", "Research Notes"),
                ("Apple apple other.\n", "Other"),
                ("Apple apple apple.\n", "research-notes"),
            )
            for body, tag in rows:
                created = run_script(
                    NEW_MEMORY,
                    "--vault",
                    str(vault),
                    "--source",
                    "manual-note",
                    "--tag",
                    tag,
                    input_text=body,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                identifiers.append(json.loads(created.stdout)["id"])

            result = run_script(
                RECALL,
                "--vault",
                str(vault),
                "--json",
                "--tag",
                "Research Notes",
                "--limit",
                "1",
                "apple",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["results"][0]["id"], identifiers[2])

    def test_recall_credential_query_fails_closed(self):
        credential_shape = "token=" + "b" * 24
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Safe body.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            result = run_script(RECALL, "--vault", str(vault), credential_shape)

            self.assertEqual(result.returncode, 4)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_CREDENTIAL_SHAPED_INPUT\n")
            self.assertNotIn(credential_shape, result.stderr)

    def test_unknown_secret_argument_fails_before_parse(self):
        credential_argument = "--token=" + "a" * 24
        for script in (NEW_MEMORY, RECALL):
            with self.subTest(script=script.name):
                result = run_script(script, credential_argument)

                self.assertEqual(result.returncode, 4)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "E_CREDENTIAL_SHAPED_INPUT\n")
                self.assertNotIn(credential_argument, result.stderr)

    def test_create_rejects_tampered_record(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Original body.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            record_path.write_text(
                record_path.read_text(encoding="utf-8").replace("Original body.", "Altered body."),
                encoding="utf-8",
                newline="\n",
            )
            before_index = (vault / "MEMORY.md").read_bytes()

            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Second body.\n",
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertEqual(len(list((vault / "memories").glob("*.md"))), 1)
            self.assertEqual((vault / "MEMORY.md").read_bytes(), before_index)

    def test_empty_input_does_not_create_vault(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="\r\n  \t\r\n",
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_EMPTY_INPUT\n")
            self.assertFalse(vault.exists())

    def test_derived_title_collapses_whitespace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Title\twith   spacing.\nBody text.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]

            result = run_script(RECALL, "--vault", str(vault), "title")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "score\tid\ttitle\tpath\n"
                f"4\t{identifier}\tTitle with spacing.\tmemories/{identifier}.md\n",
            )

    def test_recall_rejects_malformed_record_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Typed metadata.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            record_path.write_text(
                record_path.read_text(encoding="utf-8").replace('"tags":[]', '"tags":[{}]'),
                encoding="utf-8",
                newline="\n",
            )

            result = run_script(RECALL, "--vault", str(vault), "metadata")

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")

    def test_noncanonical_tags_poison_vault(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--tag",
                "project-notes",
                input_text="Canonical metadata.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            lines = record_path.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[4])
            metadata["tags"] = ["Project Notes"]
            lines[4] = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            record_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            recalled = run_script(RECALL, "--vault", str(vault), "canonical")
            extended = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="A second memory.\n",
            )

            for result in (recalled, extended):
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertEqual(len(list((vault / "memories").glob("*.md"))), 1)

    def test_unknown_metadata_key_poisons_vault(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Exact metadata schema.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            lines = record_path.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[4])
            metadata["unexpected"] = "safe-value"
            lines[4] = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            record_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            recalled = run_script(RECALL, "--vault", str(vault), "metadata")
            extended = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="A second memory.\n",
            )

            for result in (recalled, extended):
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertEqual(len(list((vault / "memories").glob("*.md"))), 1)

    def test_create_rejects_supersession_cycle(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            first = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="First state.\n",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_id = json.loads(first.stdout)["id"]
            second = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--supersedes",
                first_id,
                input_text="Second state.\n",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            second_id = json.loads(second.stdout)["id"]
            first_path = vault / "memories" / f"{first_id}.md"
            lines = first_path.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[4])
            metadata["supersedes"] = second_id
            lines[4] = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            first_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            before_index = (vault / "MEMORY.md").read_bytes()

            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Third state.\n",
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertEqual(len(list((vault / "memories").glob("*.md"))), 2)
            self.assertEqual((vault / "MEMORY.md").read_bytes(), before_index)

    def test_recall_rejects_unsafe_source(self):
        unsafe_source = "person" + "@" + "example.invalid"
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Metadata boundary.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            lines = record_path.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[4])
            metadata["source"] = unsafe_source
            lines[4] = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

            result = run_script(RECALL, "--vault", str(vault), "--json", "metadata")

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertNotIn(unsafe_source, result.stderr)

    def test_create_rejects_unsafe_source(self):
        unsafe_source = "person" + "@" + "example.invalid"
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="Metadata boundary.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            lines = record_path.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[4])
            metadata["source"] = unsafe_source
            lines[4] = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            record_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            index_before = (vault / "MEMORY.md").read_bytes()

            result = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="A second memory.\n",
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "E_VAULT_STATE\n")
            self.assertNotIn(unsafe_source, result.stderr)
            self.assertEqual(len(list((vault / "memories").glob("*.md"))), 1)
            self.assertEqual((vault / "MEMORY.md").read_bytes(), index_before)

    def test_stored_credential_is_never_emitted(self):
        credential_shape = "token=" + "a" * 24
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault = Path(temporary_directory) / "vault"
            created = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                "--title",
                "Imported note",
                input_text="Safe initial body.\n",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            identifier = json.loads(created.stdout)["id"]
            record_path = vault / "memories" / f"{identifier}.md"
            lines = record_path.read_text(encoding="utf-8").splitlines()
            content = f"Stored {credential_shape}.\n"
            mutated_id = hashlib.sha256(content.encode("utf-8")).hexdigest()
            metadata = json.loads(lines[4])
            metadata["content_sha256"] = mutated_id
            metadata["id"] = mutated_id
            lines[4] = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            lines[-1] = content.rstrip("\n")
            mutated_path = record_path.with_name(f"{mutated_id}.md")
            record_path.replace(mutated_path)
            mutated_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            recalled = run_script(
                RECALL,
                "--vault",
                str(vault),
                "--json",
                "stored",
            )
            extended = run_script(
                NEW_MEMORY,
                "--vault",
                str(vault),
                "--source",
                "manual-note",
                input_text="A second memory.\n",
            )

            for result in (recalled, extended):
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "E_VAULT_STATE\n")
                self.assertNotIn(credential_shape, result.stderr)
            self.assertEqual(len(list((vault / "memories").glob("*.md"))), 1)


class CredentialPatternDirectionTests(unittest.TestCase):
    """Pattern #6 must catch real vendor/high-entropy keys AND pass ordinary snake_case.
    Fixtures are ASSEMBLED at runtime so no key-shaped literal ships in this file
    (the package safety scan would otherwise flag it, correctly)."""

    def _mod(self):
        import importlib.util
        mods = []
        for name in ("recall.py", "new-memory.py"):
            spec = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3], ROOT / "scripts" / name)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mods.append(mod)
        return mods

    def test_real_key_shapes_are_caught(self):
        upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        lower = "abcdefghijklmnopqrstuvwxyz"
        digits = "0123456789"
        shapes = (
            "_".join(["sk", "live", "4eC39HqLyjWDarjt" + "T1zdp7dc"]),
            "_".join(["rk", "test", "51H8" + lower[:24]]),
            "_".join(["ghp", upper + digits]),
            "-".join(["xoxb", digits, lower[:16]]),
            "_".join(["my", "Prefix", upper[:8] + lower[8:26] + digits[1:5]]),
            "_".join(["tok", (digits * 3)[:28]]),
            "AKIA" + (upper + digits)[:16],
            "ASIA" + (digits + upper)[:16],
            "_".join(["shpat", lower + "abcdef"]),
            "_".join(["shpca", lower + "abcdef"]),
            "_".join(["dop_v1", lower + "abcdef"]),
            "_".join(["tok", ("0123456789abcdef" * 2)]),
        )
        for m in self._mod():
            for real in shapes:
                self.assertTrue(m.has_credential_shape(real), real)

    def test_snake_case_names_and_paths_pass(self):
        for m in self._mod():
          for benign in (
            "notes_on_gardening_tools_and_seasonal_planting_schedules",
            "vaults/my_second_brain_vault_directory_for_notes",
            "reference_recipe_index_notes_and_more_words_here",
            "long_ordinary_identifier_with_many_lowercase_parts_only",
          ):
            self.assertFalse(m.has_credential_shape(benign), benign)


if __name__ == "__main__":
    unittest.main()

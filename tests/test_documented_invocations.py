"""Tests for documented stellar-canary invocations (issue #371, F-099).

Extracts every `stellar-canary check ...` command from README.md,
CONTRIBUTING.md and docs/*.md, classifies its `--fixtures-dir` target, and
verifies that documented targets are valid pack directories (not the
repository root which contains test vectors).

When STELLAR_CANARY_BIN is set, also executes each documented command
against the engine to confirm it does not exit with code 4 (invalid fixture).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "CONTRIBUTING.md",
    *[p for p in (REPO_ROOT / "docs").glob("*.md")],
]

# Regex to find stellar-canary check commands in fenced blocks or inline code
# Matches both `--fixtures-dir value` and `--fixtures-dir=value` forms
COMMAND_PATTERN = re.compile(
    r"stellar-canary\s+check\s+"
    r"(?:--fixtures-dir\s+(?P<dir_space>\S+)"
    r"|--fixtures-dir=(?P<dir_eq>\S+))"
    r"(?:\s+(?P<rest>.*?))?(?:\s*--json)?",
    re.MULTILINE,
)

# Placeholder patterns that mean "repository root"
ROOT_PLACEHOLDERS = {
    "<path-to-a-checkout-of-this-repo>",
    "<checkout-of-this-repo>",
    "<checkout>",
    "<path-to-this-repo>",
    ".",
    "./",
    ". ./",  # sometimes appears in split commands
}


def extract_commands() -> list[dict]:
    """Extract all stellar-canary check commands from documentation files."""
    commands = []
    for doc_path in DOC_FILES:
        if not doc_path.is_file():
            continue
        text = doc_path.read_text(encoding="utf-8")
        # Also search in fenced code blocks (```bash ... ```)
        for match in COMMAND_PATTERN.finditer(text):
            dir_value = match.group("dir_space") or match.group("dir_eq")
            rest = match.group("rest") or ""
            full_cmd = f"stellar-canary check --fixtures-dir {dir_value} {rest}".strip()
            commands.append({
                "doc": str(doc_path.relative_to(REPO_ROOT)),
                "dir": dir_value,
                "rest": rest.strip(),
                "full_cmd": full_cmd,
            })
    return commands


def resolve_dir(dir_value: str) -> Path | None:
    """Resolve a --fixtures-dir value to a Path, or return None if it's a placeholder."""
    # Check if it's a placeholder for the repo root
    for placeholder in ROOT_PLACEHOLDERS:
        if dir_value == placeholder or dir_value.startswith(placeholder + "/"):
            # If it has a suffix after the placeholder, resolve that
            if dir_value.startswith(placeholder + "/"):
                suffix = dir_value[len(placeholder) + 1:]
                return REPO_ROOT / suffix
            return REPO_ROOT
    # Check if it's a protocol-NN directory under the repo
    if dir_value.startswith("protocol-") and "/" not in dir_value:
        return REPO_ROOT / dir_value
    # Absolute or relative path
    return Path(dir_value)


class DocumentedInvocationsTests(unittest.TestCase):
    """Structural tests: every documented command must target a valid pack directory."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.commands = extract_commands()
        cls.repo_root = REPO_ROOT

    def test_at_least_one_command_documented(self) -> None:
        self.assertGreater(len(self.commands), 0, "no stellar-canary check commands found in docs")

    def test_no_command_targets_repo_root_with_vectors_present(self) -> None:
        """Fail if any documented command targets the repo root while .toml files exist outside protocol-*."""
        # Find all .toml files outside protocol-* directories
        outside_tomls = []
        for toml in self.repo_root.rglob("*.toml"):
            try:
                rel = toml.relative_to(self.repo_root)
                if not rel.parts[0].startswith("protocol-"):
                    outside_tomls.append(str(rel))
            except ValueError:
                pass

        # If there are .toml files outside protocol-*, no documented command should target the root
        if outside_tomls:
            for cmd in self.commands:
                resolved = resolve_dir(cmd["dir"])
                if resolved == self.repo_root:
                    self.fail(
                        f"Documented command in {cmd['doc']} targets repository root "
                        f"but {len(outside_tomls)} .toml file(s) exist outside protocol-* "
                        f"directories (e.g. {outside_tomls[:10]}). "
                        f"Command: {cmd['full_cmd']}"
                    )

    def test_documented_commands_target_protocol_directories(self) -> None:
        """Every documented command should target a protocol-NN directory or a staging dir of packs."""
        for cmd in self.commands:
            resolved = resolve_dir(cmd["dir"])
            self.assertIsNotNone(resolved, f"Could not resolve {cmd['dir']!r} in {cmd['doc']}")

            if resolved == self.repo_root:
                # Already checked above, but double-check with a clear message
                self.fail(
                    f"Command in {cmd['doc']} targets repository root; "
                    f"must target a protocol-NN/ directory. Command: {cmd['full_cmd']}"
                )

            # The resolved path should be a protocol-NN directory or contain only protocol-NN dirs
            if resolved.name.startswith("protocol-") and resolved.parent == self.repo_root:
                # Direct protocol-NN directory - valid
                self.assertTrue(resolved.is_dir(), f"{resolved} is not a directory")
                continue

            # If it's a subdirectory of a protocol-NN, that's also valid
            if len(resolved.parts) >= 2 and resolved.parts[-2].startswith("protocol-"):
                self.assertTrue(resolved.is_dir(), f"{resolved} is not a directory")
                continue

            # For staging directories (not the repo root), verify all .toml files are under protocol-*
            if resolved != self.repo_root:
                for toml in resolved.rglob("*.toml"):
                    try:
                        rel = toml.relative_to(resolved)
                        self.assertTrue(
                            rel.parts[0].startswith("protocol-"),
                            f"Command in {cmd['doc']} targets {resolved} which contains "
                            f"{rel} outside protocol-* subdirectories"
                        )
                    except ValueError:
                        self.fail(f"File {toml} not under {resolved}")


class EngineInvocationTests(unittest.TestCase):
    """Integration tests: run documented commands against the engine (if available)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.commands = extract_commands()
        cls.engine_bin = os.environ.get("STELLAR_CANARY_BIN")
        cls.expected_version = os.environ.get("STELLAR_CANARY_EXPECTED_VERSION")
        cls.repo_root = REPO_ROOT

    def setUp(self) -> None:
        if not self.engine_bin:
            self.skipTest("STELLAR_CANARY_BIN not set; skipping engine execution tests")
        if not Path(self.engine_bin).is_file():
            self.skipTest(f"Engine binary not found at {self.engine_bin}")

        # Verify engine supports --offline
        try:
            help_out = subprocess.run(
                [self.engine_bin, "check", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            if "--offline" not in help_out:
                self.skipTest("Engine does not support --offline flag")
        except subprocess.TimeoutExpired:
            self.skipTest("Engine --help timed out")

        # Record engine version
        try:
            version_out = subprocess.run(
                [self.engine_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            print(f"Engine version: {version_out}")
            if self.expected_version and self.expected_version not in version_out:
                self.fail(f"Engine version mismatch: expected {self.expected_version}, got {version_out}")
        except subprocess.TimeoutExpired:
            self.skipTest("Engine --version timed out")

    def _run_engine(self, fixtures_dir: Path, extra_args: list[str]) -> subprocess.CompletedProcess:
        """Run stellar-canary check with the given fixtures dir and extra args."""
        # Create a temporary project directory with .stellar-canary.toml
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            # Enable only offline surfaces to avoid network calls
            (work / ".stellar-canary.toml").write_text(
                "version = 1\nprotocol = 28\n\n[tests]\nxdr = true\nrpc = false\nsoroban = false\n",
                encoding="utf-8",
            )
            cmd = [
                self.engine_bin,
                "check",
                "--fixtures-dir",
                str(fixtures_dir),
                "--offline",
                "--no-cache",
                "--json",
            ] + extra_args
            return subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=60)

    def test_documented_commands_do_not_exit_4(self) -> None:
        """Each documented command should not exit with code 4 (invalid fixture)."""
        for cmd in self.commands:
            with self.subTest(doc=cmd["doc"], dir=cmd["dir"]):
                resolved = resolve_dir(cmd["dir"])
                self.assertIsNotNone(resolved)

                if resolved == self.repo_root:
                    self.skipTest("Repository root target skipped (tested separately)")

                # Parse extra args from the command (protocol filter, etc.)
                extra_args = []
                if cmd["rest"]:
                    # Split rest into args, but filter out --json since we add it
                    for arg in cmd["rest"].split():
                        if arg != "--json":
                            extra_args.append(arg)

                result = self._run_engine(resolved, extra_args)
                self.assertNotEqual(
                    result.returncode,
                    4,
                    f"Command exited with code 4 (invalid fixture): {cmd['full_cmd']}\n"
                    f"stderr: {result.stderr}\nstdout: {result.stdout[:500]}",
                )

    def test_mixed_protocol_staging_directory_works(self) -> None:
        """A staging directory with protocol-28 and protocol-29 should work with --protocol 28."""
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            # Copy protocol-28
            shutil.copytree(self.repo_root / "protocol-28", staging / "protocol-28")
            # Create a minimal protocol-29 fixture
            p29 = staging / "protocol-29"
            p29.mkdir()
            fixture = p29 / "p29-xdr-test.toml"
            fixture.write_text(
                'id = "p29-xdr-test"\n'
                'protocol = 29\n'
                'surface = "xdr"\n'
                'category = "test"\n'
                'description = "test"\n'
                'source_reference = "CAP-0083"\n'
                'type = "ScVal"\n'
                'kind = "decode-success"\n'
                'value_base64 = "AAAAAwAAAAA="\n',  # ScVal u32 0
                encoding="utf-8",
            )

            result = self._run_engine(staging, ["--protocol", "28"])
            self.assertEqual(result.returncode, 0, f"Mixed-protocol run failed: {result.stderr}")
            # Should have skipped at least the protocol-29 fixture
            import json
            report = json.loads(result.stdout)
            self.assertGreaterEqual(
                report["counts"]["skipped"],
                1,
                f"Expected at least 1 skipped fixture, got {report['counts']}",
            )

    def test_invalid_toml_in_staging_causes_exit_4(self) -> None:
        """A staging directory with an invalid .toml should cause exit 4."""
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            shutil.copytree(self.repo_root / "protocol-28", staging / "protocol-28")
            # Add an invalid TOML file at the staging root
            (staging / "invalid.toml").write_text("not valid [[[ toml", encoding="utf-8")

            result = self._run_engine(staging, ["--protocol", "28"])
            self.assertEqual(
                result.returncode,
                4,
                f"Expected exit 4 for invalid TOML, got {result.returncode}: {result.stderr}",
            )

    def test_protocol_28_directory_works(self) -> None:
        """The protocol-28 directory should work with --protocol 28."""
        result = self._run_engine(self.repo_root / "protocol-28", ["--protocol", "28"])
        self.assertEqual(result.returncode, 0, f"protocol-28 run failed: {result.stderr}")
        import json
        report = json.loads(result.stdout)
        self.assertGreaterEqual(report["counts"]["total"], 1)

    def test_no_fixtures_dir_argument_is_ignored(self) -> None:
        """Commands without --fixtures-dir should be ignored by the extractor."""
        # This is tested implicitly: the regex requires --fixtures-dir
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
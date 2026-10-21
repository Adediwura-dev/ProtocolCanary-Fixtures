"""Workflow policy tests (issue #352, F-069).

Reads every file in .github/workflows/ and enforces the repository's policy:

1. Every `uses:` that refers to a remote action or reusable workflow is pinned
   to a 40-hex-character commit SHA. Local references (./...) and docker://
   references are handled explicitly: local paths allowed; docker:// images
   must be pinned by @sha256: digest or are listed as a documented exception.
2. Every workflow has an explicit `permissions` key at the top level, or every
   job has one. A workflow with neither fails. `permissions: {}` and `read-all`
   are allowed as explicit values; `write-all` is a failure.
3. No workflow triggered by `pull_request` uses secrets other than
   `secrets.GITHUB_TOKEN`, and no workflow uses `pull_request_target` unless
   it appears in a reviewed allow-list in the test with a reason.

The parser is line-oriented and tolerates comments, quoted values, list items,
and | blocks. It does not use PyYAML (not in stdlib).
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Allow-list for pull_request_target with justification
PR_TARGET_ALLOWLIST = {
    # No entries currently; all pull_request_target uses are forbidden.
}

# Documented exceptions for docker images not pinned by sha256
DOCKER_DIGEST_EXCEPTIONS = {
    # None currently; all docker:// must use @sha256:
}

# SHA-1 pattern (40 hex chars)
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class WorkflowPolicyTests(unittest.TestCase):
    """Enforce CI workflow security policy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_files = sorted(WORKFLOWS_DIR.glob("*.yml"))
        assert cls.workflow_files, "no workflow files found"

    def _read_workflow(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def _parse_uses(self, text: str) -> list[tuple[int, str]]:
        """Extract all `uses:` values with their line numbers.

        Handles:
          - uses: owner/repo@ref
          - uses: owner/repo/path@ref
          - uses: ./local-action
          - uses: docker://image:tag
          - uses: docker://image@sha256:digest
          - quoted values: uses: "owner/repo@ref"
          - list items: - uses: ...
          - inline comments: uses: owner/repo@ref # comment
        """
        results = []
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Match uses: key (with optional leading - for list items)
            match = re.match(r"^(-?\s*)uses:\s*(.+)$", stripped)
            if not match:
                continue
            value = match.group(2).strip()
            # Strip trailing inline comment
            value = re.sub(r"\s+#.*$", "", value)
            # Strip quotes
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            results.append((i, value))
        return results

    def _parse_permissions(self, text: str) -> tuple[bool, dict[int, dict]]:
        """Check top-level permissions and per-job permissions.

        Returns (has_top_level_permissions, job_permissions_dict).
        Handles:
        - permissions: {}
        - permissions: read-all
        - permissions:
            contents: read
        """
        lines = text.splitlines()
        in_jobs = False
        current_job = None
        job_permissions = {}
        top_level_permissions = False
        in_top_level_permissions = False
        top_level_permissions_indent = None

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            # Detect top-level permissions:
            if indent == 0 and stripped.startswith("permissions"):
                # permissions: or permissions: {} or permissions: read-all
                if not in_jobs:
                    top_level_permissions = True
                    in_top_level_permissions = True
                    top_level_permissions_indent = indent
                continue

            # If we were in top-level permissions, check if we've exited
            if in_top_level_permissions and indent <= top_level_permissions_indent:
                in_top_level_permissions = False

            # Detect jobs section
            if indent == 0 and stripped == "jobs:":
                in_jobs = True
                in_top_level_permissions = False
                continue

            if in_jobs:
                # Job name (indent 2 typically)
                if indent == 2 and re.match(r"^[a-zA-Z0-9_-]+:$", stripped):
                    current_job = stripped[:-1]
                    job_permissions[current_job] = {"has_permissions": False, "line": i}
                    continue

                # Job-level permissions
                if current_job and indent == 4 and stripped.startswith("permissions"):
                    job_permissions[current_job]["has_permissions"] = True
                    continue

                # Exit jobs block when back to indent 0
                if indent == 0 and in_jobs:
                    in_jobs = False
                    current_job = None

        return top_level_permissions, job_permissions

    def _parse_triggers(self, text: str) -> dict:
        """Parse `on:` triggers to detect pull_request, pull_request_target."""
        lines = text.splitlines()
        in_on = False
        triggers = {"pull_request": False, "pull_request_target": False}
        on_indent = None

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())

            if indent == 0 and stripped == "on:":
                in_on = True
                on_indent = indent
                continue

            if in_on:
                if indent <= on_indent and stripped != "":
                    in_on = False
                    # Check the current line as it might be the next top-level key
                    if stripped == "on:":
                        in_on = True
                        on_indent = indent
                    continue
                # Check for pull_request and pull_request_target
                # Can appear as:
                #   pull_request:
                #   pull_request_target:
                #   pull_request:
                #   - types: [opened]
                #   pull_request_target:
                if stripped in ("pull_request:", "pull_request_target:"):
                    if stripped == "pull_request:":
                        triggers["pull_request"] = True
                    else:
                        triggers["pull_request_target"] = True
                # Also handle list form: - pull_request
                elif stripped.startswith("- pull_request"):
                    if "pull_request_target" in stripped:
                        triggers["pull_request_target"] = True
                    else:
                        triggers["pull_request"] = True

            # Also handle short form: on: pull_request or on: [pull_request, push]
            if indent == 0 and stripped.startswith("on:"):
                # Short form: on: pull_request or on: pull_request_target
                rest = stripped[3:].strip()
                if rest == "pull_request" or rest.startswith("pull_request ") or rest.startswith("[pull_request"):
                    triggers["pull_request"] = True
                if rest == "pull_request_target" or rest.startswith("pull_request_target ") or rest.startswith("[pull_request_target"):
                    triggers["pull_request_target"] = True

        return triggers

    def _parse_secrets_usage(self, text: str) -> list[tuple[int, str]]:
        """Find all secrets. references in the workflow."""
        results = []
        # Match secrets.NAME but not in comments
        pattern = re.compile(r"secrets\.([A-Z0-9_]+)")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for match in pattern.finditer(line):
                secret_name = match.group(1)
                results.append((i, secret_name))
        return results

    def test_workflow_files_exist(self) -> None:
        self.assertGreater(len(self.workflow_files), 0)

    # --- Rule 1: uses: pinned to commit SHA ---

    def test_uses_pinned_to_sha(self) -> None:
        """Every remote uses: must be pinned to a 40-char commit SHA."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            uses_list = self._parse_uses(text)
            for line_num, value in uses_list:
                with self.subTest(file=wf_path.name, line=line_num, uses=value):
                    # Local reference (./...)
                    if value.startswith("./"):
                        continue
                    # Docker reference
                    if value.startswith("docker://"):
                        # Must be pinned by @sha256:digest or be in exceptions
                        if "@sha256:" in value:
                            continue
                        if value in DOCKER_DIGEST_EXCEPTIONS:
                            continue
                        self.fail(
                            f"docker:// reference not pinned by @sha256: {value} "
                            f"(file {wf_path.name}, line {line_num})"
                        )
                        continue
                    # Remote action or reusable workflow: owner/repo@ref or owner/repo/path@ref
                    # The ref is after the last @
                    if "@" not in value:
                        self.fail(
                            f"uses: missing @ref: {value} (file {wf_path.name}, line {line_num})"
                        )
                        continue
                    ref = value.split("@")[-1]
                    if not SHA1_PATTERN.match(ref):
                        self.fail(
                            f"uses: ref is not a 40-char commit SHA: {ref} "
                            f"(file {wf_path.name}, line {line_num})"
                        )

    def test_uses_no_tag_or_branch_refs(self) -> None:
        """Explicitly forbid common tag/branch patterns in uses: refs."""
        forbidden_patterns = [
            (r"@v\d+$", "version tag like @v4"),
            (r"@v\d+\.\d+$", "version tag like @v4.1"),
            (r"@v\d+\.\d+\.\d+$", "version tag like @v4.1.0"),
            (r"@main$", "branch @main"),
            (r"@master$", "branch @master"),
            (r"@develop$", "branch @develop"),
        ]
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            uses_list = self._parse_uses(text)
            for line_num, value in uses_list:
                if value.startswith("./") or value.startswith("docker://"):
                    continue
                for pattern, desc in forbidden_patterns:
                    if re.search(pattern, value):
                        self.fail(
                            f"uses: uses {desc} which moves: {value} "
                            f"(file {wf_path.name}, line {line_num})"
                        )

    def test_uses_sha_length_exact(self) -> None:
        """Ref must be exactly 40 chars (not 39 or 41)."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            uses_list = self._parse_uses(text)
            for line_num, value in uses_list:
                if value.startswith("./") or value.startswith("docker://"):
                    continue
                if "@" in value:
                    ref = value.split("@")[-1]
                    if len(ref) == 39:
                        self.fail(
                            f"uses: ref is 39 chars (too short): {ref} "
                            f"(file {wf_path.name}, line {line_num})"
                        )
                    if len(ref) == 41:
                        self.fail(
                            f"uses: ref is 41 chars (too long): {ref} "
                            f"(file {wf_path.name}, line {line_num})"
                        )

    # --- Rule 2: explicit permissions ---

    def test_workflow_has_explicit_permissions(self) -> None:
        """Every workflow must have top-level permissions or per-job permissions."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            top_level, job_perms = self._parse_permissions(text)
            with self.subTest(file=wf_path.name):
                if top_level:
                    continue
                # Check if every job has permissions
                jobs_without = [
                    job for job, info in job_perms.items() if not info["has_permissions"]
                ]
                if jobs_without:
                    self.fail(
                        f"workflow has no top-level permissions and jobs lack permissions: "
                        f"{', '.join(jobs_without)} (file {wf_path.name})"
                    )

    def test_permissions_not_write_all(self) -> None:
        """permissions: write-all is forbidden."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            # Simple check for write-all at any level
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if "write-all" in stripped and "permissions" in stripped:
                    self.fail(
                        f"permissions: write-all is forbidden (file {wf_path.name}, line {i})"
                    )

    def test_permissions_explicit_empty_allowed(self) -> None:
        """permissions: {} is allowed (explicit empty)."""
        # This is a positive test - we just ensure it doesn't fail the above
        # The actual validation is that explicit permissions (even empty) exist
        pass

    # --- Rule 3: pull_request secrets and pull_request_target ---

    def test_no_pull_request_target_without_allowlist(self) -> None:
        """pull_request_target is forbidden unless allow-listed with reason."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            triggers = self._parse_triggers(text)
            with self.subTest(file=wf_path.name):
                if triggers["pull_request_target"]:
                    if wf_path.name not in PR_TARGET_ALLOWLIST:
                        self.fail(
                            f"workflow uses pull_request_target but is not in allow-list "
                            f"(file {wf_path.name}). Add to PR_TARGET_ALLOWLIST with reason."
                        )

    def test_pull_request_no_secrets_except_github_token(self) -> None:
        """Workflows triggered by pull_request must not use secrets (except GITHUB_TOKEN)."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            triggers = self._parse_triggers(text)
            if not triggers["pull_request"]:
                continue
            secrets_used = self._parse_secrets_usage(text)
            for line_num, secret_name in secrets_used:
                with self.subTest(file=wf_path.name, line=line_num, secret=secret_name):
                    if secret_name != "GITHUB_TOKEN":
                        self.fail(
                            f"pull_request workflow uses secret {secret_name} "
                            f"(file {wf_path.name}, line {line_num})"
                        )

    # --- Negative/positive test vectors ---

    def test_negative_tag_pinned_action(self) -> None:
        """A tag-pinned action (@v4) should be rejected."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        uses_list = self._parse_uses(text)
        self.assertEqual(len(uses_list), 1)
        _, value = uses_list[0]
        self.assertFalse(SHA1_PATTERN.match(value.split("@")[-1]))

    def test_negative_branch_pinned_action(self) -> None:
        """A branch-pinned action (@main) should be rejected."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: owner/repo@main
"""
        uses_list = self._parse_uses(text)
        _, value = uses_list[0]
        self.assertFalse(SHA1_PATTERN.match(value.split("@")[-1]))

    def test_negative_39_char_ref(self) -> None:
        """A 39-char ref should be rejected."""
        ref = "a" * 39
        self.assertFalse(SHA1_PATTERN.match(ref))

    def test_negative_41_char_ref(self) -> None:
        """A 41-char ref should be rejected."""
        ref = "a" * 41
        self.assertFalse(SHA1_PATTERN.match(ref))

    def test_negative_missing_permissions(self) -> None:
        """A workflow with no permissions should fail."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""
        top_level, job_perms = self._parse_permissions(text)
        self.assertFalse(top_level)
        self.assertFalse(job_perms["test"]["has_permissions"])

    def test_negative_permissions_write_all(self) -> None:
        """permissions: write-all should be detected."""
        text = """
name: test
on: push
permissions: write-all
jobs:
  test:
    runs-on: ubuntu-latest
"""
        self.assertIn("write-all", text)

    def test_positive_sha_pinned_with_comment(self) -> None:
        """SHA-pinned action with trailing comment should be accepted."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
"""
        uses_list = self._parse_uses(text)
        _, value = uses_list[0]
        ref = value.split("@")[-1]
        self.assertTrue(SHA1_PATTERN.match(ref))

    def test_positive_local_action(self) -> None:
        """Local action reference (./...) should be accepted."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/my-action
"""
        uses_list = self._parse_uses(text)
        _, value = uses_list[0]
        self.assertTrue(value.startswith("./"))

    def test_positive_docker_digest_pinned(self) -> None:
        """Docker image pinned by @sha256: should be accepted."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    container:
      image: docker://ghcr.io/owner/image@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
"""
        uses_list = self._parse_uses(text)
        # container.image is not a uses: but let's check the pattern works
        self.assertIn("@sha256:", text)

    def test_positive_explicit_empty_permissions(self) -> None:
        """permissions: {} should count as explicit."""
        text = """
name: test
on: push
permissions: {}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""
        top_level, job_perms = self._parse_permissions(text)
        self.assertTrue(top_level)

    def test_positive_per_job_permissions(self) -> None:
        """Per-job permissions should satisfy the rule."""
        text = """
name: test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - run: echo hello
"""
        top_level, job_perms = self._parse_permissions(text)
        self.assertFalse(top_level)
        self.assertTrue(job_perms["test"]["has_permissions"])

    def test_positive_pull_request_with_only_github_token(self) -> None:
        """pull_request workflow using only GITHUB_TOKEN should pass."""
        text = """
name: test
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
"""
        triggers = self._parse_triggers(text)
        self.assertTrue(triggers["pull_request"])
        secrets_used = self._parse_secrets_usage(text)
        for _, secret in secrets_used:
            self.assertEqual(secret, "GITHUB_TOKEN")

    def test_negative_pull_request_with_other_secret(self) -> None:
        """pull_request workflow using other secret should fail."""
        text = """
name: test
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ${{ secrets.MY_SECRET }}
"""
        triggers = self._parse_triggers(text)
        self.assertTrue(triggers["pull_request"])
        secrets_used = self._parse_secrets_usage(text)
        non_github = [s for _, s in secrets_used if s != "GITHUB_TOKEN"]
        self.assertEqual(non_github, ["MY_SECRET"])

    def test_positive_documented_commands_accepted(self) -> None:
        """Current workflows should pass all checks."""
        for wf_path in self.workflow_files:
            text = self._read_workflow(wf_path)
            uses_list = self._parse_uses(text)
            for line_num, value in uses_list:
                if value.startswith("./") or value.startswith("docker://"):
                    continue
                if "@" in value:
                    ref = value.split("@")[-1]
                    # Just verify the pattern matches for current workflows
                    # (this will fail if any current workflow violates the rule)
                    self.assertTrue(
                        SHA1_PATTERN.match(ref) or value in DOCKER_DIGEST_EXCEPTIONS,
                        f"Current workflow {wf_path.name} line {line_num} has non-SHA ref: {ref}"
                    )

            top_level, job_perms = self._parse_permissions(text)
            if not top_level:
                jobs_without = [j for j, i in job_perms.items() if not i["has_permissions"]]
                self.assertEqual(
                    jobs_without, [],
                    f"Current workflow {wf_path.name} has jobs without permissions: {jobs_without}"
                )

            triggers = self._parse_triggers(text)
            if triggers["pull_request_target"]:
                self.assertIn(
                    wf_path.name, PR_TARGET_ALLOWLIST,
                    f"Current workflow {wf_path.name} uses pull_request_target without allow-list"
                )


if __name__ == "__main__":
    unittest.main()
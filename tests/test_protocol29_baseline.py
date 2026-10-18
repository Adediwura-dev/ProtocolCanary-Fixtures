"""Tests for the Protocol 29 baseline directory and registry (F-033, #290).

The ``protocol-29/`` pack ships as an empty scaffold: a ``registry-v1.json``
with ``protocol = 29`` and ``fixtures: []``, plus ``rpc/``, ``xdr/`` and
``soroban/`` subdirectories and nothing else. An empty baseline is a
directory scaffold only and must never be read as established compatibility
coverage; ``protocol-28/`` stays untouched by this change.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = REPO_ROOT / "protocol-29"
REGISTRY_PATH = PACK_DIR / "registry-v1.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry-v1.schema.json"

VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"
_spec = importlib.util.spec_from_file_location("validate_for_protocol29", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate_for_protocol29"] = validate
_spec.loader.exec_module(validate)

LITE_PATH = REPO_ROOT / "tools" / "validate" / "jsonschema_lite.py"
_lite_spec = importlib.util.spec_from_file_location("lite_for_protocol29", LITE_PATH)
lite = importlib.util.module_from_spec(_lite_spec)
assert _lite_spec.loader is not None
sys.modules["lite_for_protocol29"] = lite
_lite_spec.loader.exec_module(lite)

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
EMPTY_FLOOR = "fewer than 1"  # jsonschema_lite's wording for the fixtures minItems rule


def check_baseline(record: dict) -> list[str]:
    """Problems in the protocol-29 baseline manifest, empty when conforming.

    Everything the schema checks comes from ``jsonschema_lite``; on top of
    it, the pack binding asserts what the schema cannot: the directory name
    and the manifest's ``protocol`` value agree (``protocol-29/`` declares
    ``protocol = 29``). The schema's floor on a non-empty ``fixtures`` list
    is filtered out here and pinned separately in
    ``test_registry_conforms_to_the_registry_schema`` — an empty fixtures
    array is exactly what this baseline is.
    """
    problems = [p for p in lite.validate(record, SCHEMA) if EMPTY_FLOOR not in p]
    version = record.get("registryVersion")
    if type(version) is not int or version != 1:
        problems.append(f"registryVersion must be the integer 1, got {version!r}")
    protocol = record.get("protocol")
    if type(protocol) is not int or protocol != 29:
        problems.append(
            f"a protocol-29/ manifest must declare protocol = 29 as an integer, got {protocol!r}"
        )
    if not record.get("fixtures") and list(PACK_DIR.rglob("*.toml")):
        problems.append("registry lists no fixtures but the pack contains *.toml fixture files")
    return problems


class Protocol29BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_pack_subdirectories_exist(self) -> None:
        for name in ("rpc", "xdr", "soroban"):
            with self.subTest(directory=name):
                self.assertTrue((PACK_DIR / name).is_dir())

    def test_registry_is_an_empty_protocol_29_scaffold(self) -> None:
        integer_protocol = self.registry["protocol"]
        self.assertIs(type(integer_protocol), int, "protocol must be an integer, not a bool")
        self.assertEqual(integer_protocol, 29)
        self.assertEqual(self.registry["registryVersion"], 1)
        self.assertEqual(self.registry["fixtures"], [])

    def test_registry_conforms_to_the_registry_schema(self) -> None:
        problems = lite.validate(self.registry, SCHEMA)
        if self.registry["fixtures"]:
            self.assertEqual(problems, [])
        else:
            # Assumption stated in the PR: schemas/registry-v1.schema.json
            # sets `minItems: 1` on fixtures while the issue requires an
            # empty baseline; CF-02 section 3 requires the four keys but
            # does not forbid an empty list. An empty manifest must fail on
            # that floor — and on nothing else.
            self.assertEqual(len(problems), 1, problems)
            self.assertIn(EMPTY_FLOOR, problems[0])

    def test_registry_uses_the_canonical_encoding(self) -> None:
        # CF-02 section 3: UTF-8, LF, two-space indent, one trailing newline.
        expected = json.dumps(self.registry, indent=2, ensure_ascii=False) + "\n"
        self.assertEqual(REGISTRY_PATH.read_text(encoding="utf-8"), expected)

    def test_empty_pack_digest_is_byte_reproducible(self) -> None:
        # CF-02 section 5: with zero pack members the digest input is only
        # the ASCII header line, so the value is fully determined by the
        # contract and not by when or where the file was written.
        expected = "sha256:" + hashlib.sha256(b"canary-pack-v1\n").hexdigest()
        self.assertEqual(self.registry["packDigest"], expected)

    def test_empty_baseline_is_not_reported_as_coverage(self) -> None:
        self.assertEqual(self.registry["fixtures"], [])
        self.assertEqual(sorted(PACK_DIR.rglob("*.toml")), [])

    def test_baseline_manifest_passes_the_pack_checks(self) -> None:
        self.assertEqual(check_baseline(self.registry), [])

    def test_other_protocol_numbers_fail_the_pack_checks(self) -> None:
        for wrong in (28, 30, "29", True):
            with self.subTest(protocol=wrong):
                mutated = dict(self.registry, protocol=wrong)
                problems = check_baseline(mutated)
                self.assertTrue(problems, f"protocol {wrong!r} was accepted")

    def test_validator_accepts_the_protocol_29_directory(self) -> None:
        report = validate.validate_directory(PACK_DIR)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])

    def test_default_scan_discovers_protocol_29(self) -> None:
        # The CLI's default roots glob protocol-*/; protocol-29/ must be
        # among them and contribute no diagnostics (F-033 acceptance).
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = validate.main(["--format", "json"])
        document = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertTrue(document["valid"])
        self.assertEqual(document["warnings"], [])
        roots = [Path(root) for root in document["summary"]["roots"]]
        self.assertIn(PACK_DIR, roots, roots)


if __name__ == "__main__":
    unittest.main()
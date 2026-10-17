"""Shared fixture conformance vectors (issue #283).

Run with: python3 -m unittest tests/test_conformance_vectors.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VECTORS_DIR = REPO_ROOT / "tests" / "vectors" / "conformance"
MANIFEST_PATH = VECTORS_DIR / "vectors.json"
VALIDATOR_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"

_spec = importlib.util.spec_from_file_location("conformance_validate", VALIDATOR_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["conformance_validate"] = validate
_spec.loader.exec_module(validate)

# Loaded only for the isolation checks below: the corpus must stay invisible to
# the badge and to the default validator roots, which both discover `protocol-*`.
_badge_spec = importlib.util.spec_from_file_location(
    "conformance_badge", REPO_ROOT / "tools" / "badge" / "badge.py"
)
badge = importlib.util.module_from_spec(_badge_spec)
assert _badge_spec.loader is not None
sys.modules["conformance_badge"] = badge
_badge_spec.loader.exec_module(badge)

SURFACES = {"xdr", "rpc", "soroban"}
ERROR_CODE_PREFIXES = {
    "invalid-toml": "invalid TOML",
    "missing-required-field": "missing required field",
    "invalid-base64": "field 'value_base64' is not valid base64",
    "missing-conditional-field": "missing required field 'expected_base64'",
    "unsupported-rpc-method": "field 'method' must be one of",
    "missing-rpc-assertions": "'assert' must be a non-empty array of tables",
    "missing-rpc-assert-field": "assert[0] missing required non-empty 'field'",
    "wrong-field-type": "field 'sequence_number' must be of type int",
    "unknown-soroban-expect-key": "unexpected key(s)",
    "wrong-soroban-message-type": "expect.message_contains must be a string",
}


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


class ConformanceVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest()
        cls.vectors = cls.manifest["accepted"] + cls.manifest["rejected"]

    def test_corpus_has_minimum_coverage(self) -> None:
        self.assertEqual(self.manifest["version"], 1)
        self.assertGreaterEqual(len(self.manifest["accepted"]), 10)
        self.assertGreaterEqual(len(self.manifest["rejected"]), 10)
        for group in (self.manifest["accepted"], self.manifest["rejected"]):
            self.assertEqual({vector["surface"] for vector in group}, SURFACES)

    def test_manifest_paths_and_ids_are_unique_and_present(self) -> None:
        ids = [vector["id"] for vector in self.vectors]
        paths = [vector["path"] for vector in self.vectors]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(paths), len(set(paths)))
        for vector in self.vectors:
            with self.subTest(vector=vector["id"]):
                relative = Path(vector["path"])
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                self.assertTrue(vector["id"].strip())
                self.assertIn(vector["surface"], SURFACES)
                self.assertTrue((VECTORS_DIR / relative).is_file())

    def test_payload_size_and_boundary_vectors_are_explicit(self) -> None:
        oversized = VECTORS_DIR / "valid" / "xdr-oversized-payload.toml"
        oversized_data = tomllib.loads(oversized.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(oversized_data["value_base64"]), 1024)

        boundary = VECTORS_DIR / "valid" / "soroban-empty-args-boundary.toml"
        boundary_data = tomllib.loads(boundary.read_text(encoding="utf-8"))
        self.assertEqual(boundary_data["protocol"], 1)
        self.assertEqual(boundary_data["sequence_number"], 0)
        self.assertEqual(boundary_data["args"], [])

    def test_each_vector_has_its_documented_validator_outcome(self) -> None:
        for vector in self.vectors:
            with self.subTest(vector=vector["id"]):
                fixture = VECTORS_DIR / vector["path"]
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    shutil.copyfile(fixture, root / fixture.name)
                    report = validate.validate_directory(root)

                if vector in self.manifest["accepted"]:
                    self.assertTrue(report.ok, report.errors)
                    self.assertEqual(report.errors, [])
                    continue

                self.assertFalse(report.ok, f"{vector['id']} unexpectedly passed")
                error = vector.get("error_code")
                diagnostic = vector.get("diagnostic_contains")
                self.assertTrue(error, f"{vector['id']} has no documented error code")
                self.assertIn(error, ERROR_CODE_PREFIXES)
                self.assertTrue(
                    diagnostic,
                    f"{vector['id']} has no expected diagnostic fragment",
                )
                self.assertTrue(
                    diagnostic.startswith(ERROR_CODE_PREFIXES[error]),
                    f"{vector['id']} diagnostic does not match error code {error!r}",
                )
                self.assertTrue(
                    any(diagnostic in message for message in report.errors),
                    f"{vector['id']} expected {error!r} diagnostic containing "
                    f"{diagnostic!r}; got {report.errors}",
                )


class CorpusIsolationTests(unittest.TestCase):
    """The corpus is parser test data, not fixtures in a protocol pack."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest()
        cls.vectors = cls.manifest["accepted"] + cls.manifest["rejected"]
        cls.packs = sorted(p for p in REPO_ROOT.glob("protocol-*") if p.is_dir())

    def test_every_vector_file_is_documented(self) -> None:
        # Both directions: an orphan file on disk would be a vector nothing
        # checks, and a manifest entry with no file is already caught above.
        documented = {vector["path"] for vector in self.vectors}
        on_disk = {
            path.relative_to(VECTORS_DIR).as_posix()
            for surface_dir in ("valid", "invalid")
            for path in (VECTORS_DIR / surface_dir).glob("*.toml")
        }
        self.assertEqual(on_disk, documented)

    def test_the_corpus_is_outside_every_scan_root(self) -> None:
        # `tools/validate/validate.py` and `tools/badge/badge.py` both discover
        # `protocol-*`. Were that widened, these files -- one of which is
        # deliberately unparseable -- would be treated as consumable fixtures.
        self.assertFalse(str(VECTORS_DIR.relative_to(REPO_ROOT)).startswith("protocol-"))
        self.assertEqual(badge.pack_roots(REPO_ROOT), self.packs)
        for pack in self.packs:
            self.assertNotIn(pack, VECTORS_DIR.parents)

    def test_the_fixture_count_badge_is_unaffected(self) -> None:
        self.assertEqual(
            badge.count_fixtures(REPO_ROOT),
            sum(len(validate.find_fixture_files(pack)) for pack in self.packs),
        )

    def test_vector_ids_do_not_collide_with_shipped_fixture_ids(self) -> None:
        # Ids must be unique across everything a loader could be pointed at, so
        # a corpus id that shadowed a real fixture id would be a trap. The
        # unparseable syntax vector contributes no id, by construction.
        vector_ids = set()
        for vector in self.vectors:
            path = VECTORS_DIR / vector["path"]
            try:
                vector_ids.add(tomllib.loads(path.read_text(encoding="utf-8"))["id"])
            except (tomllib.TOMLDecodeError, KeyError):
                self.assertEqual(vector["id"], "invalid-xdr-toml-syntax")
        shipped = {
            fixture.data["id"]
            for pack in self.packs
            for path in validate.find_fixture_files(pack)
            for fixture in [validate.load_fixture(path, validate.Report())]
            if fixture is not None and "id" in fixture.data
        }
        self.assertEqual(len(vector_ids), len(self.vectors) - 1)
        self.assertEqual(vector_ids & shipped, set())


if __name__ == "__main__":
    unittest.main()
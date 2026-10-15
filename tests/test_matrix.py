"""Tests for upstream-matrix.json and schemas/upstream-matrix.json (F-026).

The matrix maps each documented protocol to the upstream components
(`stellar-core`, `soroban-rpc`, `rs-soroban-env`) and the semver range of
their releases. It is validated against its JSON Schema for real, using the
repository's stdlib validator, so a malformed range or a missing protocol
section fails with a message that names the offending path.

Run with: python3 -m unittest tests/test_matrix.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "upstream-matrix.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "upstream-matrix.json"

_spec = importlib.util.spec_from_file_location(
    "jsonschema_lite_matrix", REPO_ROOT / "tools" / "validate" / "jsonschema_lite.py"
)
lite = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["jsonschema_lite_matrix"] = lite
_spec.loader.exec_module(lite)

COMPONENTS = ("stellar-core", "soroban-rpc", "rs-soroban-env")
WINDOW = re.compile(r"^>=([0-9]+)\.0\.0 <([0-9]+)\.0\.0$")


def load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class MatrixCase(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_schema()
        self.matrix = load_matrix()

    def errors(self, matrix: dict) -> str:
        return "\n".join(lite.validate(matrix, self.schema))

    def mutated(self) -> dict:
        return copy.deepcopy(self.matrix)


class ShippedMatrixTests(MatrixCase):
    def test_the_matrix_conforms_to_its_schema(self) -> None:
        self.assertEqual(lite.validate(self.matrix, self.schema), [])

    def test_the_schema_uses_only_keywords_the_validator_implements(self) -> None:
        # Raises SchemaError for a keyword jsonschema_lite would have skipped.
        lite.validate(self.matrix, self.schema)

    def test_the_schema_declares_itself_a_json_schema(self) -> None:
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertTrue(self.schema["$id"].endswith("/schemas/upstream-matrix.json"))

    def test_the_matrix_points_at_its_schema(self) -> None:
        target = (MATRIX_PATH.parent / self.matrix["$schema"]).resolve()
        self.assertEqual(target, SCHEMA_PATH.resolve())

    def test_it_covers_protocols_28_and_29(self) -> None:
        self.assertEqual(set(self.matrix["protocols"]), {"28", "29"})

    def test_every_protocol_has_every_component(self) -> None:
        for protocol, section in self.matrix["protocols"].items():
            self.assertEqual(set(section), set(COMPONENTS), protocol)

    def test_each_range_is_the_release_line_of_its_protocol(self) -> None:
        # Upstream major versions track the protocol version: Stellar Core,
        # Stellar RPC and the Soroban host environment all ship 28.x for
        # Protocol 28 and 29.x for Protocol 29.
        for protocol, section in self.matrix["protocols"].items():
            for name, component in section.items():
                match = WINDOW.match(component["range"])
                self.assertIsNotNone(match, f"{protocol}/{name}: {component['range']!r}")
                self.assertEqual(int(match.group(1)), int(protocol), f"{protocol}/{name}")
                self.assertEqual(int(match.group(2)), int(protocol) + 1, f"{protocol}/{name}")

    def test_repositories_are_the_upstream_projects(self) -> None:
        expected = {
            "stellar-core": "stellar/stellar-core",
            "soroban-rpc": "stellar/stellar-rpc",  # soroban-rpc was renamed
            "rs-soroban-env": "stellar/rs-soroban-env",
        }
        for section in self.matrix["protocols"].values():
            self.assertEqual(
                {name: c["repository"] for name, c in section.items()}, expected
            )

    def test_every_pack_with_fixtures_has_a_matrix_section(self) -> None:
        for pack in sorted(REPO_ROOT.glob("protocol-*")):
            if not pack.is_dir() or not any(pack.rglob("*.toml")):
                continue  # a pack with no fixtures (protocol-27) has no baseline
            number = pack.name.removeprefix("protocol-")
            self.assertIn(number, self.matrix["protocols"], pack.name)

    def test_the_file_is_in_the_canonical_serialization(self) -> None:
        # Two-space indent, LF, one trailing newline, keys in source order:
        # the same shape the registry uses, so diffs stay minimal and stable.
        text = MATRIX_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(text, json.dumps(self.matrix, indent=2, ensure_ascii=False) + "\n")

    def test_protocol_sections_are_in_ascending_order(self) -> None:
        keys = [int(k) for k in self.matrix["protocols"]]
        self.assertEqual(keys, sorted(keys))


class InvalidSemverTests(MatrixCase):
    def test_malformed_ranges_are_rejected_with_a_located_message(self) -> None:
        bad_ranges = [
            "v28.0.0",
            "28",
            "28.0",
            ">=28.0",
            "^28.0.0",
            "~28.0.0",
            "28.x",
            "28.0.x",
            ">= 28.0.0",
            "28.0.0 - 29.0.0",
            ">=28.0.0  <29.0.0",
            ">=28.0.0 <29.0.0 ",
            " >=28.0.0",
            ">=28.0.0,<29.0.0",
            ">=028.0.0",
            ">=28.00.0",
            ">=28.0.0-rc.1",
            "latest",
            "",
        ]
        for value in bad_ranges:
            matrix = self.mutated()
            matrix["protocols"]["28"]["stellar-core"]["range"] = value
            text = self.errors(matrix)
            self.assertIn("$.protocols.28.stellar-core.range", text, repr(value))

    def test_a_range_that_is_not_a_string_is_rejected(self) -> None:
        for value in (28, None, ["28.0.0"], {"min": "28.0.0"}):
            matrix = self.mutated()
            matrix["protocols"]["28"]["stellar-core"]["range"] = value
            self.assertIn("expected string", self.errors(matrix), repr(value))

    def test_well_formed_ranges_are_accepted(self) -> None:
        for value in (
            ">=28.0.0 <29.0.0",
            ">28.0.0",
            "<=28.5.3",
            "28.0.0",
            "=28.0.0",
            ">=0.0.0",
            ">=28.0.0 <29.0.0 <28.9.9",
            ">=28.10.200 <29.0.0",
        ):
            matrix = self.mutated()
            matrix["protocols"]["28"]["stellar-core"]["range"] = value
            self.assertEqual(self.errors(matrix), "", repr(value))


class MissingSectionTests(MatrixCase):
    def test_a_missing_protocol_section_is_rejected(self) -> None:
        for protocol in ("28", "29"):
            matrix = self.mutated()
            del matrix["protocols"][protocol]
            self.assertIn(
                f"missing required property {protocol!r}", self.errors(matrix), protocol
            )

    def test_an_empty_protocols_object_is_rejected(self) -> None:
        matrix = self.mutated()
        matrix["protocols"] = {}
        text = self.errors(matrix)
        self.assertIn("missing required property '28'", text)
        self.assertIn("fewer than 1", text)

    def test_a_missing_component_is_rejected(self) -> None:
        for component in COMPONENTS:
            matrix = self.mutated()
            del matrix["protocols"]["29"][component]
            text = self.errors(matrix)
            self.assertIn(f"missing required property {component!r}", text, component)
            self.assertIn("$.protocols.29", text, component)

    def test_a_component_without_a_range_or_repository_is_rejected(self) -> None:
        for field in ("range", "repository"):
            matrix = self.mutated()
            del matrix["protocols"]["28"]["rs-soroban-env"][field]
            self.assertIn(f"missing required property {field!r}", self.errors(matrix))

    def test_missing_top_level_fields_are_rejected(self) -> None:
        for field in ("matrixVersion", "protocols"):
            matrix = self.mutated()
            del matrix[field]
            self.assertIn(f"missing required property {field!r}", self.errors(matrix))


class StrictnessTests(MatrixCase):
    def test_unknown_components_protocols_and_keys_are_rejected(self) -> None:
        matrix = self.mutated()
        matrix["protocols"]["28"]["horizon"] = {"repository": "stellar/go", "range": ">=1.0.0"}
        self.assertIn("unexpected property 'horizon'", self.errors(matrix))

        matrix = self.mutated()
        matrix["protocols"]["latest"] = copy.deepcopy(matrix["protocols"]["28"])
        self.assertIn("unexpected property 'latest'", self.errors(matrix))

        matrix = self.mutated()
        matrix["protocols"]["028"] = copy.deepcopy(matrix["protocols"]["28"])
        self.assertIn("unexpected property '028'", self.errors(matrix))

        matrix = self.mutated()
        matrix["protocols"]["28"]["stellar-core"]["note"] = "x"
        self.assertIn("unexpected property 'note'", self.errors(matrix))

        matrix = self.mutated()
        matrix["extra"] = 1
        self.assertIn("unexpected property 'extra'", self.errors(matrix))

    def test_an_unsupported_matrix_version_is_rejected(self) -> None:
        for value in (0, 2, "1", 1.5, True, None):
            matrix = self.mutated()
            matrix["matrixVersion"] = value
            self.assertNotEqual(self.errors(matrix), "", repr(value))

    def test_a_malformed_repository_is_rejected(self) -> None:
        for value in ("stellar-core", "stellar/", "/stellar-core", "a/b/c", "stellar core", ""):
            matrix = self.mutated()
            matrix["protocols"]["28"]["stellar-core"]["repository"] = value
            self.assertIn(
                "$.protocols.28.stellar-core.repository", self.errors(matrix), repr(value)
            )

    def test_provenance_fields_are_validated(self) -> None:
        for field, value in (
            ("source", "http://developers.stellar.org/docs"),
            ("source", "see the website"),
            ("checkedOn", "15/10/2026"),
            ("checkedOn", "2026-10-1"),
        ):
            matrix = self.mutated()
            matrix[field] = value
            self.assertIn(f"$.{field}", self.errors(matrix), (field, value))

    def test_a_section_must_be_an_object(self) -> None:
        matrix = self.mutated()
        matrix["protocols"]["28"] = [">=28.0.0 <29.0.0"]
        self.assertIn("expected object", self.errors(matrix))


if __name__ == "__main__":
    unittest.main()

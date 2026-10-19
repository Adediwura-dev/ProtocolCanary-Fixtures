"""Tests for tools/validate/jsonschema_lite.py.

The module stands in for the third-party ``jsonschema`` package, which CI does
not install, so it must (a) accept and reject documents correctly for the
keywords it supports and (b) refuse, loudly, any keyword it does not.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "jsonschema_lite_under_test", REPO_ROOT / "tools" / "validate" / "jsonschema_lite.py"
)
lite = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["jsonschema_lite_under_test"] = lite
_spec.loader.exec_module(lite)


class KeywordTests(unittest.TestCase):
    def errors(self, document, schema) -> list[str]:
        return lite.validate(document, schema)

    def test_type(self) -> None:
        self.assertEqual(self.errors("x", {"type": "string"}), [])
        self.assertEqual(self.errors(None, {"type": "null"}), [])
        self.assertEqual(self.errors(1, {"type": ["string", "integer"]}), [])
        self.assertIn("expected string, got integer", self.errors(1, {"type": "string"})[0])

    def test_booleans_are_not_integers_or_numbers(self) -> None:
        self.assertNotEqual(self.errors(True, {"type": "integer"}), [])
        self.assertNotEqual(self.errors(True, {"type": "number"}), [])
        self.assertEqual(self.errors(True, {"type": "boolean"}), [])

    def test_integer_and_number(self) -> None:
        self.assertEqual(self.errors(3, {"type": "number"}), [])
        self.assertEqual(self.errors(3.5, {"type": "number"}), [])
        self.assertNotEqual(self.errors(3.5, {"type": "integer"}), [])

    def test_const_does_not_confuse_booleans_with_integers(self) -> None:
        self.assertEqual(self.errors(1, {"const": 1}), [])
        self.assertNotEqual(self.errors(True, {"const": 1}), [])
        self.assertNotEqual(self.errors(2, {"const": 1}), [])

    def test_enum(self) -> None:
        self.assertEqual(self.errors("a", {"enum": ["a", "b"]}), [])
        self.assertIn("is not one of", self.errors("c", {"enum": ["a", "b"]})[0])

    def test_string_keywords(self) -> None:
        schema = {"type": "string", "minLength": 2, "maxLength": 3, "pattern": "^[a-z]+$"}
        self.assertEqual(self.errors("abc", schema), [])
        self.assertNotEqual(self.errors("a", schema), [])
        self.assertNotEqual(self.errors("abcd", schema), [])
        self.assertIn("does not match pattern", self.errors("A1", schema)[0])

    def test_pattern_is_a_search_not_a_full_match(self) -> None:
        self.assertEqual(self.errors("xxabcxx", {"pattern": "abc"}), [])
        self.assertNotEqual(self.errors("xxabxx", {"pattern": "^abc$"}), [])

    def test_numeric_bounds(self) -> None:
        schema = {"type": "integer", "minimum": 1, "maximum": 3}
        self.assertEqual(self.errors(2, schema), [])
        self.assertNotEqual(self.errors(0, schema), [])
        self.assertNotEqual(self.errors(4, schema), [])

    def test_array_keywords(self) -> None:
        schema = {"type": "array", "minItems": 1, "maxItems": 2, "uniqueItems": True,
                  "items": {"type": "integer"}}
        self.assertEqual(self.errors([1, 2], schema), [])
        self.assertNotEqual(self.errors([], schema), [])
        self.assertNotEqual(self.errors([1, 2, 3], schema), [])
        self.assertIn("repeats", self.errors([1, 1], schema)[0])
        self.assertIn("[1]", self.errors([1, "x"], schema)[0])

    def test_object_keywords(self) -> None:
        schema = {
            "type": "object",
            "required": ["a"],
            "additionalProperties": False,
            "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        }
        self.assertEqual(self.errors({"a": 1, "b": "x"}, schema), [])
        self.assertIn("missing required property 'a'", self.errors({}, schema)[0])
        self.assertIn("unexpected property 'c'", self.errors({"a": 1, "c": 2}, schema)[0])
        self.assertIn("$.a", self.errors({"a": "no"}, schema)[0])

    def test_pattern_properties_and_additional_schema(self) -> None:
        schema = {
            "type": "object",
            "patternProperties": {"^[0-9]+$": {"type": "string"}},
            "additionalProperties": False,
        }
        self.assertEqual(self.errors({"28": "x"}, schema), [])
        self.assertNotEqual(self.errors({"abc": "x"}, schema), [])
        self.assertNotEqual(self.errors({"28": 1}, schema), [])
        extra = {"type": "object", "additionalProperties": {"type": "integer"}}
        self.assertEqual(self.errors({"a": 1}, extra), [])
        self.assertNotEqual(self.errors({"a": "x"}, extra), [])

    def test_min_properties(self) -> None:
        self.assertNotEqual(self.errors({}, {"type": "object", "minProperties": 1}), [])
        self.assertEqual(self.errors({"a": 1}, {"type": "object", "minProperties": 1}), [])

    def test_local_ref(self) -> None:
        schema = {
            "$defs": {"id": {"type": "string", "minLength": 1}},
            "properties": {"x": {"$ref": "#/$defs/id"}},
        }
        self.assertEqual(self.errors({"x": "a"}, schema), [])
        self.assertNotEqual(self.errors({"x": ""}, schema), [])

    def test_annotations_are_ignored(self) -> None:
        schema = {"$schema": "x", "$id": "y", "title": "t", "description": "d",
                  "$comment": "c", "examples": [1], "default": 2, "type": "integer"}
        self.assertEqual(self.errors(1, schema), [])

    def test_a_keyword_the_validator_does_not_implement_is_an_error(self) -> None:
        for keyword in ("oneOf", "anyOf", "allOf", "not", "format", "dependencies", "if"):
            with self.assertRaises(lite.SchemaError, msg=keyword):
                lite.validate(1, {keyword: []})

    def test_a_remote_ref_is_an_error(self) -> None:
        with self.assertRaises(lite.SchemaError):
            lite.validate(1, {"$ref": "https://example.com/schema.json"})

    def test_an_unresolvable_ref_is_an_error(self) -> None:
        with self.assertRaises(lite.SchemaError):
            lite.validate(1, {"$ref": "#/nope"})

    def test_boolean_and_malformed_schemas_are_handled_explicitly(self) -> None:
        self.assertEqual(self.errors({"x": 1}, True), [])
        self.assertEqual(self.errors({"x": 1}, False), ["$: no value is allowed here"])
        with self.assertRaisesRegex(lite.SchemaError, "unknown JSON type 'decimal'"):
            self.errors(1, {"type": "decimal"})
        with self.assertRaisesRegex(lite.SchemaError, "schema must be an object or a boolean"):
            self.errors(1, None)

    def test_unrecognized_python_values_get_a_stable_type_name(self) -> None:
        errors = self.errors(object(), {"type": "string"})
        self.assertEqual(errors, ["$: expected string, got object"])

    def test_true_additional_properties_are_accepted(self) -> None:
        schema = {"type": "object", "additionalProperties": True}
        self.assertEqual(self.errors({"extra": {"anything": 1}}, schema), [])


class RegistrySchemaTests(unittest.TestCase):
    """The registry schema is checked for real, not mirrored by predicates."""

    SCHEMA = json.loads(
        (REPO_ROOT / "schemas" / "registry-v1.schema.json").read_text(encoding="utf-8")
    )
    SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def manifest(self) -> dict:
        return {
            "registryVersion": 1,
            "protocol": 28,
            "fixtures": [
                {
                    "id": "p28-xdr-a",
                    "path": "xdr/a.toml",
                    "sha256": self.SHA,
                    "surface": "xdr",
                    "category": "cap-0083",
                    "description": "d",
                    "payloads": [],
                }
            ],
            "packDigest": "sha256:" + self.SHA,
        }

    def test_the_schema_uses_only_supported_keywords(self) -> None:
        # Raises SchemaError on any keyword the validator does not implement.
        lite.validate(self.manifest(), self.SCHEMA)

    def test_a_valid_manifest_conforms(self) -> None:
        self.assertEqual(lite.validate(self.manifest(), self.SCHEMA), [])

    def test_violations_are_reported_with_a_location(self) -> None:
        bad = self.manifest()
        bad["registryVersion"] = 2
        bad["fixtures"][0]["sha256"] = "XYZ"
        bad["fixtures"][0]["surface"] = "grpc"
        bad["packDigest"] = "md5:abc"
        bad["extra"] = True
        text = "\n".join(lite.validate(bad, self.SCHEMA))
        self.assertIn("$.registryVersion", text)
        self.assertIn("$.fixtures[0].sha256", text)
        self.assertIn("$.fixtures[0].surface", text)
        self.assertIn("$.packDigest", text)
        self.assertIn("unexpected property 'extra'", text)

    def test_an_empty_fixture_list_is_rejected(self) -> None:
        bad = self.manifest()
        bad["fixtures"] = []
        self.assertIn("fewer than 1", "\n".join(lite.validate(bad, self.SCHEMA)))


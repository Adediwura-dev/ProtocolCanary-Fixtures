"""Tests for tools/validate/validate.py.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"

_spec = importlib.util.spec_from_file_location("validate", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate"] = validate
_spec.loader.exec_module(validate)

SCHEMA_SYNC_PATH = REPO_ROOT / "tools" / "validate" / "schema_sync.py"

_sync_spec = importlib.util.spec_from_file_location("schema_sync", SCHEMA_SYNC_PATH)
schema_sync = importlib.util.module_from_spec(_sync_spec)
assert _sync_spec.loader is not None
sys.modules["schema_sync"] = schema_sync
_sync_spec.loader.exec_module(schema_sync)


VALID_XDR = """
id = "p28-xdr-cap83-example"
protocol = 28
surface = "xdr"
category = "cap-0083"
description = "example"
source_reference = "CAP-0083"

type = "StellarValue"
kind = "decode-success"
value_base64 = "AAAAAA=="
"""

RPC_HEADER = """
id = "p28-rpc-example"
protocol = 28
surface = "rpc"
category = "network"
description = "example"
source_reference = "https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getNetwork"

method = "get-network"
"""

RPC_ASSERT_TABLE = """
[[assert]]
kind = "field-equals"
field = "protocolVersion"
value = 28
"""

VALID_RPC = RPC_HEADER + RPC_ASSERT_TABLE

VALID_SOROBAN = """
id = "p28-soroban-example"
protocol = 28
surface = "soroban"
category = "smoke"
description = "example"
source_reference = "https://developers.stellar.org/docs/tokens/stellar-asset-contract"

source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
function = "name"
sequence_number = 1

[expect]
kind = "simulation-success"
"""


def write(dir_path: Path, name: str, contents: str) -> Path:
    path = dir_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def run_main(argv: list[str]) -> tuple[int, str, str]:
    """Invoke ``validate.main`` capturing its (stdout, stderr).

    ``main`` is the actual entry point CI runs (``python3
    tools/validate/validate.py``): it parses argv, prints its report to
    stdout/stderr and returns an exit code. It does not return its
    ``Report``, so tests exercise it the way CI does rather than only
    through the lower-level ``validate_directory`` helper.
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = validate.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class ValidatorTests(unittest.TestCase):
    def run_validation(self, files: dict[str, str]) -> "validate.Report":
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, contents in files.items():
                write(root, name, contents)
            return validate.validate_directory(root)

    def test_empty_directory_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = validate.validate_directory(Path(tmp))
        self.assertEqual(report.errors, [])
        self.assertTrue(report.ok)

    def test_accepts_a_valid_xdr_fixture(self) -> None:
        report = self.run_validation({"a.toml": VALID_XDR})
        self.assertEqual(report.errors, [])

    def test_accepts_a_valid_rpc_fixture(self) -> None:
        report = self.run_validation({"a.toml": VALID_RPC})
        self.assertEqual(report.errors, [])

    def test_accepts_rpc_fixture_with_field_exists_assert(self) -> None:
        good = VALID_RPC.replace('kind = "field-equals"', 'kind = "field-exists"').replace(
            "\nvalue = 28\n", "\n"
        )
        report = self.run_validation({"a.toml": good})
        self.assertEqual(report.errors, [])

    def test_accepts_a_valid_soroban_fixture(self) -> None:
        report = self.run_validation({"a.toml": VALID_SOROBAN})
        self.assertEqual(report.errors, [])

    def test_discovers_fixtures_in_nested_subdirectories(self) -> None:
        files = {
            "flat.toml": VALID_XDR,
            "xdr/cap-0083/nested.toml": VALID_RPC,
            "xdr/cap-0085/deeper/deepest.toml": VALID_SOROBAN,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = {write(root, name, contents) for name, contents in files.items()}
            self.assertEqual(set(validate.find_fixture_files(root)), written)
            report = validate.validate_directory(root)
        self.assertEqual(report.errors, [])

    def test_rejects_duplicate_ids(self) -> None:
        other = VALID_XDR.replace(
            'category = "cap-0083"', 'category = "cap-0083-2"'
        )
        report = self.run_validation({"a.toml": VALID_XDR, "b.toml": other})
        self.assertTrue(any("duplicate fixture id" in e for e in report.errors))

    # os.chmod() does not restrict access on Windows, and root ignores the
    # permission bits entirely on POSIX, so on both the file stays readable and
    # this error path cannot be provoked.
    @unittest.skipIf(
        os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        "os.chmod is not effective on this platform/user",
    )
    def test_unreadable_file_reports_a_read_error(self) -> None:
        """A fixture file that cannot be read yields a 'failed to read file' error.

        Covers the ``except OSError`` branch in ``validate.load_fixture()``.
        Every other test uses ``TemporaryDirectory()``, where files are always
        readable, so this error path would otherwise go untested.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write(root, "a.toml", VALID_XDR)
            try:
                os.chmod(path, 0o000)
                try:
                    path.read_text(encoding="utf-8")
                except OSError:
                    pass
                else:
                    self.skipTest("a 0o000 file is still readable here")

                report = validate.validate_directory(root)
            finally:
                # Restore permissions before the directory is removed, so
                # cleanup never depends on the filesystem tolerating 0o000.
                os.chmod(path, 0o644)

        self.assertFalse(report.ok)
        matching = [e for e in report.errors if "failed to read file" in e]
        self.assertTrue(matching, f"expected a read error, got: {report.errors}")
        self.assertIn("a.toml", matching[0])

    def test_rejects_duplicate_ids_across_nested_directories(self) -> None:
        # The repository stores fixtures in nested per-surface/per-CAP
        # directories (e.g. protocol-28/xdr/cap-0083/a.toml vs.
        # protocol-28/soroban/b.toml), so duplicate detection must recurse
        # through the whole subtree rather than only compare files that sit
        # directly in the root. This guards against a regression that keeps
        # flat-directory detection working while breaking the recursive case.
        other = VALID_XDR.replace(
            'category = "cap-0083"', 'category = "cap-0083-2"'
        )
        report = self.run_validation(
            {
                "xdr/cap-0083/a.toml": VALID_XDR,
                "soroban/b.toml": other,
            }
        )
        duplicates = [e for e in report.errors if "duplicate fixture id" in e]
        self.assertTrue(duplicates, report.errors)
        # The error should point at one of the nested files, confirming the
        # nested fixture was actually discovered by the recursive walk.
        nested_paths = (
            str(Path("soroban") / "b.toml"),
            str(Path("xdr") / "cap-0083" / "a.toml"),
        )
        self.assertTrue(
            any(nested_path in error for nested_path in nested_paths for error in duplicates)
        )

    def test_rejects_invalid_surface(self) -> None:
        bad = VALID_XDR.replace('surface = "xdr"', 'surface = "wallet"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("'surface'" in e for e in report.errors))

    def test_error_message_includes_fixture_id(self) -> None:
        # A fixture whose id has been parsed and is valid should be nameable
        # from the reported error alone, so a failing CI run does not require
        # cross-referencing the path against the file's contents.
        bad = VALID_XDR.replace('surface = "xdr"', 'surface = "wallet"')
        report = self.run_validation({"a.toml": bad})
        matching = [e for e in report.errors if "'surface'" in e]
        self.assertTrue(matching, report.errors)
        self.assertTrue(
            all("p28-xdr-cap83-example" in e for e in matching), matching
        )
        # The id must be an annotation on the message, not a replacement for
        # the path that locates the offending file.
        self.assertTrue(all("a.toml" in e for e in matching), matching)

    def test_error_message_omits_id_when_id_is_invalid(self) -> None:
        # When id itself fails validation (here: it is not lowercase), it is
        # not yet known to be valid, so messages for that fixture fall back to
        # the path-only format rather than echoing an invalid id.
        bad = VALID_XDR.replace(
            'id = "p28-xdr-cap83-example"', 'id = "P28-XDR-CAP83-EXAMPLE"'
        ).replace('surface = "xdr"', 'surface = "wallet"')
        report = self.run_validation({"a.toml": bad})
        matching = [e for e in report.errors if "'surface'" in e]
        self.assertTrue(matching, report.errors)
        self.assertTrue(
            all("P28-XDR-CAP83-EXAMPLE" not in e for e in matching), matching
        )

    def test_warning_message_includes_fixture_id(self) -> None:
        # Report.warning shares Report.error's formatting, so a warning about a
        # fixture with a known id is annotated the same way.
        report = validate.Report()
        path = Path("a.toml")
        report.register_fixture_id(path, "p28-xdr-cap83-example")
        report.warning(path, "an advisory")
        self.assertEqual(
            report.warnings, ["a.toml [p28-xdr-cap83-example]: an advisory"]
        )

    def test_rejects_invalid_protocol_type(self) -> None:
        bad = VALID_XDR.replace("protocol = 28", 'protocol = "28"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("'protocol'" in e for e in report.errors))

    def test_rejects_non_positive_protocol(self) -> None:
        bad = VALID_XDR.replace("protocol = 28", "protocol = 0")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("positive integer" in e for e in report.errors))

    def test_rejects_negative_protocol(self) -> None:
        bad = VALID_XDR.replace("protocol = 28", "protocol = -1")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("positive integer" in e for e in report.errors))

    def test_rejects_unknown_capability(self) -> None:
        bad = VALID_XDR + '\nrequired_capabilities = ["not-a-real-capability"]\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("unknown capability" in e for e in report.errors))

    def test_accepts_known_required_capabilities(self) -> None:
        good = VALID_XDR + '\nrequired_capabilities = ["soroban-contract"]\n'
        report = self.run_validation({"a.toml": good})
        self.assertEqual(report.errors, [])

    def test_rejects_non_array_required_capabilities(self) -> None:
        bad = VALID_XDR + '\nrequired_capabilities = "rpc-client"\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("must be an array" in e for e in report.errors))

    def test_rejects_duplicate_required_capabilities(self) -> None:
        bad = VALID_XDR + '\nrequired_capabilities = ["rpc-client", "rpc-client"]\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("duplicate entry" in e for e in report.errors))

    def test_rejects_missing_input_file(self) -> None:
        bad = VALID_XDR + '\ninput_file = "does-not-exist.xdr.b64"\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("does not resolve to an existing file" in e for e in report.errors))

    def test_rejects_missing_expected_file(self) -> None:
        bad = VALID_XDR + '\nexpected_file = "does-not-exist.xdr.b64"\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("does not resolve to an existing file" in e for e in report.errors)
        )

    def test_rejects_empty_input_file(self) -> None:
        bad = VALID_XDR + '\ninput_file = ""\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("field 'input_file', if present, must be a non-empty string" in e for e in report.errors)
        )

    def test_rejects_non_string_input_file(self) -> None:
        # input_file is read from TOML, so an unquoted value can be parsed as
        # an int (or any other non-string) instead of the path the contributor
        # meant. The same guard that rejects an empty input_file must reject a
        # non-string one rather than attempting to resolve it as a path.
        bad = VALID_XDR + "\ninput_file = 42\n"
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("field 'input_file', if present, must be a non-empty string" in e for e in report.errors)
        )

    def test_rejects_non_string_expected_file(self) -> None:
        bad = VALID_XDR + '\nexpected_file = 123\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("field 'expected_file', if present, must be a non-empty string" in e for e in report.errors)
        )

    def test_accepts_an_existing_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "input.xdr.b64", "AAAAAA==")
            write(root, "a.toml", VALID_XDR + '\ninput_file = "input.xdr.b64"\n')
            report = validate.validate_directory(root)
        self.assertEqual(report.errors, [])

    def test_rejects_missing_expected_file(self) -> None:
        bad = VALID_XDR + '\nexpected_file = "does-not-exist.expected.b64"\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("does not resolve to an existing file" in e for e in report.errors))

    def test_accepts_an_existing_expected_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "expected.xdr.b64", "AAAAAA==")
            write(root, "a.toml", VALID_XDR + '\nexpected_file = "expected.xdr.b64"\n')
            report = validate.validate_directory(root)
        self.assertEqual(report.errors, [])

    def test_rejects_invalid_expectation_kind(self) -> None:
        bad = VALID_XDR.replace('kind = "decode-success"', 'kind = "not-a-real-kind"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("'kind'" in e for e in report.errors))

    def test_rejects_xdr_type_not_in_xdr_types(self) -> None:
        bad = VALID_XDR.replace('type = "StellarValue"', 'type = "LedgerEntry"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "LedgerEntry" in e
                and "StellarValue" in e
                and "ContractExecutable" in e
                for e in report.errors
            )
        )

    def test_xdr_fixture_rejects_missing_type(self) -> None:
        bad = VALID_XDR.replace('type = "StellarValue"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'type'" in e for e in report.errors),
            report.errors,
        )

    def test_xdr_fixture_rejects_missing_kind(self) -> None:
        bad = VALID_XDR.replace('kind = "decode-success"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'kind'" in e for e in report.errors),
            report.errors,
        )

    def test_xdr_fixture_rejects_missing_value_base64(self) -> None:
        bad = VALID_XDR.replace('value_base64 = "AAAAAA=="\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "missing required field 'value_base64'" in e
                for e in report.errors
            ),
            report.errors,
        )

    def test_encode_equals_requires_expected_base64(self) -> None:
        bad = VALID_XDR.replace('kind = "decode-success"', 'kind = "encode-equals"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("expected_base64" in e for e in report.errors))

    def test_rejects_empty_min_length_fields(self) -> None:
        cases = {
            "value_base64": VALID_XDR.replace('value_base64 = "AAAAAA=="', 'value_base64 = ""'),
            "expected_base64": (
                VALID_XDR.replace('kind = "decode-success"', 'kind = "encode-equals"')
                + 'expected_base64 = ""\n'
            ),
            "source_account": VALID_SOROBAN.replace(
                'source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"',
                'source_account = ""',
            ),
            "contract_id": VALID_SOROBAN.replace(
                'contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"',
                'contract_id = ""',
            ),
            "function": VALID_SOROBAN.replace('function = "name"', 'function = ""'),
        }
        for field, fixture in cases.items():
            with self.subTest(field=field):
                report = self.run_validation({"a.toml": fixture})
                self.assertTrue(
                    any(f"field '{field}' must not be empty" in error for error in report.errors),
                    report.errors,
                )

    def test_rejects_non_string_source_reference(self) -> None:
        bad = VALID_XDR.replace('source_reference = "CAP-0083"', 'source_reference = 83')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("source_reference" in e for e in report.errors))

    def test_rejects_empty_source_reference(self) -> None:
        bad = VALID_XDR.replace('source_reference = "CAP-0083"', 'source_reference = ""')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("source_reference" in e for e in report.errors))

    def test_errors_on_missing_source_reference(self) -> None:
        # Every fixture must cite an authoritative upstream source (see
        # CONTRIBUTING.md and SECURITY.md). A missing source_reference is a
        # hard validation error, not a warning, so it must fail the build
        # rather than pass with a printed advisory.
        bad = VALID_XDR.replace('source_reference = "CAP-0083"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "source_reference" in e
                and "authoritative upstream source" in e
                and "CAP-0083" in e
                for e in report.errors
            ),
            report.errors,
        )
        # It must be reported as an error only, never downgraded to a warning.
        self.assertFalse(
            any("source_reference" in w for w in report.warnings),
            report.warnings,
        )

    def test_accepts_a_present_source_reference(self) -> None:
        # The pass case for the rule above: a fixture that cites an
        # authoritative source produces no source_reference error.
        report = self.run_validation({"a.toml": VALID_XDR})
        self.assertFalse(
            any("source_reference" in e for e in report.errors), report.errors
        )

    def test_rejects_malformed_toml(self) -> None:
        report = self.run_validation({"a.toml": "not valid [[[ toml"})
        self.assertTrue(any("invalid TOML" in e for e in report.errors))

    def test_load_fixture_handles_unreadable_file_oserror(self) -> None:
        report = validate.Report()
        path = Path("unreadable.toml")
        with mock.patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = validate.load_fixture(path, report)
        self.assertIsNone(result)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("failed to read file: Permission denied", report.errors[0])

    def test_rejects_missing_id(self) -> None:
        data = {
            "protocol": 28,
            "surface": "xdr",
            "category": "cap-0083",
            "description": "example",
            "source_reference": "CAP-0083",
        }
        fixture = validate.Fixture(Path("missing-id.toml"), data)
        report = validate.Report()

        validate.validate_common_fields(fixture, report)

        self.assertTrue(
            any("missing required field 'id'" in error for error in report.errors),
            report.errors,
        )

    def test_rejects_non_string_id(self) -> None:
        data = {
            "id": 12345,
            "protocol": 28,
            "surface": "xdr",
            "category": "cap-0083",
            "description": "example",
            "source_reference": "CAP-0083",
        }
        fixture = validate.Fixture(Path("non-string-id.toml"), data)
        report = validate.Report()

        validate.validate_common_fields(fixture, report)

        self.assertTrue(
            any("field 'id' must be of type str" in error for error in report.errors),
            report.errors,
        )

    def test_run_validation_rejects_non_string_id(self) -> None:
        bad = VALID_XDR.replace('id = "p28-xdr-cap83-example"', "id = 12345")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("field 'id' must be of type str" in error for error in report.errors),
            report.errors,
        )

    def test_known_fixture_id_returns_none_for_non_string_id(self) -> None:
        fx = validate.Fixture(Path("test.toml"), {"id": 12345})
        self.assertIsNone(validate.known_fixture_id(fx))

    def test_rejects_missing_surface(self) -> None:
        data = {
            "id": "p28-xdr-cap83-missing-surface",
            "protocol": 28,
            "category": "cap-0083",
            "description": "example",
            "source_reference": "CAP-0083",
        }
        fixture = validate.Fixture(Path("missing-surface.toml"), data)
        report = validate.Report()

        validate.validate_common_fields(fixture, report)

        self.assertTrue(
            any("missing required field 'surface'" in error for error in report.errors),
            report.errors,
        )

    def test_rejects_missing_category(self) -> None:
        data = {
            "id": "p28-xdr-cap83-missing-category",
            "protocol": 28,
            "surface": "xdr",
            "description": "example",
            "source_reference": "CAP-0083",
        }
        fixture = validate.Fixture(Path("missing-category.toml"), data)
        report = validate.Report()

        validate.validate_common_fields(fixture, report)

        self.assertTrue(
            any("missing required field 'category'" in error for error in report.errors),
            report.errors,
        )

    def test_rejects_non_string_category(self) -> None:
        data = {
            "id": "p28-xdr-cap83-non-string-category",
            "protocol": 28,
            "surface": "xdr",
            "category": 123,
            "description": "example",
            "source_reference": "CAP-0083",
        }
        fixture = validate.Fixture(Path("non-string-category.toml"), data)
        report = validate.Report()

        validate.validate_common_fields(fixture, report)

        self.assertTrue(
            any("field 'category' must be of type str" in error for error in report.errors),
            report.errors,
        )

    def test_rejects_empty_category(self) -> None:
        bad = VALID_XDR.replace('category = "cap-0083"', 'category = ""')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("must not be empty" in e and "'category'" in e for e in report.errors))

    def test_rejects_vague_category(self) -> None:
        for vague in ("misc", "other", "test", "general"):
            with self.subTest(category=vague):
                bad = VALID_XDR.replace('category = "cap-0083"', f'category = "{vague}"')
                report = self.run_validation({"a.toml": bad})
                self.assertTrue(any("too vague" in e for e in report.errors))

    def test_rejects_vague_category_in_any_letter_case(self) -> None:
        for vague in ("Misc", "OTHER", "Test", "GENERAL"):
            with self.subTest(category=vague):
                bad = VALID_XDR.replace('category = "cap-0083"', f'category = "{vague}"')
                report = self.run_validation({"a.toml": bad})
                self.assertTrue(any("too vague" in e for e in report.errors), report.errors)

    def test_rejects_missing_description(self) -> None:
        bad = VALID_XDR.replace('description = "example"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("description" in e for e in report.errors))

    def test_rejects_non_string_description(self) -> None:
        data = {
            "id": "p28-xdr-cap83-non-string-description",
            "protocol": 28,
            "surface": "xdr",
            "category": "cap-0083",
            "description": 123,
            "source_reference": "CAP-0083",
        }
        fixture = validate.Fixture(Path("non-string-description.toml"), data)
        report = validate.Report()

        validate.validate_common_fields(fixture, report)

        self.assertTrue(
            any("field 'description' must be of type str" in error for error in report.errors),
            report.errors,
        )

    def test_rejects_empty_description(self) -> None:
        # schemas/fixture-v1.schema.json declares description with
        # minLength 1, so an empty string must fail validation the same way
        # an empty id or category does, rather than passing structurally.
        bad = VALID_XDR.replace('description = "example"', 'description = ""')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("field 'description' must not be empty" in e for e in report.errors),
            report.errors,
        )

    def test_rejects_empty_id(self) -> None:
        bad = VALID_XDR.replace(
            'id = "p28-xdr-cap83-example"', 'id = ""'
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("must not be empty" in e and "'id'" in e for e in report.errors))

    def test_rejects_missing_description(self) -> None:
        bad = VALID_XDR.replace('description = "example"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("description" in e for e in report.errors))

    def test_rejects_uppercase_id(self) -> None:
        bad = VALID_XDR.replace(
            'id = "p28-xdr-cap83-example"', 'id = "P28-XDR-CAP83-EXAMPLE"'
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("lowercase" in e for e in report.errors))

    def test_rejects_empty_id(self) -> None:
        bad = VALID_XDR.replace(
            'id = "p28-xdr-cap83-example"', 'id = ""'
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("field 'id' must not be empty" in e for e in report.errors))

    def test_rejects_non_table_assert_entry(self) -> None:
        # TOML permits an array element to be a non-table value; validate_rpc_body
        # has an explicit branch for that case. Mix a well-formed assert table
        # with a bare string so the malformed entry is the only error reported.
        bad = """
id = "p28-rpc-mixed-assert"
protocol = 28
surface = "rpc"
category = "network"
description = "example"
source_reference = "https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getNetwork"

method = "get-network"

assert = [
    { kind = "field-equals", field = "protocolVersion", value = 28 },
    "not-a-table",
]
"""
        report = self.run_validation({"a.toml": bad})
        self.assertIn(
            "assert[1] must be a table",
            "\n".join(report.errors),
        )
        # The valid entry alongside the malformed one must not itself error.
        self.assertFalse(any("assert[0]" in e for e in report.errors))

    def test_unknown_top_level_field_is_not_an_error(self) -> None:
        # Documents current behavior: validate.py performs no top-level
        # additionalProperties check, so an unrecognized field (e.g. a typo'd
        # field name) is silently accepted rather than rejected. If this ever
        # changes, this test should fail and force a deliberate decision.
        with_unknown = VALID_XDR.replace(
            'source_reference = "CAP-0083"',
            'source_reference = "CAP-0083"\nsoure_reference = "typo"',
        )
        report = self.run_validation({"a.toml": with_unknown})
        self.assertEqual(report.errors, [])

    def test_rpc_fixture_requires_at_least_one_assert(self) -> None:
        bad = """
id = "p28-rpc-no-assert"
protocol = 28
surface = "rpc"
category = "network"
description = "example"
source_reference = "https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getNetwork"

method = "get-network"
"""
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("at least one" in e for e in report.errors))

    def test_rpc_fixture_rejects_invalid_method(self) -> None:
        # `get-balance` is a real Stellar RPC method that canary-rpc does not
        # implement; `getTransactions` is the case #159 asks for. Neither may
        # be silently accepted just because it looks like a plausible name.
        for invalid_method in ("get-balance", "getTransactions"):
            with self.subTest(method=invalid_method):
                bad = VALID_RPC.replace(
                    'method = "get-network"', f'method = "{invalid_method}"'
                )
                report = self.run_validation({"a.toml": bad})
                errors = [e for e in report.errors if "'method'" in e]
                self.assertEqual(len(errors), 1, report.errors)
                # The error must name the offending method and enumerate the
                # methods the validator accepts, so that any change to
                # RPC_METHODS is detectable from the message alone.
                self.assertIn(invalid_method, errors[0])
                for supported in ("get-network", "get-latest-ledger"):
                    self.assertIn(supported, errors[0])

    def test_rpc_fixture_rejects_missing_method(self) -> None:
        bad = VALID_RPC.replace('method = "get-network"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'method'" in e for e in report.errors),
            report.errors,
        )

    def test_rpc_fixture_rejects_empty_assert_array(self) -> None:
        bad = RPC_HEADER + "\nassert = []\n"
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("non-empty array" in e for e in report.errors))

    def test_rpc_fixture_rejects_non_array_assert_value(self) -> None:
        bad = (
            RPC_HEADER
            + '\nassert = { kind = "field-equals", field = "protocolVersion", value = 28 }\n'
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("'assert' must be a non-empty array of tables" in e for e in report.errors),
            report.errors,
        )

    def test_rpc_fixture_rejects_non_table_assert_entry(self) -> None:
        bad = RPC_HEADER + '\nassert = ["not-a-table"]\n'
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("assert[0] must be a table" in e for e in report.errors))

    def test_rpc_fixture_rejects_invalid_assert_kind(self) -> None:
        bad = RPC_HEADER + """
[[assert]]
kind = "not-a-real-kind"
field = "protocolVersion"
"""
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("assert[0].kind" in e for e in report.errors))

    def test_rpc_fixture_rejects_missing_assert_field(self) -> None:
        bad = RPC_HEADER + """
[[assert]]
kind = "field-equals"
value = 28
"""
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("missing required non-empty 'field'" in e for e in report.errors))

    def test_rpc_fixture_rejects_empty_assert_field(self) -> None:
        bad = RPC_HEADER + """
[[assert]]
kind = "field-equals"
field = ""
value = 28
"""
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("missing required non-empty 'field'" in e for e in report.errors))

    def test_rpc_fixture_rejects_field_equals_without_value(self) -> None:
        bad = RPC_HEADER + """
[[assert]]
kind = "field-equals"
field = "protocolVersion"
"""
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("kind=field-equals requires 'value'" in e for e in report.errors))

    def test_rpc_fixture_rejects_invalid_expected_type(self) -> None:
        bad = RPC_HEADER + """
[[assert]]
kind = "field-type"
field = "protocolVersion"
expected_type = "not-a-real-type"
"""
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("assert[0].expected_type" in e for e in report.errors))
    def test_rejects_unrecognized_rpc_assert_kind(self) -> None:
        bad = VALID_RPC.replace('kind = "field-equals"', 'kind = "field-contains"')
        report = self.run_validation({"a.toml": bad})
        errors = [e for e in report.errors if "assert[0].kind" in e]
        self.assertEqual(len(errors), 1, report.errors)
        # The error must name the offending kind and enumerate the kinds the
        # validator does accept, so that neither widening nor narrowing
        # RPC_ASSERT_KINDS can pass unnoticed.
        self.assertIn("field-contains", errors[0])
        for supported in ("field-exists", "field-absent", "field-type", "field-equals"):
            self.assertIn(supported, errors[0])

    def test_accepts_rpc_fixture_with_field_absent(self) -> None:
        fixture = RPC_HEADER + """
[[assert]]
kind = "field-absent"
field = "error"
"""
        report = self.run_validation({"a.toml": fixture})
        self.assertEqual(report.errors, [])

    def test_soroban_fixture_requires_expect(self) -> None:
        bad = VALID_SOROBAN.replace("[expect]\nkind = \"simulation-success\"\n", "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("'expect'" in e for e in report.errors))

    def test_rejects_non_array_args(self) -> None:
        # schemas/fixture-v1.schema.json declares soroban "args" as
        # { "type": "array" }; a non-array value must be reported here too.
        for literal in ('"not-an-array"', "5"):
            with self.subTest(args=literal):
                bad = VALID_SOROBAN.replace(
                    "sequence_number = 1",
                    f"sequence_number = 1\nargs = {literal}",
                )
                report = self.run_validation({"a.toml": bad})
                self.assertTrue(
                    any("'args'" in e and "array" in e for e in report.errors),
                    f"expected an 'args' array error for args = {literal}, "
                    f"got: {report.errors}",
                )

    def test_accepts_array_args(self) -> None:
        good = VALID_SOROBAN.replace(
            "sequence_number = 1", 'sequence_number = 1\nargs = ["name"]'
        )
        report = self.run_validation({"a.toml": good})
        self.assertEqual(report.errors, [])
    def test_soroban_fixture_rejects_unknown_expect_kind(self) -> None:
        # [expect] is present but its kind is not in SOROBAN_EXPECT_KINDS —
        # a different branch of validate_soroban_body than the missing-
        # [expect] case above.
        bad = VALID_SOROBAN.replace(
            'kind = "simulation-success"', 'kind = "simulation-timeout"'
        )
        report = self.run_validation({"a.toml": bad})
        errors = [e for e in report.errors if "expect.kind" in e]
        self.assertEqual(len(errors), 1, report.errors)
        # The error must name the offending value and enumerate the kinds the
        # validator does accept, so that neither widening nor narrowing
        # SOROBAN_EXPECT_KINDS can pass unnoticed.
        self.assertIn("simulation-timeout", errors[0])
        for supported in validate.SOROBAN_EXPECT_KINDS:
            self.assertIn(supported, errors[0])

    def test_soroban_fixture_rejects_missing_source_account(self) -> None:
        bad = VALID_SOROBAN.replace(
            'source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"\n',
            "",
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'source_account'" in e for e in report.errors)
        )

    def test_soroban_fixture_rejects_non_string_source_account(self) -> None:
        bad = VALID_SOROBAN.replace(
            'source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"',
            "source_account = 12345",
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "'source_account'" in e and "must be of type str" in e and "int" in e
                for e in report.errors
            ),
            report.errors,
        )

    def test_soroban_fixture_rejects_missing_contract_id(self) -> None:
        bad = VALID_SOROBAN.replace(
            'contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"\n',
            "",
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'contract_id'" in e for e in report.errors)
        )

    def test_soroban_fixture_rejects_non_string_contract_id(self) -> None:
        bad = VALID_SOROBAN.replace(
            'contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"',
            'contract_id = true',
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "'contract_id'" in e and "must be of type str" in e and "bool" in e
                for e in report.errors
            ),
            report.errors,
        )

    def test_soroban_fixture_rejects_missing_function(self) -> None:
        bad = VALID_SOROBAN.replace('function = "name"\n', "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'function'" in e for e in report.errors)
        )

    def test_soroban_fixture_rejects_non_string_function(self) -> None:
        bad = VALID_SOROBAN.replace('function = "name"', 'function = ["name"]')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "'function'" in e and "must be of type str" in e and "list" in e
                for e in report.errors
            ),
            report.errors,
        )

    def test_soroban_fixture_rejects_missing_sequence_number(self) -> None:
        bad = VALID_SOROBAN.replace("sequence_number = 1\n", "")
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any("missing required field 'sequence_number'" in e for e in report.errors)
        )

    def test_soroban_fixture_rejects_non_integer_sequence_number(self) -> None:
        bad = VALID_SOROBAN.replace("sequence_number = 1", 'sequence_number = "1"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(
            any(
                "'sequence_number'" in e and "must be of type int" in e and "str" in e
                for e in report.errors
            ),
            report.errors,
        )

    def test_soroban_fixture_rejects_non_table_expect(self) -> None:
        bad = VALID_SOROBAN.replace(
            "[expect]\nkind = \"simulation-success\"\n",
            'expect = "simulation-success"\n',
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("'expect' must be a table" in e for e in report.errors))

    def test_rejects_invalid_base64_in_value_base64(self) -> None:
        bad = VALID_XDR.replace('value_base64 = "AAAAAA=="', 'value_base64 = "not-valid-base64!!!"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("not valid base64" in e and "value_base64" in e for e in report.errors))

    def test_rejects_malformed_padding_base64(self) -> None:
        bad = VALID_XDR.replace('value_base64 = "AAAAAA=="', 'value_base64 = "AAAAA"')
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("not valid base64" in e and "value_base64" in e for e in report.errors))

    def test_rejects_invalid_base64_in_expected_base64(self) -> None:
        bad = (
            VALID_XDR.replace('kind = "decode-success"', 'kind = "encode-equals"')
            + 'expected_base64 = "not-valid-base64!!!"\n'
        )
        report = self.run_validation({"a.toml": bad})
        self.assertTrue(any("not valid base64" in e and "expected_base64" in e for e in report.errors))

    def test_accepts_valid_base64_in_encode_equals(self) -> None:
        good = (
            VALID_XDR.replace('kind = "decode-success"', 'kind = "encode-equals"')
            + 'expected_base64 = "AAAAAA=="\n'
        )
        report = self.run_validation({"a.toml": good})
        self.assertEqual(report.errors, [])

    def test_main_autodiscovers_protocol_directories_when_no_arguments_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "protocol-28/xdr/cap-0083/fixture.toml", VALID_XDR)
            write(root, "not-a-protocol/other.toml", "invalid toml [[[")
            fake_file = str(root / "tools" / "validate" / "validate.py")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(validate, "__file__", fake_file):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = validate.main([])
            self.assertEqual(code, 0)
            self.assertEqual(err.getvalue(), "")
            self.assertIn("1 fixture file(s) valid across 1 root(s)", out.getvalue())

    def test_main_rejects_non_directory_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "not-a-directory.txt"
            file_path.write_text("dummy", encoding="utf-8")
            code, out, err = run_main([str(file_path)])
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)
        self.assertIn("FAILED: 1 error(s)", err)

    def test_main_rejects_non_existent_root(self) -> None:
        code, out, err = run_main(["non-existent-root-path-for-test"])
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)
        self.assertIn("FAILED: 1 error(s)", err)


class QuietFlagTests(unittest.TestCase):
    """`--quiet` suppresses warnings while keeping errors and the summary.

    A missing ``source_reference`` is now an error rather than a warning, so
    no built-in validator rule currently emits a warning. To keep the flag's
    suppression path covered, these tests inject a warning into the report
    returned for the temporary root. Errors are produced by a real invalid
    fixture, so both halves of the flag's behavior are exercised together.
    """

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = validate.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write_error_fixture(self, root: Path) -> None:
        # Invalid surface -> error only (source_reference is still present).
        write(root, "error.toml", VALID_XDR.replace('surface = "xdr"', 'surface = "wallet"'))

    def with_injected_warning(self):
        # Wrap the real validate_directory so the report it returns also carries
        # a warning, giving --quiet something to suppress.
        real = validate.validate_directory

        def wrapper(root: Path) -> "validate.Report":
            report = real(root)
            report.warning(Path(root) / "injected.toml", "injected warning")
            return report

        return mock.patch.object(validate, "validate_directory", side_effect=wrapper)

    def test_default_prints_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_error_fixture(root)
            with self.with_injected_warning():
                code, out, err = self.run_main([str(root)])
        self.assertIn("warning:", out)
        self.assertIn("error:", err)
        self.assertEqual(code, 1)

    def test_quiet_suppresses_warnings_but_keeps_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_error_fixture(root)
            with self.with_injected_warning():
                code, out, err = self.run_main(["--quiet", str(root)])
        self.assertNotIn("warning:", out)
        self.assertIn("error:", err)
        self.assertEqual(code, 1)

    def test_quiet_keeps_ok_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "ok.toml", VALID_XDR)
            with self.with_injected_warning():
                code, out, err = self.run_main(["--quiet", str(root)])
        self.assertNotIn("warning:", out)
        self.assertIn("OK:", out)
        self.assertEqual(code, 0)


class SchemaSyncTests(unittest.TestCase):
    """schemas/fixture-v1.schema.json must not drift from validate.py.

    The schema is this repository's editor-facing mirror of the validator's
    rules, but nothing consumes it at run time. ``schema_sync.py`` (standard
    library only) compares the schema's enums and required-field lists with
    ``validate.py``'s constants; these tests pin that enforcement so a
    validator change such as adding a new XDR type or RPC method cannot
    silently leave the schema stale. See CONTRIBUTING.md#fixture-schema.
    """

    def setUp(self) -> None:
        self.schema = schema_sync.load_schema()
        self.validator = validate

    def test_repository_schema_is_in_sync_with_the_validator(self) -> None:
        self.assertEqual(schema_sync.check_sync(self.schema, self.validator), [])

    def test_main_reports_ok_for_the_repository(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = schema_sync.main([])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("OK:", out.getvalue())

    def test_detects_rpc_method_the_schema_lacks(self) -> None:
        # Simulates validate.py gaining an RPC method the schema does not
        # list: the schema is unchanged, so dropping a method from its enum
        # is equivalent to the validator supporting an extra one.
        drift = copy.deepcopy(self.schema)
        block = schema_sync.surface_block(drift, "rpc")
        assert block is not None
        block["properties"]["method"]["enum"].remove("get-latest-ledger")
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("rpc method enum" in e for e in errors), errors)
        self.assertTrue(any("get-latest-ledger" in e for e in errors), errors)

    def test_detects_rpc_method_the_validator_lacks(self) -> None:
        drift = copy.deepcopy(self.schema)
        block = schema_sync.surface_block(drift, "rpc")
        assert block is not None
        block["properties"]["method"]["enum"].append("get-transaction")
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("rpc method enum" in e for e in errors), errors)
        self.assertTrue(any("get-transaction" in e for e in errors), errors)

    def test_detects_xdr_type_drift(self) -> None:
        drift = copy.deepcopy(self.schema)
        block = schema_sync.surface_block(drift, "xdr")
        assert block is not None
        block["properties"]["type"]["enum"].append("LedgerEntry")
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("xdr type enum" in e for e in errors), errors)

    def test_detects_missing_required_field(self) -> None:
        drift = copy.deepcopy(self.schema)
        drift["required"].remove("source_reference")
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("top-level required fields" in e for e in errors), errors)
        self.assertTrue(any("source_reference" in e for e in errors), errors)

    def test_detects_capability_drift(self) -> None:
        drift = copy.deepcopy(self.schema)
        drift["properties"]["required_capabilities"]["items"]["enum"].append(
            "time-travel"
        )
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("required_capabilities enum" in e for e in errors), errors)

    def test_detects_a_missing_surface_block(self) -> None:
        drift = copy.deepcopy(self.schema)
        drift["allOf"] = [
            block
            for block in drift["allOf"]
            if block["if"]["properties"]["surface"]["const"] != "soroban"
        ]
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("soroban surface" in e for e in errors), errors)

    def test_detects_a_missing_xdr_surface_block(self) -> None:
        drift = copy.deepcopy(self.schema)
        drift["allOf"] = [
            block
            for block in drift["allOf"]
            if block["if"]["properties"]["surface"]["const"] != "xdr"
        ]
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("xdr surface" in e for e in errors), errors)

    def test_detects_a_missing_rpc_surface_block(self) -> None:
        drift = copy.deepcopy(self.schema)
        drift["allOf"] = [
            block
            for block in drift["allOf"]
            if block["if"]["properties"]["surface"]["const"] != "rpc"
        ]
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("rpc surface" in e for e in errors), errors)

    def test_detects_surface_enum_drift(self) -> None:
        drift = copy.deepcopy(self.schema)
        drift["properties"]["surface"]["enum"].append("cli")
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("surface enum" in e for e in errors), errors)

    def test_detects_xdr_kind_drift(self) -> None:
        drift = copy.deepcopy(self.schema)
        block = schema_sync.surface_block(drift, "xdr")
        assert block is not None
        block["properties"]["kind"]["enum"].append("fuzz-test")
        errors = schema_sync.check_sync(drift, self.validator)
        self.assertTrue(any("xdr kind enum" in e for e in errors), errors)

    def test_main_reports_failure_on_schema_validator_drift(self) -> None:
        with mock.patch.object(
            schema_sync, "check_sync", return_value=["rpc method enum drift"]
        ):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = schema_sync.main([])
        self.assertEqual(code, 1)
        self.assertIn("error: rpc method enum drift", err.getvalue())
        self.assertIn("FAILED: 1 schema/validator drift error(s)", err.getvalue())

    def test_main_rejects_unexpected_arguments(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = schema_sync.main(["--unexpected-flag"])
        self.assertEqual(code, 2)
        self.assertIn("usage:", err.getvalue())


class SourceReferenceFormatTests(unittest.TestCase):
    """`source_reference` must be a CAP identifier or a pinned https URL (F-025).

    The syntactic rule is also expressed as ``source_reference.pattern`` in
    schemas/fixture-v1.schema.json; ``SchemaSyncTests`` pins the two together.
    The rule a regex cannot express, that a GitHub link names a commit or a
    tag and not a branch, is checked only by ``validate.check_source_reference``.
    """

    SHA = "9cd703075d87a6ce293752b1532e7b68efe12ae1"

    def problem(self, value: str) -> str | None:
        return validate.check_source_reference(value)

    def test_accepts_a_cap_identifier(self) -> None:
        for value in ("CAP-0083", "CAP-0001", "CAP-9999"):
            self.assertIsNone(self.problem(value), value)

    def test_accepts_documentation_and_standards_urls(self) -> None:
        for value in (
            "https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getNetwork",
            "https://developers.stellar.org/docs/networks/software-versions#protocol-29",
            "https://www.rfc-editor.org/rfc/rfc8785",
            "https://datatracker.ietf.org/doc/html/rfc9110#section-15.5",
        ):
            self.assertIsNone(self.problem(value), value)

    def test_accepts_github_links_pinned_to_a_commit_or_a_tag(self) -> None:
        base = "https://github.com/stellar/stellar-protocol"
        for value in (
            f"{base}/blob/{self.SHA}/core/cap-0083.md",
            f"{base}/blob/{self.SHA}/core/cap-0083.md#L10-L20",
            f"{base}/tree/{self.SHA}/core",
            f"{base}/blob/v28.0.1/core/cap-0083.md",
            f"{base}/blob/28.0/core/cap-0083.md",
            f"{base}/blob/protocol-28/core/cap-0083.md",
            f"{base}/blob/core-v21.0.0-rc.1/core/cap-0083.md",
            f"{base}/releases/tag/v28.0.1",
            f"{base}/commit/{self.SHA}",
            f"{base}/issues/1234",
            f"{base}/pull/1234",
            f"https://raw.githubusercontent.com/stellar/stellar-protocol/{self.SHA}/core/cap-0083.md",
            "https://raw.githubusercontent.com/stellar/stellar-protocol/v28.0.1/core/cap-0083.md",
            "https://raw.githubusercontent.com/stellar/stellar-protocol/refs/tags/v28.0.1/core/cap-0083.md",
        ):
            self.assertIsNone(self.problem(value), value)

    def test_rejects_free_text(self) -> None:
        for value in ("see issue", "TODO", "n/a", "the CAP", "CAP 0083", "CAP-83", "cap-0083"):
            message = self.problem(value)
            self.assertIsNotNone(message, value)
            self.assertIn("CAP identifier", message)

    def test_rejects_relative_links_and_non_https_schemes(self) -> None:
        for value in (
            "docs/cap-0083.md",
            "./cap-0083.md",
            "../cap-0083.md",
            "/docs/cap-0083.md",
            "//github.com/stellar/stellar-protocol/blob/v1/core/cap-0083.md",
            "ftp://example.com/cap-0083.md",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "mailto:someone@example.com",
        ):
            self.assertIsNotNone(self.problem(value), value)

    def test_rejects_plain_http(self) -> None:
        message = self.problem("http://developers.stellar.org/docs/x")
        self.assertIsNotNone(message)
        self.assertIn("https://", message)

    def test_rejects_urls_without_a_host_or_a_path(self) -> None:
        for value in ("https://", "https:///docs/x", "https://example.com", "https://example.com/"):
            self.assertIsNotNone(self.problem(value), value)
        message = self.problem("https://localhost/docs/x")
        self.assertIsNotNone(message)
        self.assertIn("host", message)

    def test_rejects_embedded_credentials(self) -> None:
        for value in (
            f"https://user:secret@github.com/stellar/stellar-protocol/blob/{self.SHA}/core/cap-0083.md",
            "https://token@example.com/docs/x",
        ):
            message = self.problem(value)
            self.assertIsNotNone(message, value)
            self.assertIn("credentials", message)

    def test_rejects_whitespace(self) -> None:
        for value in (
            "https://example.com/a b",
            " https://example.com/docs/x",
            "https://example.com/docs/x ",
            "https://example.com/docs/x\n",
            "https://example.com/\tdocs",
        ):
            self.assertIsNotNone(self.problem(value), repr(value))

    def test_rejects_a_github_link_that_follows_a_branch(self) -> None:
        base = "https://github.com/stellar/stellar-protocol"
        for ref in ("master", "main", "develop", "HEAD", "latest", "next", "my-branch"):
            message = self.problem(f"{base}/blob/{ref}/core/cap-0083.md")
            self.assertIsNotNone(message, ref)
            self.assertIn(repr(ref), message)
            self.assertIn("pin", message)
        self.assertIsNotNone(self.problem(f"{base}/tree/master/core"))
        self.assertIsNotNone(
            self.problem("https://raw.githubusercontent.com/stellar/stellar-protocol/master/core/cap-0083.md")
        )
        self.assertIsNotNone(
            self.problem(
                "https://raw.githubusercontent.com/stellar/stellar-protocol/refs/heads/master/core/cap-0083.md"
            )
        )

    def test_rejects_a_short_commit_hash(self) -> None:
        # Seven characters is ambiguous and can start to collide; the link
        # must name the full object so it cannot silently change meaning.
        base = "https://github.com/stellar/stellar-protocol"
        self.assertIsNotNone(self.problem(f"{base}/blob/9cd7030/core/cap-0083.md"))
        self.assertIsNotNone(self.problem(f"{base}/blob/{self.SHA.upper()}/core/cap-0083.md"))

    def test_rejects_a_github_repository_root_and_a_ref_less_link(self) -> None:
        for value in (
            "https://github.com/stellar/stellar-protocol",
            "https://github.com/stellar/stellar-protocol/",
            "https://github.com/stellar",
            "https://github.com/stellar/stellar-protocol/blob",
        ):
            self.assertIsNotNone(self.problem(value), value)

    def test_rejects_an_overlong_reference(self) -> None:
        value = "https://example.com/" + "a" * validate.MAX_SOURCE_REFERENCE_LENGTH
        message = self.problem(value)
        self.assertIsNotNone(message)
        self.assertIn("characters", message)

    def test_the_error_names_the_field_the_value_and_the_fix(self) -> None:
        bad = VALID_XDR.replace(
            'source_reference = "CAP-0083"',
            'source_reference = "https://github.com/stellar/stellar-protocol/blob/master/core/cap-0083.md"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "a.toml", bad)
            report = validate.validate_directory(Path(tmp))
        matching = [e for e in report.errors if "source_reference" in e]
        self.assertEqual(len(matching), 1, report.errors)
        self.assertIn("'master'", matching[0])
        self.assertIn("pin the link to a full commit SHA or a release tag", matching[0])
        self.assertIn("blob/master/core/cap-0083.md", matching[0])

    def test_a_pinned_github_link_passes_end_to_end(self) -> None:
        good = VALID_XDR.replace(
            'source_reference = "CAP-0083"',
            f'source_reference = "https://github.com/stellar/stellar-protocol/blob/{self.SHA}/core/cap-0083.md"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "a.toml", good)
            report = validate.validate_directory(Path(tmp))
        self.assertEqual(report.errors, [])

    def test_free_text_fails_end_to_end(self) -> None:
        bad = VALID_XDR.replace('source_reference = "CAP-0083"', 'source_reference = "see issue"')
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "a.toml", bad)
            report = validate.validate_directory(Path(tmp))
        self.assertTrue(any("source_reference" in e and "'see issue'" in e for e in report.errors))

    def test_every_shipped_fixture_reference_passes(self) -> None:
        for path in sorted((REPO_ROOT).glob("protocol-*/**/*.toml")):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            reference = data.get("source_reference")
            self.assertIsInstance(reference, str, path)
            self.assertIsNone(validate.check_source_reference(reference), f"{path}: {reference}")

    def test_the_schema_example_passes_the_validator_rule(self) -> None:
        schema = schema_sync.load_schema()
        example = schema["examples"][0]["source_reference"]
        self.assertIsNone(validate.check_source_reference(example))

    def test_schema_sync_detects_a_drifted_pattern(self) -> None:
        schema = copy.deepcopy(schema_sync.load_schema())
        schema["properties"]["source_reference"]["pattern"] = "^https://.+$"
        errors = schema_sync.check_sync(schema, validate)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("source_reference pattern", errors[0])

    def test_schema_sync_detects_a_missing_pattern(self) -> None:
        schema = copy.deepcopy(schema_sync.load_schema())
        del schema["properties"]["source_reference"]["pattern"]
        errors = schema_sync.check_sync(schema, validate)
        self.assertEqual(len(errors), 1, errors)


if __name__ == "__main__":
    unittest.main()

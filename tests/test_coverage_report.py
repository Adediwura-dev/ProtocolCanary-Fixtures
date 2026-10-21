"""Tests for tools/coverage/report.py (the fixture coverage report, #354).

Run with: python3 -m unittest discover tests

The synthetic packs below are built entirely in a temporary directory and
every expected output -- the report dict, the text table, the JSON
document -- is written out in this file, not read back from the tool.
Nothing here edits a pack or any repository file.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COVERAGE_PATH = REPO_ROOT / "tools" / "coverage" / "report.py"
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"

_spec = importlib.util.spec_from_file_location("coverage_report", COVERAGE_PATH)
coverage_report = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["coverage_report"] = coverage_report
_spec.loader.exec_module(coverage_report)

_vspec = importlib.util.spec_from_file_location("validate", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_vspec)
assert _vspec.loader is not None
sys.modules["validate"] = validate
_vspec.loader.exec_module(validate)


# --------------------------------------------------------------------------
# Fixture builders: complete, validator-valid fixture files as plain text.
# --------------------------------------------------------------------------


def write(root: Path, name: str, contents: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    raise TypeError(f"unsupported TOML value {value!r}")


def xdr_fixture(
    fid: str,
    *,
    type_: str,
    kind: str,
    category: str,
    protocol: int = 28,
    capabilities: list[str] | None = None,
) -> str:
    lines = [
        "# Synthetic structural fixture built for a unit test.",
        f'id = "{fid}"',
        f"protocol = {protocol}",
        'surface = "xdr"',
        f'category = "{category}"',
        f'description = "{fid}"',
        'source_reference = "CAP-0083"',
    ]
    if capabilities:
        lines.append(
            "required_capabilities = ["
            + ", ".join(_toml_value(c) for c in capabilities)
            + "]"
        )
    lines += [
        f'type = "{type_}"',
        f'kind = "{kind}"',
        'value_base64 = "AAAAAA=="',
    ]
    return "\n".join(lines) + "\n"


def rpc_fixture(
    fid: str,
    *,
    method: str,
    asserts: list[dict[str, object]],
    protocol: int = 28,
    category: str = "network",
) -> str:
    lines = [
        "# Synthetic structural fixture built for a unit test.",
        f'id = "{fid}"',
        f"protocol = {protocol}",
        'surface = "rpc"',
        f'category = "{category}"',
        f'description = "{fid}"',
        'source_reference = "CAP-0083"',
        f'method = "{method}"',
    ]
    for table in asserts:
        lines.append("")
        lines.append("[[assert]]")
        for key in sorted(table):
            lines.append(f"{key} = {_toml_value(table[key])}")
    return "\n".join(lines) + "\n"


def soroban_fixture(
    fid: str,
    *,
    expect_kind: str,
    message_contains: str | None = None,
    protocol: int = 28,
    capabilities: list[str] | None = None,
    category: str = "network",
) -> str:
    lines = [
        "# Synthetic structural fixture built for a unit test.",
        f'id = "{fid}"',
        f"protocol = {protocol}",
        'surface = "soroban"',
        f'category = "{category}"',
        f'description = "{fid}"',
        'source_reference = "CAP-0083"',
        'source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"',
        'contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"',
        'function = "name"',
        "sequence_number = 1",
    ]
    if capabilities:
        lines.append(
            "required_capabilities = ["
            + ", ".join(_toml_value(c) for c in capabilities)
            + "]"
        )
    lines += ["", "[expect]", f'kind = "{expect_kind}"']
    if message_contains is not None:
        lines.append(f"message_contains = {_toml_value(message_contains)}")
    return "\n".join(lines) + "\n"


def build_example_pack(root: Path) -> Path:
    """A pack exercising every accepted value that appears anywhere.

    3 xdr fixtures (2 ContractExecutable, 1 StellarValue), 2 rpc fixtures
    (one per method, four distinct assertion kinds), 2 soroban fixtures
    (both expectation kinds): 7 fixtures, 2 CAP categories, 2 used
    required-capabilities.
    """
    pack = root / "protocol-99"
    write(
        pack,
        "xdr/p99-xdr-alpha-roundtrip.toml",
        xdr_fixture(
            "p99-xdr-alpha-roundtrip",
            type_="StellarValue",
            kind="roundtrip",
            category="cap-0083",
            capabilities=["stellar-sdk-dependency"],
        ),
    )
    write(
        pack,
        "xdr/p99-xdr-beta-decode-success.toml",
        xdr_fixture(
            "p99-xdr-beta-decode-success",
            type_="ContractExecutable",
            kind="decode-success",
            category="cap-0085",
        ),
    )
    write(
        pack,
        "xdr/p99-xdr-gamma-decode-failure.toml",
        xdr_fixture(
            "p99-xdr-gamma-decode-failure",
            type_="ContractExecutable",
            kind="decode-failure",
            category="contract-executable",
        ),
    )
    write(
        pack,
        "rpc/p99-rpc-network.toml",
        rpc_fixture(
            "p99-rpc-network",
            method="get-network",
            asserts=[
                {"kind": "field-equals", "field": "passphrase", "value": "Test"},
                {"kind": "field-type", "field": "sequence", "expected_type": "number"},
            ],
        ),
    )
    write(
        pack,
        "rpc/p99-rpc-latest-ledger.toml",
        rpc_fixture(
            "p99-rpc-latest-ledger",
            method="get-latest-ledger",
            asserts=[
                {"kind": "field-exists", "field": "id"},
                {"kind": "field-absent", "field": "passphrase"},
            ],
        ),
    )
    write(
        pack,
        "soroban/p99-soroban-success.toml",
        soroban_fixture(
            "p99-soroban-success",
            expect_kind="simulation-success",
            capabilities=["soroban-contract"],
        ),
    )
    write(
        pack,
        "soroban/p99-soroban-error.toml",
        soroban_fixture(
            "p99-soroban-error",
            expect_kind="simulation-error",
            message_contains="not found",
        ),
    )
    return pack


# The expected report for build_example_pack(), written out by hand: every
# key is either a capability constant's accepted value (from validate.py's
# sets) or a fixture written above, and every number is counted from the
# fixture list above -- never copied from the tool's output.
EXPECTED_EXAMPLE_REPORT = {
    "capabilities": {
        "CAPABILITIES": {
            "rpc-client": 0,
            "raw-ledger-access": 0,
            "soroban-contract": 1,
            "stellar-sdk-dependency": 1,
            "wasm-artifact": 0,
        },
        "RPC_ASSERT_KINDS": {
            "field-exists": 1,
            "field-absent": 1,
            "field-equals": 1,
            "field-number-range": 0,
            "field-type": 1,
        },
        "RPC_ASSERT_TYPES": {
            "array": 0,
            "bool": 0,
            "null": 0,
            "number": 1,
            "object": 0,
            "string": 0,
        },
        "RPC_METHODS": {"get-network": 1, "get-latest-ledger": 1},
        "SOROBAN_ARG_KINDS": {
            "address": 0,
            "bool": 0,
            "bytes": 0,
            "duration": 0,
            "i128": 0,
            "i256": 0,
            "i32": 0,
            "i64": 0,
            "map": 0,
            "string": 0,
            "symbol": 0,
            "timepoint": 0,
            "u128": 0,
            "u256": 0,
            "u32": 0,
            "u64": 0,
            "vec": 0,
            "void": 0,
        },
        "SOROBAN_EXPECT_KINDS": {"simulation-error": 1, "simulation-success": 1},
        "SURFACES": {"rpc": 2, "soroban": 2, "xdr": 3},
        "XDR_KINDS": {
            "decode-success": 1,
            "decode-failure": 1,
            "roundtrip": 1,
            "encode-equals": 0,
        },
        "XDR_TYPES": {
            "ContractExecutable": 2,
            "LedgerEntry": 0,
            "ScVal": 0,
            "StellarValue": 1,
            "TransactionEnvelope": 0,
        },
    },
    "caps": {"cap-0083": 1, "cap-0085": 1},
    "invalid": {"count": 0, "files": []},
    "packs": [{"name": "protocol-99", "files": 7, "valid": 7, "invalid": 0}],
    "totals": {"files": 7, "valid": 7, "invalid": 0},
    "unmapped_capabilities": [],
}

# The same expectation rendered as the text table (golden output; the
# column alignment is asserted independently in
# test_text_columns_are_aligned_within_each_section).
EXPECTED_EXAMPLE_TEXT = """\
Fixture coverage report: 7 fixture file(s), 7 valid, 0 invalid

Packs
  protocol-99    7 file(s),   7 valid,   0 invalid

CAPABILITIES
  raw-ledger-access         0  no fixture
  rpc-client                0  no fixture
  soroban-contract          1
  stellar-sdk-dependency    1
  wasm-artifact             0  no fixture

RPC_ASSERT_KINDS
  field-absent          1
  field-equals          1
  field-exists          1
  field-number-range    0  no fixture
  field-type            1

RPC_ASSERT_TYPES
  array     0  no fixture
  bool      0  no fixture
  null      0  no fixture
  number    1
  object    0  no fixture
  string    0  no fixture

RPC_METHODS
  get-latest-ledger    1
  get-network          1

SOROBAN_ARG_KINDS
  address      0  no fixture
  bool         0  no fixture
  bytes        0  no fixture
  duration     0  no fixture
  i128         0  no fixture
  i256         0  no fixture
  i32          0  no fixture
  i64          0  no fixture
  map          0  no fixture
  string       0  no fixture
  symbol       0  no fixture
  timepoint    0  no fixture
  u128         0  no fixture
  u256         0  no fixture
  u32          0  no fixture
  u64          0  no fixture
  vec          0  no fixture
  void         0  no fixture

SOROBAN_EXPECT_KINDS
  simulation-error      1
  simulation-success    1

SURFACES
  rpc        2
  soroban    2
  xdr        3

XDR_KINDS
  decode-failure    1
  decode-success    1
  encode-equals     0  no fixture
  roundtrip         1

XDR_TYPES
  ContractExecutable     2
  LedgerEntry            0  no fixture
  ScVal                  0  no fixture
  StellarValue           1
  TransactionEnvelope    0  no fixture

CAP categories
  cap-0083    1
  cap-0085    1

Invalid fixtures
  none
"""


def run_main(argv: list[str]) -> tuple[int, str, str]:
    """Run coverage_report.main capturing (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = coverage_report.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExamplePackTests(unittest.TestCase):
    """The report over a hand-built pack matches the expectation written above."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pack = build_example_pack(Path(cls._tmp.name))
        cls.report = coverage_report.build_report([cls.pack])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_build_report_matches_the_written_expectation(self) -> None:
        self.assertEqual(self.report, EXPECTED_EXAMPLE_REPORT)

    def test_every_capability_key_set_is_sorted(self) -> None:
        for name, counts in self.report["capabilities"].items():
            self.assertEqual(list(counts), sorted(counts), name)
        self.assertEqual(list(self.report["caps"]), sorted(self.report["caps"]))
        self.assertEqual(list(self.report), sorted(self.report))

    def test_render_text_matches_the_golden_output(self) -> None:
        self.assertEqual(coverage_report.render_text(self.report), EXPECTED_EXAMPLE_TEXT)

    def test_text_columns_are_aligned_within_each_section(self) -> None:
        # The golden above must not drift into ragged columns: within one
        # section, every count starts at the same column.
        sections: dict[str, list[int]] = {}
        current: str | None = None
        for line in coverage_report.render_text(self.report).splitlines():
            if line and not line.startswith(" "):
                current = line
                sections.setdefault(current, [])
                continue
            match = re.match(r"  (\S+)(\s+)(\d+)(  no fixture)?$", line)
            if match is not None and current is not None:
                sections[current].append(match.start(3))
        for title, positions in sections.items():
            if len(positions) > 1:
                self.assertEqual(len(set(positions)), 1, f"{title}: {positions}")

    def test_render_json_is_one_document_equal_to_the_report(self) -> None:
        rendered = coverage_report.render_json(self.report)
        self.assertEqual(json.loads(rendered), EXPECTED_EXAMPLE_REPORT)
        self.assertTrue(rendered.endswith("\n"))
        # sort_keys: the document's key order is part of the format.
        self.assertEqual(
            json.dumps(json.loads(rendered), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            rendered,
        )

    def test_zero_counts_are_marked_no_fixture(self) -> None:
        text = coverage_report.render_text(self.report)
        self.assertIn("  ScVal                  0  no fixture\n", text)
        self.assertIn("  encode-equals     0  no fixture\n", text)
        self.assertIn("  string    0  no fixture\n", text)
        # A used capability carries no marker.
        self.assertIn("  xdr        3\n", text)

    def test_output_is_deterministic_and_has_no_absolute_paths(self) -> None:
        first = coverage_report.render_text(self.report)
        second = coverage_report.render_text(coverage_report.build_report([self.pack]))
        self.assertEqual(first, second)
        self.assertNotIn(self._tmp.name, first)
        self.assertNotIn(str(REPO_ROOT), first)
        as_json = coverage_report.render_json(self.report)
        self.assertNotIn(self._tmp.name, as_json)
        self.assertNotIn(str(REPO_ROOT), as_json)

    def test_cli_text_and_json_run_over_the_pack(self) -> None:
        code, out, err = run_main([str(self.pack)])
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_EXAMPLE_TEXT)

        code, out, err = run_main(["--format", "json", str(self.pack)])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), EXPECTED_EXAMPLE_REPORT)


class ReadOnlyTests(unittest.TestCase):
    def test_the_tool_never_modifies_a_pack_or_any_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = build_example_pack(Path(tmp))

            def digest() -> dict[str, str]:
                return {
                    str(p.relative_to(pack)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(pack.rglob("*"))
                    if p.is_file()
                }

            before = digest()
            coverage_report.build_report([pack])
            coverage_report.render_text(coverage_report.build_report([pack]))
            coverage_report.render_json(coverage_report.build_report([pack]))
            self.assertEqual(digest(), before)
            # ... and no new files appeared (no __pycache__, no manifest).
            self.assertEqual(
                sorted(str(p.relative_to(pack)) for p in pack.rglob("*") if p.is_file()),
                sorted(before),
            )


class EmptyPackTests(unittest.TestCase):
    def test_empty_pack_reports_every_capability_with_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "protocol-99"
            pack.mkdir()
            report = coverage_report.build_report([pack])
            text = coverage_report.render_text(report)
            # Rebuilding over the same empty pack is byte-identical.
            self.assertEqual(
                coverage_report.render_text(coverage_report.build_report([pack])), text
            )

        self.assertEqual(report["totals"], {"files": 0, "valid": 0, "invalid": 0})
        self.assertEqual(report["packs"], [{"name": "protocol-99", "files": 0, "valid": 0, "invalid": 0}])
        self.assertEqual(report["caps"], {})
        self.assertEqual(report["invalid"], {"count": 0, "files": []})
        for name, values in report["capabilities"].items():
            self.assertTrue(values, name)
            self.assertEqual(set(values.values()), {0}, name)

        # One marker per zero capability value, plus the empty CAP section.
        self.assertEqual(
            text.count("no fixture"),
            sum(len(v) for v in report["capabilities"].values()) + 1,
        )


class InvalidFixtureTests(unittest.TestCase):
    def test_a_rejected_fixture_is_counted_as_invalid_not_crashed_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "protocol-99"
            write(
                pack,
                "xdr/p99-xdr-good.toml",
                xdr_fixture(
                    "p99-xdr-good",
                    type_="StellarValue",
                    kind="roundtrip",
                    category="cap-0083",
                ),
            )
            # Unknown surface: the validator rejects it, the report counts it.
            write(
                pack,
                "xdr/p99-xdr-bogus-surface.toml",
                xdr_fixture(
                    "p99-xdr-bogus-surface",
                    type_="StellarValue",
                    kind="roundtrip",
                    category="stellar-value",
                ).replace('surface = "xdr"', 'surface = "telepathy"'),
            )
            report = coverage_report.build_report([pack])
            label = pack.name

        self.assertEqual(report["totals"], {"files": 2, "valid": 1, "invalid": 1})
        self.assertEqual(
            report["invalid"],
            {"count": 1, "files": [f"{label}/xdr/p99-xdr-bogus-surface.toml"]},
        )
        # Only the accepted fixture contributes to the counts.
        self.assertEqual(report["capabilities"]["SURFACES"], {"rpc": 0, "soroban": 0, "xdr": 1})
        self.assertEqual(report["caps"], {"cap-0083": 1})

    def test_an_unparseable_fixture_is_counted_as_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "protocol-99"
            write(pack, "xdr/broken.toml", "id = 'unterminated\n")
            report = coverage_report.build_report([pack])
        self.assertEqual(report["totals"], {"files": 1, "valid": 0, "invalid": 1})
        self.assertEqual(report["invalid"]["count"], 1)

    def test_a_pack_mixing_protocols_is_counted_not_split_or_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "protocol-99"
            write(
                pack,
                "xdr/a.toml",
                xdr_fixture("p28-xdr-a", type_="StellarValue", kind="roundtrip", category="stellar-value"),
            )
            write(
                pack,
                "xdr/b.toml",
                xdr_fixture(
                    "p29-xdr-b",
                    type_="ContractExecutable",
                    kind="decode-success",
                    category="contract-executable",
                    protocol=29,
                ),
            )
            report = coverage_report.build_report([pack])
        self.assertEqual(report["totals"], {"files": 2, "valid": 2, "invalid": 0})
        self.assertEqual(report["capabilities"]["SURFACES"], {"rpc": 0, "soroban": 0, "xdr": 2})
        self.assertEqual(report["capabilities"]["XDR_TYPES"]["ContractExecutable"], 1)

    def test_cli_exits_zero_even_when_a_fixture_is_rejected(self) -> None:
        # The report's job is to count; failing on invalid fixtures is the
        # validator's job (this tool reports them under `invalid`).
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "protocol-99"
            write(pack, "xdr/bad.toml", 'id = "x"\n')
            code, out, err = run_main([str(pack)])
        self.assertEqual(code, 0, err)
        self.assertIn("1 invalid", out)


class MissingDirectoryTests(unittest.TestCase):
    def test_build_report_raises_for_a_path_that_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-pack"
            with self.assertRaises(coverage_report.CoverageError) as ctx:
                coverage_report.build_report([missing])
        self.assertIn(str(missing), str(ctx.exception))
        self.assertIn("not a directory", str(ctx.exception))

    def test_cli_exits_non_zero_with_a_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "no-such-pack")
            code, out, err = run_main([missing])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error:", err)
        self.assertIn("no-such-pack", err)
        self.assertIn("not a directory", err)

    def test_cli_exits_non_zero_for_a_missing_validator_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = run_main(["--validator", str(Path(tmp) / "nope.py"), tmp])
        self.assertEqual(code, 1)
        self.assertIn("no such validator file", err)


class ValidatorConstantTests(unittest.TestCase):
    """A constant added to the validator shows up here without a change here."""

    @staticmethod
    def scratch_validator(tmp: Path) -> Path:
        """A copy of validate.py with one more method and one more constant."""
        source = VALIDATE_PATH.read_text(encoding="utf-8")
        original = 'RPC_METHODS = {"get-network", "get-latest-ledger"}'
        assert original in source, "validator's RPC_METHODS line moved"
        patched = source.replace(
            original,
            'RPC_METHODS = {"get-network", "get-latest-ledger", "get-ledgers"}',
        )
        patched += (
            "\n# A new capability constant added by a scratch copy of the validator.\n"
            'BRAND_NEW_CAPABILITY = {"shiny", "newer"}\n'
        )
        scratch = tmp / "validate_scratch.py"
        scratch.write_text(patched, encoding="utf-8")
        return scratch

    @staticmethod
    def load(path: Path, name: str) -> object:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_a_new_method_and_a_new_constant_appear_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack = build_example_pack(tmp_path)
            scratch = self.scratch_validator(tmp_path)
            module = self.load(scratch, "validate_scratch_for_test")
            report = coverage_report.build_report([pack], validator=module)

        self.assertEqual(report["capabilities"]["RPC_METHODS"]["get-ledgers"], 0)
        self.assertIn("get-ledgers", report["capabilities"]["RPC_METHODS"])
        # A brand-new set has no counting rule, so its zeros are structural
        # and it says so rather than pretending to have been measured.
        self.assertEqual(report["capabilities"]["BRAND_NEW_CAPABILITY"], {"newer": 0, "shiny": 0})
        self.assertEqual(report["unmapped_capabilities"], ["BRAND_NEW_CAPABILITY"])
        # The unmodified report does not mention either.
        self.assertNotIn("get-ledgers", EXPECTED_EXAMPLE_REPORT["capabilities"]["RPC_METHODS"])
        self.assertNotIn("BRAND_NEW_CAPABILITY", EXPECTED_EXAMPLE_REPORT["capabilities"])

    def test_cli_loads_a_scratch_validator_via_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack = build_example_pack(tmp_path)
            scratch = self.scratch_validator(tmp_path)
            code, out, err = run_main(
                ["--format", "json", "--validator", str(scratch), str(pack)]
            )
        self.assertEqual(code, 0, err)
        document = json.loads(out)
        self.assertEqual(document["capabilities"]["RPC_METHODS"]["get-ledgers"], 0)
        self.assertIn("BRAND_NEW_CAPABILITY", document["capabilities"])

    def test_every_capability_constant_of_the_validator_is_in_the_report(self) -> None:
        # Scan the validator for capability constants by shape: module-level
        # ALL_CAPS sets of strings. Anything shaped like a capability that is
        # missing from the report (or from this test's exclusion rule) fails
        # here, so a new constant cannot be missed by both.
        report = coverage_report.build_report([REPO_ROOT / "protocol-28"])
        sections = report["capabilities"]
        found: set[str] = set()
        for name in sorted(dir(validate)):
            if not name.isupper() or name.startswith("_"):
                continue
            value = getattr(validate, name)
            if isinstance(value, (set, frozenset)) and all(
                isinstance(v, str) for v in value
            ):
                found.add(name)
                if name in coverage_report.IGNORED_CONSTANTS:
                    continue
                self.assertIn(name, sections, f"capability constant {name} is not reported")
                self.assertEqual(set(sections[name]), set(value), name)
                self.assertEqual(
                    list(sections[name]), sorted(value), f"{name} is not sorted"
                )
        # The vocabulary this issue names must be among those found, so a
        # future refactor of the scan cannot silently find nothing.
        self.assertLessEqual(
            {
                "SURFACES",
                "XDR_TYPES",
                "XDR_KINDS",
                "RPC_METHODS",
                "RPC_ASSERT_KINDS",
                "RPC_ASSERT_TYPES",
                "SOROBAN_EXPECT_KINDS",
                "CAPABILITIES",
            },
            found,
        )
        self.assertEqual(found & coverage_report.IGNORED_CONSTANTS, {"VAGUE_CATEGORIES"})


class RealProtocolPackTests(unittest.TestCase):
    """The real protocol-28 pack, counted independently of the tool."""

    @staticmethod
    def independent_counts(pack: Path) -> dict[str, dict[str, int]]:
        """Counts straight from glob + tomllib, no validator, no report tool."""
        def bucket() -> dict[str, int]:
            return {}

        def bump(counts: dict[str, int], key: str) -> None:
            counts[key] = counts.get(key, 0) + 1

        counts: dict[str, dict[str, int]] = {
            "SURFACES": bucket(),
            "XDR_TYPES": bucket(),
            "XDR_KINDS": bucket(),
            "RPC_METHODS": bucket(),
            "RPC_ASSERT_KINDS": bucket(),
            "RPC_ASSERT_TYPES": bucket(),
            "SOROBAN_EXPECT_KINDS": bucket(),
            "CAPABILITIES": bucket(),
        }
        caps: dict[str, int] = {}
        files = sorted(pack.rglob("*.toml"))
        for path in files:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            bump(counts["SURFACES"], data["surface"])
            if data["surface"] == "xdr":
                bump(counts["XDR_TYPES"], data["type"])
                bump(counts["XDR_KINDS"], data["kind"])
            elif data["surface"] == "rpc":
                bump(counts["RPC_METHODS"], data["method"])
                for assertion in data["assert"]:
                    bump(counts["RPC_ASSERT_KINDS"], assertion["kind"])
                    if "expected_type" in assertion:
                        bump(counts["RPC_ASSERT_TYPES"], assertion["expected_type"])
            elif data["surface"] == "soroban":
                bump(counts["SOROBAN_EXPECT_KINDS"], data["expect"]["kind"])
            for capability in data.get("required_capabilities", []):
                bump(counts["CAPABILITIES"], capability)
            category = data["category"]
            if re.fullmatch(r"cap-\d{4}", category, re.IGNORECASE):
                bump(caps, category.lower())
        # Every accepted value must be present with its count, zeros
        # included: the key universe comes from the validator's constants.
        universe = {
            "SURFACES": validate.SURFACES,
            "XDR_TYPES": validate.XDR_TYPES,
            "XDR_KINDS": validate.XDR_KINDS,
            "RPC_METHODS": validate.RPC_METHODS,
            "RPC_ASSERT_KINDS": validate.RPC_ASSERT_KINDS,
            "RPC_ASSERT_TYPES": validate.RPC_ASSERT_TYPES,
            "SOROBAN_EXPECT_KINDS": validate.SOROBAN_EXPECT_KINDS,
            "CAPABILITIES": validate.CAPABILITIES,
        }
        for name, accepted in universe.items():
            counts[name] = {value: counts[name].get(value, 0) for value in sorted(accepted)}
        counts["__caps__"] = dict(sorted(caps.items()))
        counts["__files__"] = {"files": len(files)}
        return counts

    def test_counts_equal_an_independent_glob_and_parse(self) -> None:
        pack = REPO_ROOT / "protocol-28"
        report = coverage_report.build_report([pack])
        expected = self.independent_counts(pack)

        for name in (
            "SURFACES",
            "XDR_TYPES",
            "XDR_KINDS",
            "RPC_METHODS",
            "RPC_ASSERT_KINDS",
            "RPC_ASSERT_TYPES",
            "SOROBAN_EXPECT_KINDS",
            "CAPABILITIES",
        ):
            self.assertEqual(report["capabilities"][name], expected[name], name)
        self.assertEqual(report["caps"], expected["__caps__"])
        self.assertEqual(report["totals"]["files"], expected["__files__"]["files"])
        self.assertEqual(report["totals"]["invalid"], 0)
        self.assertEqual(report["totals"]["valid"], report["totals"]["files"])

    def test_report_over_the_real_pack_is_deterministic_and_path_free(self) -> None:
        pack = REPO_ROOT / "protocol-28"
        first = coverage_report.render_text(coverage_report.build_report([pack]))
        second = coverage_report.render_text(coverage_report.build_report([pack]))
        self.assertEqual(first, second)
        self.assertNotIn(str(REPO_ROOT), first)
        self.assertIn("protocol-28", first)

    def test_cli_defaults_to_the_repository_packs(self) -> None:
        code, out, err = run_main([])
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertIn("protocol-28", out)
        self.assertIn("Fixture coverage report:", out)


if __name__ == "__main__":
    unittest.main()

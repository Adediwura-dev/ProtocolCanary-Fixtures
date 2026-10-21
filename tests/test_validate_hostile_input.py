"""Hostile-input tests for ``tools/validate/validate.py`` (issue #341).

Every hostile fixture below is generated inside the test in a temporary
directory and never committed. Each case is run in both output formats and
asserts exit code, stdout, stderr and that the string ``Traceback`` never
appears: the three crash rows (invalid UTF-8, extreme nesting, an integer
literal beyond Python's conversion limit) and the silent-acceptance row (an
integer outside the signed 64-bit range the engine reads) must all be
ordinary validator errors, matching the fixture load errors the engine
reports (docs/fixture-contract.md section 3 in Protocol-Canary:
``FixtureError::Parse``/``Read``).

The optional engine cross-check at the bottom runs only when the
environment variable ``STELLAR_CANARY_BIN`` names an engine binary and is
skipped otherwise.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"

_spec = importlib.util.spec_from_file_location("validate_hostile_target", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate_hostile_target"] = validate
_spec.loader.exec_module(validate)

STELLAR_CANARY_BIN = os.environ.get("STELLAR_CANARY_BIN")

# A minimal fixture that validates cleanly, so every error in a test comes
# from the hostile change applied to it.
VALID_XDR = """\
id = "p28-xdr-hostile-base"
protocol = 28
surface = "xdr"
category = "cap-0083"
description = "example"
source_reference = "CAP-0083"

type = "StellarValue"
kind = "decode-success"
value_base64 = "AAAAAA=="
"""

# A minimal Soroban fixture with a ``sequence_number`` placeholder; the
# account and contract strkeys are the all-zero (well-known) addresses.
VALID_SOROBAN = """\
id = "p28-soroban-hostile-base"
protocol = 28
surface = "soroban"
category = "smoke"
description = "example"
source_reference = "https://developers.stellar.org/docs/tokens/stellar-asset-contract"

source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
function = "name"
sequence_number = SEQUENCE_NUMBER

[expect]
kind = "simulation-success"
"""

INT64_MAX_DECIMAL = "9223372036854775807"
INT64_MIN_DECIMAL = "-9223372036854775808"


def soroban_fixture(sequence_number: str) -> bytes:
    """The valid Soroban fixture with ``sequence_number`` substituted."""
    return VALID_SOROBAN.replace("SEQUENCE_NUMBER", sequence_number).encode("utf-8")


def deep_nesting_line(depth: int = 100_000) -> bytes:
    """A ``nest`` line with ``depth`` nested array brackets."""
    return b"\nnest = " + b"[" * depth + b"]" * depth + b"\n"


def oversized_integer_line(digits: int = 5_000) -> bytes:
    """A ``big`` line holding a decimal integer with ``digits`` digits."""
    return b"\nbig = " + b"5" * digits + b"\n"


def write_fixtures(root: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def run_validator(root: Path, *args: str) -> tuple[int, str, str]:
    """Invoke ``validate.main`` the way CI does; return (exit, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = validate.main([*args, str(root)])
    return exit_code, stdout.getvalue(), stderr.getvalue()


def max_parseable_nesting() -> int:
    """The deepest array nesting ``tomllib.loads`` still parses here.

    Found by doubling and binary search, so the "just under the limit" test
    stays correct when the interpreter's recursion limit changes.
    """

    def parses(depth: int) -> bool:
        try:
            tomllib.loads("nest = " + "[" * depth + "]" * depth + "\n")
        except RecursionError:
            return False
        return True

    lo, hi = 1, 2
    while parses(hi) and hi < 100_000:
        lo, hi = hi, hi * 2
    if not parses(lo):
        return 0
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if parses(mid):
            lo = mid
        else:
            hi = mid
    return lo


class HostileInputTests(unittest.TestCase):
    def assert_hostile(
        self, files: dict[str, bytes], needle: str, hostile_name: str = "x.toml"
    ) -> None:
        """The validator reports ``needle`` in both formats: exit 1, no traceback.

        JSON mode must print exactly one valid JSON document on stdout
        (``json.loads`` over the whole stdout enforces "exactly one") and
        nothing on stderr.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixtures(root, files)
            for args in ((), ("--format", "json")):
                with self.subTest(args=args):
                    code, out, err = run_validator(root, *args)
                    self.assertEqual(code, 1)
                    self.assertNotIn("Traceback", out + err)
                    if args:  # --format json
                        self.assertEqual(err, "")
                        document = json.loads(out)
                        self.assertFalse(document["valid"])
                        messages = [e["message"] for e in document["errors"]]
                        self.assertTrue(
                            any(needle in message for message in messages), messages
                        )
                        self.assertTrue(
                            any(hostile_name in e["path"] for e in document["errors"]),
                            document["errors"],
                        )
                    else:  # text
                        self.assertIn(needle, err)
                        self.assertIn(hostile_name, err)

    # ------------------------------------------- the three crash rows

    def test_invalid_utf8_is_reported_not_crashed(self) -> None:
        """Row 1: invalid UTF-8 bytes are an error naming the file, not a traceback."""
        content = VALID_XDR.encode("utf-8") + b"\n# \xff\xfe\n"
        self.assert_hostile({"x.toml": content}, "file is not valid UTF-8 (byte 0xff")

    def test_lone_continuation_byte_is_reported(self) -> None:
        """An unpaired high-bit byte (here a lone continuation byte) is reported."""
        content = VALID_XDR.encode("utf-8") + b"\x80\n"
        self.assert_hostile({"x.toml": content}, "file is not valid UTF-8 (byte 0x80")

    def test_truncated_multibyte_sequence_at_eof_is_reported(self) -> None:
        """A file ending inside a multi-byte sequence is reported, not crashed on."""
        content = VALID_XDR.encode("utf-8") + b"# caf\xc3"
        self.assert_hostile({"x.toml": content}, "file is not valid UTF-8")

    def test_deep_nesting_is_reported_not_crashed(self) -> None:
        """Row 2: parser recursion overflow is an error, not a traceback."""
        content = VALID_XDR.encode("utf-8") + deep_nesting_line()
        self.assert_hostile({"x.toml": content}, "nesting is too deep")

    def test_oversized_integer_literal_is_reported_not_crashed(self) -> None:
        """Row 3: an integer literal beyond Python's digit limit is an error."""
        if sys.get_int_max_str_digits() == 0:
            self.skipTest("this interpreter has no integer conversion limit")
        content = VALID_XDR.encode("utf-8") + oversized_integer_line()
        self.assert_hostile({"x.toml": content}, "integer literal is too large")

    def test_nesting_just_under_the_recursion_limit_does_not_crash(self) -> None:
        """A nesting depth that still parses must be handled without crashing.

        Whether the run reports "nesting is too deep" (the validator's own
        call stack is deeper than a bare tomllib.loads) or "unexpected
        top-level key 'nest'" (the parse succeeded), it exits 1 with a
        normal error and no traceback.
        """
        depth = max_parseable_nesting()
        if depth < 2:
            self.skipTest("could not determine a parseable nesting depth")
        for candidate in (depth, depth - 1):
            with self.subTest(depth=candidate):
                content = VALID_XDR.encode("utf-8") + deep_nesting_line(candidate)
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    write_fixtures(root, {"x.toml": content})
                    code, out, err = run_validator(root)
                    self.assertEqual(code, 1)
                    self.assertNotIn("Traceback", out + err)
                    self.assertTrue(
                        "nesting is too deep" in err
                        or "unexpected top-level key 'nest'" in err,
                        err,
                    )

    # --------------------------------- the signed 64-bit range (row 4)

    def test_int64_boundary_values_are_accepted(self) -> None:
        """2^63-1 and -2^63 are the accepted edges of the signed 64-bit range."""
        for sequence_number in (INT64_MAX_DECIMAL, INT64_MIN_DECIMAL):
            with self.subTest(sequence_number=sequence_number):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    write_fixtures(
                        root, {"x.toml": soroban_fixture(sequence_number)}
                    )
                    code, out, err = run_validator(root)
                    self.assertEqual(code, 0, err)
                    self.assertIn("OK", out)
                    self.assertNotIn("Traceback", out + err)

    def test_values_just_outside_the_int64_range_are_rejected(self) -> None:
        """Row 4: 2^63 and -2^63-1 are errors, not silent acceptances."""
        for sequence_number, magnitude in (
            ("9223372036854775808", "9223372036854775808"),
            ("-9223372036854775809", "9223372036854775809"),
        ):
            with self.subTest(sequence_number=sequence_number):
                self.assert_hostile(
                    {"x.toml": soroban_fixture(sequence_number)},
                    "integer at 'sequence_number' is outside the signed 64-bit range",
                )
                # The magnitude must be named.
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    write_fixtures(
                        root, {"x.toml": soroban_fixture(sequence_number)}
                    )
                    code, out, err = run_validator(root, "--format", "json")
                    self.assertEqual(code, 1)
                    messages = " ".join(
                        e["message"] for e in json.loads(out)["errors"]
                    )
                    self.assertIn(f"magnitude is {magnitude}", messages)

    def test_out_of_range_integer_names_key_path_without_full_digits(self) -> None:
        """A huge but parseable literal is reported by key path and size only.

        4300 digits is the largest decimal literal the default integer
        conversion limit lets tomllib parse; stringifying it again would
        raise inside the error path, so the message must describe the
        magnitude instead of printing the digits.
        """
        digits = min(4300, sys.get_int_max_str_digits() or 4300)
        content = VALID_XDR.encode("utf-8") + oversized_integer_line(digits)
        self.assert_hostile({"x.toml": content}, "integer at 'big' is outside")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixtures(root, {"x.toml": content})
            code, out, err = run_validator(root, "--format", "json")
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", out + err)
            document = json.loads(out)
            message = document["errors"][0]["message"]
            self.assertIn("signed 64-bit range", message)
            self.assertIsNone(re.search(r"\d{25,}", message), message)

    # ------------------------------------------- regressions that must hold

    def test_nul_byte_keeps_its_existing_error(self) -> None:
        """A NUL byte was already reported correctly and must stay reported."""
        content = VALID_XDR.encode("utf-8") + b"\x00\n"
        self.assert_hostile({"x.toml": content}, "U+0000")

    def test_valid_utf8_with_high_bit_text_still_validates(self) -> None:
        """Accented and non-Latin UTF-8 is valid fixture text, not an encoding error."""
        fixture = VALID_XDR.replace(
            'description = "example"', 'description = "café 日本語 naïve"'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixtures(root, {"x.toml": fixture})
            code, out, err = run_validator(root)
            self.assertEqual(code, 0, err)
            self.assertIn("OK", out)

    def test_hostile_file_among_valid_ones_still_reports_the_others(self) -> None:
        """One hostile file must not stop the rest of the run from validating."""
        files = {
            "protocol-28/alpha.toml": VALID_XDR.replace(
                "p28-xdr-hostile-base", "p28-xdr-alpha"
            ).encode("utf-8"),
            "protocol-28/xdr/bad.toml": VALID_XDR.encode("utf-8")
            + deep_nesting_line(),
            "protocol-28/charlie.toml": VALID_XDR.replace(
                "p28-xdr-hostile-base", "p28-xdr-charlie"
            ).encode("utf-8"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixtures(root, files)

            code, out, err = run_validator(root, "--format", "json")
            self.assertEqual(code, 1)
            self.assertEqual(err, "")
            self.assertNotIn("Traceback", out)
            document = json.loads(out)
            self.assertFalse(document["valid"])
            self.assertEqual(document["summary"]["fixtures"], 3)
            error_paths = [e["path"] for e in document["errors"]]
            self.assertTrue(error_paths)
            for path in error_paths:
                self.assertIn("bad.toml", path)
            self.assertFalse(any("alpha.toml" in path for path in error_paths))
            self.assertFalse(any("charlie.toml" in path for path in error_paths))

            code, out, err = run_validator(root)
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", out + err)
            self.assertIn("bad.toml", err)
            self.assertNotIn("alpha.toml", err)
            self.assertNotIn("charlie.toml", err)


@unittest.skipUnless(
    STELLAR_CANARY_BIN,
    "STELLAR_CANARY_BIN is not set; skipping the optional engine cross-check",
)
class EngineCrossCheckTests(unittest.TestCase):
    """Replay the issue's table rows against the real engine.

    The engine refuses each hostile input while loading fixtures, with a
    fixture error (exit code 4) per docs/fixture-contract.md section 3 and
    never a crash; the validator must agree with that instead of dying with
    a traceback or silently accepting the file. No network is needed: the
    engine rejects these inputs before any request.
    """

    def engine_loads(self, content: bytes) -> subprocess.CompletedProcess:
        with (
            tempfile.TemporaryDirectory() as work,
            tempfile.TemporaryDirectory() as pack,
        ):
            write_fixtures(Path(pack), {"protocol-28/x.toml": content})
            (Path(work) / ".stellar-canary.toml").write_text(
                "version = 1\nprotocol = 28\n\n"
                "[tests]\nxdr = true\nrpc = true\nsoroban = true\n",
                encoding="utf-8",
            )
            return subprocess.run(
                [
                    STELLAR_CANARY_BIN,
                    "check",
                    "--fixtures-dir",
                    pack,
                    "--protocol",
                    "28",
                    "--rpc-url",
                    "http://127.0.0.1:1",
                    "--rpc-timeout",
                    "1",
                    "--rpc-max-retries",
                    "0",
                    "--no-cache",
                    "--json",
                ],
                cwd=work,
                capture_output=True,
                text=True,
            )

    def test_hostile_inputs_are_engine_load_errors(self) -> None:
        hostile = {
            "invalid-utf8": VALID_XDR.encode("utf-8") + b"\n# \xff\xfe\n",
            "deep-nesting": VALID_XDR.encode("utf-8") + deep_nesting_line(),
            "oversized-integer": VALID_XDR.encode("utf-8") + oversized_integer_line(),
            "int64-overflow": soroban_fixture("9223372036854775808"),
        }
        for name, content in hostile.items():
            with self.subTest(name=name):
                result = self.engine_loads(content)
                self.assertEqual(
                    result.returncode,
                    4,
                    f"engine expected to reject {name}: "
                    f"rc={result.returncode} stderr={result.stderr!r}",
                )


if __name__ == "__main__":
    unittest.main()

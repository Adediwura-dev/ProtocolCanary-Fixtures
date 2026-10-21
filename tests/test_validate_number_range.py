"""Tests for field-number-range RPC assertion validation and schema (issue #344, F-080).

Validates field-number-range assertions according to the engine's runner rules.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"

_spec = importlib.util.spec_from_file_location("validate", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate"] = validate
_spec.loader.exec_module(validate)


def make_rpc_fixture(assert_block: str) -> str:
    return f"""
id = "test-rpc-number-range"
protocol = 28
surface = "rpc"
category = "smoke"
description = "test rpc fixture"
source_reference = "https://github.com/stellar/stellar-protocol/blob/4db3e802b2929c865a27bd43f2544c979150137d/ecosystem/sep-0041.md"
method = "get-network"

{assert_block}
"""


def validate_fixture_content(content: str) -> validate.Report:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p = root / "fixture.toml"
        p.write_text(content, encoding="utf-8")
        return validate.validate_directory(root)


class TestValidateNumberRange(unittest.TestCase):
    def test_positive_cases(self) -> None:
        positive_blocks = [
            # min only
            """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 1
""",
            # max only
            """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
max = 99
""",
            # both min and max
            """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 1
max = 99
""",
            # equal bounds
            """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 28
max = 28
""",
            # negative bounds
            """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = -10
max = -1
""",
            # float bounds
            """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 1.5
max = 2.5
""",
        ]

        for block in positive_blocks:
            with self.subTest(block=block):
                report = validate_fixture_content(make_rpc_fixture(block))
                self.assertEqual(report.errors, [], f"expected valid fixture for block:\n{block}")

    def test_negative_cases(self) -> None:
        negative_cases = [
            # string bound: min = "1"
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = "1"
""",
                "assert[0].min must be a number",
            ),
            # boolean bound: max = true
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
max = true
""",
                "assert[0].max must be a number",
            ),
            # non-finite bound: min = nan
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = nan
""",
                "assert[0].min must be a finite number",
            ),
            # non-finite bound: max = inf
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
max = inf
""",
                "assert[0].max must be a finite number",
            ),
            # min greater than max: min = 9, max = 1
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 9
max = 1
""",
                "assert[0] min 9 greater than max 1",
            ),
            # block with neither bound
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
""",
                "assert[0] with kind=field-number-range requires 'min' or 'max'",
            ),
            # block with an extra key such as value = 3
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 1
value = 3
""",
                "unexpected key(s) 'value' in assert[0]",
            ),
            # missing field
            (
                """
[[assert]]
kind = "field-number-range"
min = 1
""",
                "assert[0] missing required non-empty 'field'",
            ),
            # empty field
            (
                """
[[assert]]
kind = "field-number-range"
field = ""
min = 1
""",
                "assert[0] missing required non-empty 'field'",
            ),
        ]

        for block, expected_error in negative_cases:
            with self.subTest(expected=expected_error):
                report = validate_fixture_content(make_rpc_fixture(block))
                self.assertTrue(
                    any(expected_error in e for e in report.errors),
                    f"expected error containing {expected_error!r}, got: {report.errors}",
                )


class TestEngineNumberRangeVerification(unittest.TestCase):
    def test_engine_number_range_loading(self) -> None:
        """If STELLAR_CANARY_BIN is set, verify that engine loading verdict agrees (exit 4 or not)."""
        engine_bin = os.environ.get("STELLAR_CANARY_BIN")
        if not engine_bin or not os.path.exists(engine_bin):
            self.skipTest("STELLAR_CANARY_BIN is not set or does not exist")

        test_cases = [
            # (assert_block, should_load_successfully)
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 1
max = 99
""",
                True,
            ),
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = 9
max = 1
""",
                False,
            ),
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
""",
                False,
            ),
            (
                """
[[assert]]
kind = "field-number-range"
field = "protocolVersion"
min = "1"
""",
                False,
            ),
        ]

        for block, should_load in test_cases:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / ".stellar-canary.toml").write_text(
                    "version = 1\nprotocol = 28\n\n[tests]\nxdr = false\nrpc = true\nsoroban = false\n",
                    encoding="utf-8",
                )
                pack = work / "pack"
                fx_dir = pack / "rpc"
                fx_dir.mkdir(parents=True, exist_ok=True)
                (fx_dir / "test.toml").write_text(
                    make_rpc_fixture(block),
                    encoding="utf-8",
                )

                proc = subprocess.run(
                    [
                        engine_bin,
                        "check",
                        "--fixtures-dir",
                        str(pack),
                        "--protocol",
                        "28",
                        "--rpc-url",
                        "http://127.0.0.1:1",
                        "--json",
                        "--no-cache",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=work,
                )
                if should_load:
                    # When loaded, it will either pass or fail at network call (not exit code 4 loading rejection)
                    self.assertNotEqual(
                        proc.returncode, 4, f"engine unexpectedly rejected valid fixture with exit 4: {proc.stderr}"
                    )
                else:
                    # When invalid, loading rejection exit code is 4
                    self.assertEqual(
                        proc.returncode, 4, f"engine expected to reject with exit 4, got {proc.returncode}: {proc.stderr}"
                    )


if __name__ == "__main__":
    unittest.main()

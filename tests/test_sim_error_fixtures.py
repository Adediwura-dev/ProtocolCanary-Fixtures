"""Tests for the protocol-28 Soroban simulation-error fixture suite (issue #302, F-045).

Two jobs, both offline and deterministic -- nothing here contacts the network,
because the network observations were made once, when the fixtures were
authored, and are recorded in each fixture's header comment:

1. Pin the invariants the ``p28-soroban-sim-error-*`` fixtures depend on, so a
   later edit cannot quietly turn one into a vacuous or non-deterministic
   assertion. In particular every ``message_contains`` must name a real
   ``SCErrorType``/``SCErrorCode`` pair from ``Stellar-contract.x``, which is
   what stops an invented or typo'd host error name from being committed.
2. Cover the validator rules that keep a malformed ``simulation-error``
   expectation out of the pack.

A note on scope, because it is a deliberate divergence from issue #302's
wording: that issue asks for a test asserting that "simulation-error fixtures
without error code or message fail validation". They must not. canary-soroban
models the field as ``SimulationError { message_contains: Option<String> }``, so
a ``simulation-error`` fixture with no ``message_contains`` is a complete,
supported assertion meaning "the simulation must fail". Rejecting it here would
make this repository's validator stricter than the loader that actually parses
the fixtures, which CONTRIBUTING.md forbids ("if the two ever disagree,
Protocol-Canary's implementation wins"). ``test_message_contains_is_optional``
pins the real rule so it is not "fixed" into a divergence later; the checks that
do reject a bad ``message_contains`` are the ones that catch a fixture silently
asserting less than it appears to.

Run with: python3 -m unittest tests/test_sim_error_fixtures.py
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"
PACK = REPO_ROOT / "protocol-28"
SOROBAN_DIR = PACK / "soroban"

_spec = importlib.util.spec_from_file_location("validate", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate"] = validate
_spec.loader.exec_module(validate)

# Every fixture in this suite. The IDs are also listed in
# tests/test_pack_protocol_28.py's EXPECTED_IDS_BY_SURFACE, which asserts the
# pack's inventory exactly; this list is the subset that asserts a simulation
# failure.
SIM_ERROR_IDS = (
    "p28-soroban-sim-error-unknown-function",
    "p28-soroban-sim-error-missing-contract",
    "p28-soroban-sim-error-arg-count",
    "p28-soroban-sim-error-arg-type",
)

# SCErrorType and SCErrorCode, transcribed from Stellar-contract.x in
# stellar/stellar-xdr at commit 9c9c145953e80990d6ff1ae3a6a973a0ce6d0694
# (tag v28.0). The host renders an error as `Error(<type>, <code>)` using these
# names with the SCE_/SCEC_ prefix stripped, which is the form the fixtures'
# message_contains values take. SCE_CONTRACT is excluded on purpose: it carries
# a contract-defined uint32 rather than an SCErrorCode, so it cannot be named
# this way, and producing one needs a deployed contract (see
# docs/protocol-28.md's note on the gap).
SC_ERROR_TYPES = (
    "SCE_WASM_VM",
    "SCE_CONTEXT",
    "SCE_STORAGE",
    "SCE_OBJECT",
    "SCE_CRYPTO",
    "SCE_EVENTS",
    "SCE_BUDGET",
    "SCE_VALUE",
    "SCE_AUTH",
)
SC_ERROR_CODES = (
    "SCEC_ARITH_DOMAIN",
    "SCEC_INDEX_BOUNDS",
    "SCEC_INVALID_INPUT",
    "SCEC_MISSING_VALUE",
    "SCEC_EXISTING_VALUE",
    "SCEC_EXCEEDED_LIMIT",
    "SCEC_INVALID_ACTION",
    "SCEC_INTERNAL_ERROR",
    "SCEC_UNEXPECTED_TYPE",
    "SCEC_UNEXPECTED_SIZE",
)

# The two addresses the suite is allowed to invoke: the reserved, network-wide
# Stellar Asset Contract for native XLM, and a strkey-valid contract id that
# deliberately has no instance. Both are justified in the fixtures' headers.
NATIVE_SAC = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
NONEXISTENT_CONTRACT = "CBKPSY6IA5QBMJP5FKMKSSCGQQ6NAIQ4RX3HETMJFJ4VQUKEJTKG2ZSM"
NONEXISTENT_CONTRACT_PREIMAGE = b"protocol-canary-nonexistent-contract"
ZERO_ACCOUNT = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"

ERROR_PATTERN = re.compile(r"^Error\(([A-Za-z]+), ([A-Za-z]+)\)$")


def rendered_error_name(enum_member: str, prefix: str) -> str:
    """Convert an XDR enum member to the name soroban-env-host prints for it.

    The host renders ``SCE_VALUE`` as ``Value`` and ``SCEC_UNEXPECTED_TYPE`` as
    ``UnexpectedType``: the prefix is dropped and each remaining underscore-
    separated word is capitalised. Deriving the expected spelling from the XDR
    text rather than hard-coding it is what keeps
    ``test_each_message_names_a_real_type_and_code_pair`` honest.
    """
    return "".join(part.capitalize() for part in enum_member.removeprefix(prefix).split("_"))


# A minimal valid soroban fixture, used as the base for the validator's
# negative cases below. Mirrors tests/test_validate.py's VALID_SOROBAN.
BASE_FIXTURE = """
id = "p28-soroban-example"
protocol = 28
surface = "soroban"
category = "simulation-error"
description = "example"
source_reference = "https://developers.stellar.org/docs/tokens/stellar-asset-contract"

source_account = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
contract_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
function = "name"
sequence_number = 1

[expect]
kind = "simulation-error"
"""


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def strkey_contract_payload(address: str) -> bytes:
    """Decode a strkey CONTRACT address, verifying version byte and checksum."""
    raw = base64.b32decode(address + "=" * (-len(address) % 8))
    if len(raw) != 35:
        raise AssertionError(f"{address!r} decodes to {len(raw)} bytes, expected 35")
    if raw[0] != 2 << 3:
        raise AssertionError(f"{address!r} version byte {raw[0]} is not CONTRACT (16)")
    if crc16_xmodem(raw[:-2]).to_bytes(2, "little") != raw[-2:]:
        raise AssertionError(f"{address!r} has a bad CRC-16/XMODEM checksum")
    return raw[1:-2]


class SimErrorFixtureTests(unittest.TestCase):
    """The shipped fixtures must be structurally valid and internally honest."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = validate.validate_directory(PACK)
        fixtures = {
            fx.data["id"]: fx
            for fx in (
                validate.load_fixture(p, validate.Report())
                for p in validate.find_fixture_files(SOROBAN_DIR)
            )
            if fx is not None
        }
        cls.fixtures = fixtures
        cls.suite = [fixtures[fixture_id] for fixture_id in SIM_ERROR_IDS]

    def test_pack_is_structurally_valid(self) -> None:
        self.assertEqual(self.report.errors, [])

    def test_every_expected_fixture_is_present(self) -> None:
        self.assertEqual(
            sorted(self.fixtures),
            sorted([*SIM_ERROR_IDS, "p28-soroban-native-asset-name"]),
        )

    def test_each_fixture_asserts_a_simulation_error(self) -> None:
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                self.assertEqual(fx.data["surface"], "soroban")
                self.assertEqual(fx.data["protocol"], 28)
                self.assertEqual(fx.data["expect"]["kind"], "simulation-error")

    def test_each_fixture_pins_a_non_empty_message(self) -> None:
        # Asserting only "it failed" would still pass if the host started
        # failing for an unrelated reason, so every fixture in this suite names
        # the error it expects.
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                needle = fx.data["expect"]["message_contains"]
                self.assertIsInstance(needle, str)
                self.assertTrue(needle)

    def test_each_fixture_starts_with_a_header_comment(self) -> None:
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                first_non_empty = next(
                    (
                        line
                        for line in fx.path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ),
                    "",
                )
                self.assertTrue(first_non_empty.lstrip().startswith("#"))

    def test_each_header_records_when_it_was_observed(self) -> None:
        # CONTRIBUTING.md's "Recording verification dates" rule: a live
        # observation must carry a YYYY-MM-DD (UTC) date so a reader can judge
        # how stale it is.
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                text = fx.path.read_text(encoding="utf-8")
                self.assertRegex(text, r"\b20\d\d-\d\d-\d\d\b")
                self.assertIn("UTC", text)

    def test_fixtures_live_under_the_soroban_subdirectory(self) -> None:
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                self.assertEqual(fx.path.parent, SOROBAN_DIR)


class ErrorCodeTaxonomyTests(unittest.TestCase):
    """message_contains must name real host errors, not plausible-looking ones."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.needles = {
            fixture_id: validate.load_fixture(
                SOROBAN_DIR / f"{fixture_id}.toml", validate.Report()
            ).data["expect"]["message_contains"]
            for fixture_id in SIM_ERROR_IDS
        }

    def test_each_message_names_a_real_type_and_code_pair(self) -> None:
        # This is the check that makes the suite more than a spelling test: an
        # error name that is not in Stellar-contract.x's enums cannot appear
        # here, so an invented `Error(Value, BadThing)` fails.
        known_types = {rendered_error_name(name, "SCE_") for name in SC_ERROR_TYPES}
        known_codes = {rendered_error_name(name, "SCEC_") for name in SC_ERROR_CODES}
        for fixture_id, needle in self.needles.items():
            with self.subTest(fixture=fixture_id):
                match = ERROR_PATTERN.match(needle)
                self.assertIsNotNone(
                    match,
                    f"{needle!r} is not of the form 'Error(<type>, <code>)'",
                )
                error_type, error_code = match.groups()
                self.assertIn(error_type, sorted(known_types))
                self.assertIn(error_code, sorted(known_codes))

    def test_the_suite_covers_distinct_error_codes(self) -> None:
        # Four fixtures asserting the same code would be one fixture with extra
        # steps; distinct codes mean distinct host subsystems are exercised.
        codes = {ERROR_PATTERN.match(n).group(2) for n in self.needles.values()}
        self.assertEqual(len(codes), len(self.needles))

    def test_the_suite_spans_more_than_one_host_subsystem(self) -> None:
        types = {ERROR_PATTERN.match(n).group(1) for n in self.needles.values()}
        self.assertGreaterEqual(
            len(types),
            3,
            f"expected errors from at least three SCErrorType subsystems, got {sorted(types)}",
        )

    def test_no_fixture_claims_a_contract_defined_error(self) -> None:
        # A SCE_CONTRACT error carries a user-defined uint32 and can only come
        # from a deployed contract, which this repository does not ship.
        # Claiming one would be fabricated coverage.
        for fixture_id, needle in self.needles.items():
            with self.subTest(fixture=fixture_id):
                self.assertNotIn("Contract", needle)


class DeterminismTests(unittest.TestCase):
    """The fixtures must not depend on state that changes between runs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.suite = [
            validate.load_fixture(
                SOROBAN_DIR / f"{fixture_id}.toml", validate.Report()
            )
            for fixture_id in SIM_ERROR_IDS
        ]

    def test_only_reserved_or_deliberately_absent_contracts_are_invoked(self) -> None:
        # Anything else would mean the pack depends on a contract this
        # repository deployed, or on a third party keeping theirs alive.
        allowed = {NATIVE_SAC, NONEXISTENT_CONTRACT}
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                self.assertIn(fx.data["contract_id"], allowed)

    def test_source_account_is_the_unfunded_all_zero_account(self) -> None:
        # Simulation does not require a funded account or a real sequence
        # number; using one would make the fixture depend on account state.
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                self.assertEqual(fx.data["source_account"], ZERO_ACCOUNT)
                self.assertEqual(fx.data["sequence_number"], 1)

    def test_nonexistent_contract_id_is_well_formed_and_preimage_derived(self) -> None:
        # The header comment claims this id is a valid CONTRACT strkey of
        # SHA-256("protocol-canary-nonexistent-contract"). Verify the claim:
        # if the address were mistyped the fixture would still "pass", but for
        # the wrong reason and with a header that lies.
        payload = strkey_contract_payload(NONEXISTENT_CONTRACT)
        self.assertEqual(payload, hashlib.sha256(NONEXISTENT_CONTRACT_PREIMAGE).digest())

    def test_no_fixture_asserts_live_varying_state(self) -> None:
        # A simulation error is reported without resource estimates, so nothing
        # here may depend on a ledger sequence, fee or balance.
        forbidden = ("latest_ledger", "sequence", "balance", "min_resource_fee")
        for fx in self.suite:
            with self.subTest(fixture=fx.data["id"]):
                for key in forbidden:
                    self.assertNotIn(key, fx.data["expect"])


class ValidatorNegativeTests(unittest.TestCase):
    """The validator must reject a simulation-error expectation that lies."""

    def run_validation(self, contents: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.toml").write_text(contents, encoding="utf-8")
            return validate.validate_directory(root).errors

    def test_rejects_a_non_string_message_contains(self) -> None:
        # canary-soroban reads the field with `.and_then(toml::Value::as_str)`,
        # so an integer here is silently dropped and the fixture degrades to
        # "the simulation failed" while still looking like it pins a message.
        bad = BASE_FIXTURE + 'message_contains = 42\n'
        errors = self.run_validation(bad)
        self.assertTrue(
            any("message_contains" in e and "string" in e for e in errors),
            errors,
        )

    def test_rejects_an_empty_message_contains(self) -> None:
        # `str::contains("")` is always true, so an empty needle asserts nothing
        # beyond the failure itself.
        bad = BASE_FIXTURE + 'message_contains = ""\n'
        errors = self.run_validation(bad)
        self.assertTrue(
            any("message_contains" in e and "empty" in e for e in errors),
            errors,
        )

    def test_rejects_an_unknown_expect_key(self) -> None:
        # canary-soroban calls unknown_body_keys over ["kind",
        # "message_contains"] and rejects anything else, so a typo'd key is a
        # loader error that CI should catch rather than defer to run time.
        for key in ("message_contain", "error_code", "kind2"):
            with self.subTest(key=key):
                bad = BASE_FIXTURE + f'{key} = "Error(Value, InvalidInput)"\n'
                errors = self.run_validation(bad)
                self.assertTrue(
                    any("unexpected key" in e and key in e for e in errors),
                    errors,
                )

    def test_message_contains_is_optional(self) -> None:
        # The counter-rule, pinned on purpose: omitting message_contains is
        # valid, because canary-soroban models it as Option<String>. See the
        # module docstring for why issue #302's literal wording cannot be
        # implemented without diverging from the authoritative loader.
        self.assertEqual(self.run_validation(BASE_FIXTURE), [])

    def test_accepts_a_pinned_message(self) -> None:
        good = BASE_FIXTURE + 'message_contains = "Error(Value, InvalidInput)"\n'
        self.assertEqual(self.run_validation(good), [])

    def test_validator_and_schema_agree_on_the_expect_keys(self) -> None:
        # schemas/fixture-v1.schema.json closes [expect] with
        # additionalProperties: false; the validator's allow-list must name the
        # same two keys or the two will drift.
        import json

        schema = json.loads(
            (REPO_ROOT / "schemas" / "fixture-v1.schema.json").read_text(encoding="utf-8")
        )
        soroban_block = next(
            block
            for block in schema["allOf"]
            if block.get("if", {}).get("properties", {})
            .get("surface", {})
            .get("const")
            == "soroban"
        )
        expect = soroban_block["then"]["properties"]["expect"]
        self.assertFalse(expect["additionalProperties"])
        self.assertEqual(
            sorted(expect["properties"]), sorted(validate.SOROBAN_EXPECT_FIELDS)
        )
        self.assertEqual(expect["properties"]["message_contains"]["minLength"], 1)


if __name__ == "__main__":
    unittest.main()

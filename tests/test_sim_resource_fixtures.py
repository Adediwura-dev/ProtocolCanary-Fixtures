"""Tests for the staged resource-estimation fixture corpus (issue #301, F-044).

Issue #301 asks this pack for fixtures asserting on ``simulateTransaction``'s
resource estimation -- ``minResourceFee``, ``transactionData``, and the CPU and
I/O budgets encoded inside it. It also states the constraint that shapes this
whole file:

    This task must not claim that an unsupported assertion already exists.

It does not. ``canary-soroban``'s ``SorobanAssertion`` has exactly two variants,
``SimulationSuccess`` and ``SimulationError``, at ``StellarCanary/Protocol-Canary``
commit ``4d2cb9978127ecd1045b4cca022a4681875d0052`` -- re-read for this change,
not carried over from an earlier audit -- and ``CONTRIBUTING.md#fixture-schema``
forbids this repository's schema from leading that loader. So the fixtures are
**staged** under ``tests/vectors/resource_estimation/``, a path neither
``tools/validate/validate.py`` nor ``tools/badge/badge.py`` scans (both glob only
``protocol-*/``), and the assertion vocabulary they use lives in
``schemas/proposed/simulation-resource-estimate.expect.json``, which is
deliberately not reachable from ``schemas/fixture-v1.schema.json``.

Everything here is offline and deterministic. The network observations were made
once, on 2026-10-16 UTC, and are recorded verbatim in each staged fixture's
``[staged_observation]`` table. What this file does with them is the part worth
testing: it re-decodes the recorded ``transactionData`` bytes against the XDR in
``stellar-xdr`` v28.0 and checks, byte-exactly, that the bounds each fixture
declares actually hold for the observation the fixture claims to be based on. A
fixture whose header prose and whose assertions disagree therefore fails here
rather than looking plausible in review.

Four jobs:

1. Prove the staged fixtures are well-formed against the *proposed* vocabulary,
   and that their recorded observations satisfy the bounds they declare.
2. Cover the negative cases issue #301 asks for -- an invalid or negative
   resource fee must fail schema validation.
3. Guard the shipped pack: nothing under ``protocol-*/`` may use the unsupported
   kind, and the shipped validator must in fact reject it. This is the test that
   makes the staging a safety property rather than a filing choice.
4. Keep the documentation honest about what is staged and why.

Run with: python3 -m unittest tests/test_sim_resource_fixtures.py
"""
from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STAGED_DIR = REPO_ROOT / "tests" / "vectors" / "resource_estimation"
PROPOSED_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "proposed" / "simulation-resource-estimate.expect.json"
)
SHIPPED_SCHEMA_PATH = REPO_ROOT / "schemas" / "fixture-v1.schema.json"
DOCS_PATH = REPO_ROOT / "docs" / "protocol-28.md"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate = _load("validate", REPO_ROOT / "tools" / "validate" / "validate.py")
lite = _load("jsonschema_lite", REPO_ROOT / "tools" / "validate" / "jsonschema_lite.py")
badge = _load("badge", REPO_ROOT / "tools" / "badge" / "badge.py")

PROPOSED_KIND = "simulation-resource-estimate"

STAGED_IDS = (
    "p28-soroban-sim-resource-min-fee-positive",
    "p28-soroban-sim-resource-fee-matches-transaction-data",
    "p28-soroban-sim-resource-pure-read-footprint",
)

# XDR field widths, from stellar-xdr v28.0's Stellar-transaction.x. These are
# the boundaries issue #301's "boundary fixtures" are built out of: a resource
# estimate that does not fit the field carrying it is malformed no matter what
# number the host intended.
INT64_MAX = 2**63 - 1  # SorobanTransactionData.resourceFee
UINT32_MAX = 2**32 - 1  # SorobanResources.instructions / diskReadBytes / writeBytes

NATIVE_SAC = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
ZERO_ACCOUNT = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
UPSTREAM_COMMIT = "4d2cb9978127ecd1045b4cca022a4681875d0052"

# XDR enum members this decoder is willing to meet, spelled out so an unexpected
# discriminant fails loudly instead of being skipped by a guessed width.
SCV_LEDGER_KEY_CONTRACT_INSTANCE = 20
SC_ADDRESS_TYPE_ACCOUNT = 0
SC_ADDRESS_TYPE_CONTRACT = 1
LEDGER_ENTRY_CONTRACT_DATA = 6
CONTRACT_DATA_DURABILITY_PERSISTENT = 1
CRYPTO_KEY_TYPE_ED25519 = 0


class XdrError(AssertionError):
    """The recorded bytes do not match the v28.0 XDR this decoder implements."""


class _Reader:
    """Cursor over an XDR buffer that refuses to read past the end."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def _take(self, count: int) -> bytes:
        if self.offset + count > len(self.data):
            raise XdrError(
                f"buffer truncated: wanted {count} byte(s) at offset {self.offset} "
                f"of a {len(self.data)}-byte encoding"
            )
        chunk = self.data[self.offset : self.offset + count]
        self.offset += count
        return chunk

    def u32(self) -> int:
        return int.from_bytes(self._take(4), "big")

    def i64(self) -> int:
        return int.from_bytes(self._take(8), "big", signed=True)

    def fixed(self, count: int) -> bytes:
        """Fixed-length opaque: no length prefix and no padding."""
        return self._take(count)

    def opaque(self, count: int) -> bytes:
        """Fixed-length opaque, then the zero padding RFC 4506 requires."""
        value = self._take(count)
        padding = (-count) % 4
        if padding and any(self._take(padding)):
            raise XdrError("non-zero padding after a variable-length opaque")
        return value

    def string(self) -> bytes:
        return self.opaque(self.u32())

    @property
    def exhausted(self) -> bool:
        return self.offset == len(self.data)


def decode_scval(reader: _Reader) -> dict:
    """Decode the two ``SCVal`` arms a contract-instance footprint key can use."""
    value_type = reader.u32()
    if value_type == SCV_LEDGER_KEY_CONTRACT_INSTANCE:
        return {"type": value_type, "name": "LedgerKeyContractInstance"}  # void arm
    raise XdrError(
        f"SCValType {value_type} is not one this decoder implements; extend it "
        "rather than guessing a width"
    )


def decode_scaddress(reader: _Reader) -> dict:
    address_type = reader.u32()
    if address_type == SC_ADDRESS_TYPE_CONTRACT:
        return {
            "type": address_type,
            "name": "Contract",
            "hash": reader.fixed(32).hex(),
        }
    if address_type == SC_ADDRESS_TYPE_ACCOUNT:
        # AccountID is a PublicKey union: a CryptoKeyType discriminant, then a
        # uint256 for the only key type in use.
        if reader.u32() != CRYPTO_KEY_TYPE_ED25519:
            raise XdrError("AccountID carries a non-ED25519 CryptoKeyType")
        return {
            "type": address_type,
            "name": "Account",
            "hash": reader.fixed(32).hex(),
        }
    raise XdrError(f"SCAddressType {address_type} is not one this decoder implements")


def decode_ledger_key(reader: _Reader) -> dict:
    entry_type = reader.u32()
    if entry_type != LEDGER_ENTRY_CONTRACT_DATA:
        raise XdrError(
            f"LedgerEntryType {entry_type} is not CONTRACT_DATA; a read-only SAC "
            "accessor's footprint is not what this fixture recorded"
        )
    # union LedgerKey, CONTRACT_DATA arm: three fields, not two. Omitting
    # `durability` mis-aligns every later offset -- that mistake was made while
    # authoring these fixtures and is why the decode is checked for exhaustion.
    address = decode_scaddress(reader)
    key = decode_scval(reader)
    durability = reader.u32()
    return {
        "entry_type": entry_type,
        "address": address,
        "key": key,
        "durability": durability,
    }


def decode_soroban_transaction_data(b64: str) -> dict:
    """Decode a base64 ``SorobanTransactionData`` exactly, or raise ``XdrError``.

    stellar-xdr v28.0, ``Stellar-transaction.x``::

        struct SorobanTransactionData {
            union switch (int v) {
                case 0: void;
                case 1: SorobanResourcesExtV0 resourceExt;
            } ext;
            SorobanResources resources;  // footprint, instructions, diskReadBytes, writeBytes
            int64 resourceFee;
        };

    The caller asserts the buffer was consumed to the byte. That is what makes
    this a real check rather than a plausible-looking one: a decoder that
    silently mis-aligns would still return numbers, but would leave bytes over
    or run off the end.
    """
    raw = base64.b64decode(b64)
    reader = _Reader(raw)

    ext_version = reader.u32()
    archived: list[int] = []
    if ext_version == 1:  # SorobanResourcesExtV0 { uint32 archivedSorobanEntries<>; }
        archived = [reader.u32() for _ in range(reader.u32())]
    elif ext_version != 0:
        raise XdrError(f"SorobanTransactionData.ext.v is {ext_version}, expected 0 or 1")

    read_only = [decode_ledger_key(reader) for _ in range(reader.u32())]
    read_write = [decode_ledger_key(reader) for _ in range(reader.u32())]
    decoded = {
        "decoded_bytes": len(raw),
        "consumed_bytes": None,  # set below, after the fixed-width tail
        "ext_v": ext_version,
        "archived_soroban_entries": archived,
        "footprint_read_only": read_only,
        "footprint_read_write": read_write,
        "instructions": reader.u32(),
        "disk_read_bytes": reader.u32(),
        "write_bytes": reader.u32(),
        "resource_fee": reader.i64(),
    }
    if not reader.exhausted:
        raise XdrError(
            f"decode stopped at byte {reader.offset} of {len(raw)}; "
            f"{len(raw) - reader.offset} trailing byte(s) mean the layout assumed "
            "here is not the layout that was encoded"
        )
    decoded["consumed_bytes"] = reader.offset
    return decoded


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
        raise XdrError(f"{address!r} decodes to {len(raw)} bytes, expected 35")
    if raw[0] != 2 << 3:
        raise XdrError(f"{address!r} version byte {raw[0]} is not CONTRACT (16)")
    if crc16_xmodem(raw[:-2]).to_bytes(2, "little") != raw[-2:]:
        raise XdrError(f"{address!r} has a bad CRC-16/XMODEM checksum")
    return raw[1:-2]


def load_staged() -> dict[str, dict]:
    """Every staged fixture, keyed by id, with its path and raw text attached."""
    fixtures: dict[str, dict] = {}
    for path in sorted(STAGED_DIR.glob("*.toml")):
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        data["_path"] = path
        data["_text"] = path.read_text(encoding="utf-8")
        fixtures[data["id"]] = data
    return fixtures


PROPOSED_SCHEMA = json.loads(PROPOSED_SCHEMA_PATH.read_text(encoding="utf-8"))

# Bounds the proposed vocabulary declares as pairs. JSON Schema cannot compare
# two properties of the same object, and jsonschema_lite deliberately implements
# no cross-field keyword, so the "at_least <= at_most" half of the rule lives
# here. Both halves are tested.
BOUND_PAIRS = (
    ("min_resource_fee_at_least", "min_resource_fee_at_most"),
    ("instructions_at_least", "instructions_at_most"),
    ("disk_read_bytes_at_least", "disk_read_bytes_at_most"),
    ("write_bytes_at_least", "write_bytes_at_most"),
)


def bound_pair_errors(expect: dict) -> list[str]:
    return [
        f"{low} ({expect[low]}) is greater than {high} ({expect[high]}): "
        "an inverted range can never be satisfied"
        for low, high in BOUND_PAIRS
        if low in expect and high in expect and expect[low] > expect[high]
    ]


def check_expect(expect: dict) -> list[str]:
    """Schema validation plus the one rule JSON Schema cannot express."""
    return lite.validate(expect, PROPOSED_SCHEMA) + bound_pair_errors(expect)


class StagedCorpusStructureTests(unittest.TestCase):
    """The staged files must be as rigorous as a shipped fixture, not a draft."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = load_staged()

    def test_exactly_the_expected_fixtures_are_staged(self) -> None:
        self.assertEqual(sorted(self.fixtures), sorted(STAGED_IDS))

    def test_ids_follow_the_repository_convention(self) -> None:
        # CONTRIBUTING.md's `p<protocol>-<surface>-<slug>`, and the same
        # `sim-` infix the shipped p28-soroban-sim-error-* suite uses, so the
        # ids need no renaming on promotion.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertTrue(fixture_id.startswith("p28-soroban-sim-resource-"))
                self.assertEqual(fx["_path"].stem, fixture_id)

    def test_common_fields_match_the_shipped_contract(self) -> None:
        # Staging changes where a fixture lives, not what it must declare. If
        # these drifted, promotion would need edits beyond a `git mv`.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(fx["protocol"], 28)
                self.assertEqual(fx["surface"], "soroban")
                self.assertEqual(fx["category"], "resource-estimation")
                self.assertTrue(fx["description"])
                self.assertTrue(fx["source_reference"].startswith("https://"))

    def test_source_reference_pins_a_commit_not_a_branch(self) -> None:
        # CONTRIBUTING.md requires a GitHub link to name a full commit SHA or a
        # release tag. These cite the XDR the assertions are defined against.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                reference = fx["source_reference"]
                self.assertIn("/blob/", reference)
                revision = reference.split("/blob/")[1].split("/")[0]
                self.assertRegex(revision, r"^[0-9a-f]{40}$")
                self.assertIn("stellar/stellar-xdr", reference)

    def test_each_file_starts_with_a_header_comment(self) -> None:
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                first = next(
                    line for line in fx["_text"].splitlines() if line.strip()
                )
                self.assertTrue(first.lstrip().startswith("#"))

    def test_each_header_says_the_fixture_is_staged_and_why(self) -> None:
        # The single most important property of this directory: no reader may
        # mistake these for consumable fixtures. Issue #301's "must not claim
        # that an unsupported assertion already exists" is enforced here.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                header = fx["_text"].split("\nid =")[0].lower()
                self.assertIn("staged fixture", header)
                self.assertIn("do not move into", header)
                self.assertIn(PROPOSED_KIND, header)

    def test_each_header_records_when_and_where_it_was_observed(self) -> None:
        # CONTRIBUTING.md's "Recording verification dates": YYYY-MM-DD in UTC.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertRegex(fx["_text"], r"\b20\d\d-\d\d-\d\d\b")
                self.assertIn("UTC", fx["_text"])

    def test_observations_are_deterministic_inputs(self) -> None:
        # The unfunded all-zero account and the reserved native SAC: the same
        # setup p28-soroban-native-asset-name already ships, so nothing here
        # depends on a contract this repository deployed or on account state.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(fx["source_account"], ZERO_ACCOUNT)
                self.assertEqual(fx["contract_id"], NATIVE_SAC)
                self.assertEqual(fx["sequence_number"], 1)
                self.assertIn(fx["function"], ("name", "decimals", "symbol"))

    def test_staged_files_do_not_claim_live_varying_state_as_expectations(self) -> None:
        # The recorded ledger and fees belong in [staged_observation] only. An
        # equality against them inside [expect] would fail on the next ledger.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                expect = fx["expect"]
                for key in ("latest_ledger", "sequence", "min_resource_fee", "transaction_data"):
                    self.assertNotIn(key, expect)
                self.assertNotIn("observed_utc", expect)

    def test_three_distinct_functions_are_covered(self) -> None:
        functions = {fx["function"] for fx in self.fixtures.values()}
        self.assertEqual(functions, {"name", "decimals", "symbol"})


class ProposedVocabularyTests(unittest.TestCase):
    """The staged [expect] blocks must conform to the proposed fragment."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = load_staged()

    def test_every_staged_expect_block_validates(self) -> None:
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(check_expect(fx["expect"]), [])

    def test_every_staged_expect_declares_the_proposed_kind(self) -> None:
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(fx["expect"]["kind"], PROPOSED_KIND)

    def test_no_two_fixtures_assert_the_same_thing(self) -> None:
        # Three files asserting one property would be one fixture with extra
        # steps. Distinct field sets mean distinct estimates are exercised.
        signatures = {
            tuple(sorted(set(fx["expect"]) - {"kind"}))
            for fx in self.fixtures.values()
        }
        self.assertEqual(len(signatures), len(self.fixtures))

    def test_proposed_fragment_is_not_wired_into_the_shipped_schema(self) -> None:
        # The whole point of staging: schemas/fixture-v1.schema.json must keep
        # mirroring the released loader (CONTRIBUTING.md#fixture-schema), so it
        # may not mention a kind canary-soroban rejects.
        shipped = SHIPPED_SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertNotIn(PROPOSED_KIND, shipped)
        self.assertNotIn("proposed/", shipped)
        self.assertNotIn("simulation-resource-estimate.expect.json", shipped)

    def test_shipped_schema_still_matches_the_validator(self) -> None:
        # Guard against this change having touched the authoritative schema.
        schema_sync = _load("schema_sync", REPO_ROOT / "tools" / "validate" / "schema_sync.py")
        self.assertEqual(
            schema_sync.check_sync(schema_sync.load_schema(), validate), []
        )

    def test_fragment_bounds_are_the_xdr_field_widths(self) -> None:
        # A bound that is not an XDR width would be a guessed "reasonable"
        # number, which CONTRIBUTING.md forbids.
        properties = PROPOSED_SCHEMA["properties"]
        for field in ("min_resource_fee_at_least", "min_resource_fee_at_most"):
            self.assertEqual(properties[field]["maximum"], INT64_MAX, field)
        for field in (
            "instructions_at_least",
            "instructions_at_most",
            "disk_read_bytes_at_least",
            "disk_read_bytes_at_most",
            "write_bytes_at_least",
            "write_bytes_at_most",
        ):
            self.assertEqual(properties[field]["maximum"], UINT32_MAX, field)
        self.assertEqual(
            properties["resource_fee_matches_min_resource_fee"]["const"], True
        )

    def test_fragment_closes_the_expect_table(self) -> None:
        # Mirrors the shipped soroban [expect], which canary-soroban also closes
        # via unknown_body_keys; an open table would let a typo'd bound pass.
        self.assertFalse(PROPOSED_SCHEMA["additionalProperties"])
        self.assertEqual(PROPOSED_SCHEMA["required"], ["kind"])

    def test_fragment_uses_only_keywords_the_lite_validator_implements(self) -> None:
        # jsonschema_lite raises SchemaError on an unimplemented keyword, so a
        # fragment using one would be unvalidatable in CI. Checking the fragment
        # against itself proves every keyword it contains is supported.
        self.assertIsInstance(lite.validate({"kind": PROPOSED_KIND}, PROPOSED_SCHEMA), list)


class NegativeValidationTests(unittest.TestCase):
    """Issue #301's negative testing: an invalid resource fee must not validate."""

    def check(self, expect: dict) -> list[str]:
        return check_expect(expect)

    def test_a_negative_resource_fee_fails(self) -> None:
        # resourceFee is an int64, so a negative value is *representable* -- the
        # schema floor of 1, not the type, is what rejects it. That is why the
        # minimum is part of the proposal rather than left to the type.
        errors = self.check({"kind": PROPOSED_KIND, "min_resource_fee_at_least": -1})
        self.assertTrue(errors, "a negative fee floor was accepted")
        self.assertIn("less than 1", errors[0])

    def test_a_zero_resource_fee_floor_fails(self) -> None:
        # Zero would let a host that returns no estimate at all pass.
        errors = self.check({"kind": PROPOSED_KIND, "min_resource_fee_at_least": 0})
        self.assertTrue(errors)

    def test_a_non_integer_resource_fee_fails(self) -> None:
        # RPC returns minResourceFee as a decimal *string*; a fixture that
        # copied that string into a bound would compare nothing.
        for value in ("6143", 6143.0, True, None, [6143]):
            with self.subTest(value=value):
                self.assertTrue(
                    self.check({"kind": PROPOSED_KIND, "min_resource_fee_at_least": value})
                )

    def test_a_fee_wider_than_int64_fails(self) -> None:
        errors = self.check(
            {"kind": PROPOSED_KIND, "min_resource_fee_at_most": INT64_MAX + 1}
        )
        self.assertTrue(errors)
        self.assertIn("greater than", errors[0])

    def test_instructions_wider_than_uint32_fail(self) -> None:
        self.assertTrue(
            self.check({"kind": PROPOSED_KIND, "instructions_at_most": UINT32_MAX + 1})
        )

    def test_a_negative_footprint_count_fails(self) -> None:
        self.assertTrue(
            self.check({"kind": PROPOSED_KIND, "footprint_read_only_count": -1})
        )

    def test_an_inverted_bound_pair_fails(self) -> None:
        errors = self.check(
            {"kind": PROPOSED_KIND, "min_resource_fee_at_least": 9000,
             "min_resource_fee_at_most": 100}
        )
        self.assertTrue(errors)
        self.assertIn("inverted range", errors[0])

    def test_a_equal_bound_pair_is_accepted(self) -> None:
        # The boundary of the boundary rule: a single-point range is legal, so
        # the check must be `>` and not `>=`.
        self.assertEqual(
            self.check(
                {"kind": PROPOSED_KIND, "min_resource_fee_at_least": 100,
                 "min_resource_fee_at_most": 100}
            ),
            [],
        )

    def test_asserting_the_fee_does_not_match_fails(self) -> None:
        # `false` would assert that the host returns two contradictory
        # estimates, which is not a compatibility property anyone wants pinned.
        errors = self.check(
            {"kind": PROPOSED_KIND, "resource_fee_matches_min_resource_fee": False}
        )
        self.assertTrue(errors)

    def test_an_unknown_expect_key_fails(self) -> None:
        for key in ("min_resource_fee", "resource_fee", "instructions", "cpu_instructions"):
            with self.subTest(key=key):
                errors = self.check({"kind": PROPOSED_KIND, key: 1})
                self.assertTrue(errors)
                self.assertIn("unexpected property", errors[0])

    def test_a_missing_kind_fails(self) -> None:
        self.assertTrue(self.check({"min_resource_fee_at_least": 1}))

    def test_the_shipped_kinds_are_not_valid_here(self) -> None:
        # The proposed fragment describes one kind. A fixture that kept
        # `simulation-success` while adding resource bounds would silently lose
        # the bounds, since the shipped loader ignores what it does not know.
        for kind in ("simulation-success", "simulation-error"):
            with self.subTest(kind=kind):
                self.assertTrue(self.check({"kind": kind, "min_resource_fee_at_least": 1}))


class ObservationParityTests(unittest.TestCase):
    """The recorded observations must satisfy the bounds derived from them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = load_staged()
        cls.decoded = {
            fixture_id: decode_soroban_transaction_data(
                fx["staged_observation"]["transaction_data"]
            )
            for fixture_id, fx in cls.fixtures.items()
        }

    def test_every_observation_decodes_byte_exactly(self) -> None:
        # The load-bearing check: a mis-aligned decode still yields numbers, so
        # "consumed every byte" is what proves the layout assumed here is the
        # layout that was encoded.
        for fixture_id, decoded in self.decoded.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(decoded["consumed_bytes"], decoded["decoded_bytes"])

    def test_declared_bounds_hold_for_the_recorded_observation(self) -> None:
        for fixture_id, fx in self.fixtures.items():
            decoded = self.decoded[fixture_id]
            observation = fx["staged_observation"]
            fee = int(observation["min_resource_fee"])
            actual = {
                "min_resource_fee": fee,
                "instructions": decoded["instructions"],
                "disk_read_bytes": decoded["disk_read_bytes"],
                "write_bytes": decoded["write_bytes"],
                "footprint_read_only_count": len(decoded["footprint_read_only"]),
                "footprint_read_write_count": len(decoded["footprint_read_write"]),
            }
            for key, value in fx["expect"].items():
                if key == "kind":
                    continue
                with self.subTest(fixture=fixture_id, assertion=key):
                    if key.endswith("_at_least"):
                        self.assertGreaterEqual(actual[key[: -len("_at_least")]], value)
                    elif key.endswith("_at_most"):
                        self.assertLessEqual(actual[key[: -len("_at_most")]], value)
                    elif key.endswith("_count"):
                        self.assertEqual(actual[key], value)
                    elif key == "resource_fee_matches_min_resource_fee":
                        self.assertEqual(decoded["resource_fee"], fee)
                    else:  # pragma: no cover - the schema rejects unknown keys
                        self.fail(f"no observation check implemented for {key!r}")

    def test_encoded_resource_fee_equals_the_reported_min_resource_fee(self) -> None:
        # Checked for all three, not just the fixture that asserts it: the
        # equality is the evidence that the fixture's assertion is a property of
        # the response rather than a coincidence of one call.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(
                    self.decoded[fixture_id]["resource_fee"],
                    int(fx["staged_observation"]["min_resource_fee"]),
                )

    def test_footprint_names_the_contract_that_was_invoked(self) -> None:
        # Ties the estimate to the invocation: the read-only entry's contract
        # hash must be the strkey payload of the fixture's contract_id.
        expected_hash = strkey_contract_payload(NATIVE_SAC).hex()
        for fixture_id, decoded in self.decoded.items():
            with self.subTest(fixture=fixture_id):
                keys = decoded["footprint_read_only"]
                self.assertEqual(len(keys), 1)
                self.assertEqual(keys[0]["address"]["name"], "Contract")
                self.assertEqual(keys[0]["address"]["hash"], expected_hash)
                self.assertEqual(
                    keys[0]["key"]["name"], "LedgerKeyContractInstance"
                )
                self.assertEqual(
                    keys[0]["durability"], CONTRACT_DATA_DURABILITY_PERSISTENT
                )

    def test_observations_are_pure_reads(self) -> None:
        for fixture_id, decoded in self.decoded.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(decoded["ext_v"], 0)
                self.assertEqual(decoded["archived_soroban_entries"], [])
                self.assertEqual(decoded["footprint_read_write"], [])
                self.assertEqual(decoded["disk_read_bytes"], 0)
                self.assertEqual(decoded["write_bytes"], 0)
                self.assertGreater(decoded["instructions"], 0)

    def test_instruction_counts_differ_per_function(self) -> None:
        # Three identical estimates would mean the observation was copied rather
        # than made. They differ, and each is a plausible few-hundred-thousand.
        counts = {
            fixture_id: decoded["instructions"]
            for fixture_id, decoded in self.decoded.items()
        }
        self.assertEqual(len(set(counts.values())), 3, counts)
        for fixture_id, count in counts.items():
            with self.subTest(fixture=fixture_id):
                self.assertLess(count, UINT32_MAX)

    def test_recorded_result_xdr_is_the_positive_control(self) -> None:
        # The simulation must have actually succeeded and returned the SEP-41
        # value for the function called, otherwise the resource estimate was
        # produced by a failing invocation and proves nothing.
        expected = {
            "name": ("native", 14),
            "symbol": ("native", 14),
            "decimals": (7, 3),
        }
        for fixture_id, fx in self.fixtures.items():
            function = fx["function"]
            with self.subTest(fixture=fixture_id):
                raw = base64.b64decode(fx["staged_observation"]["result_xdr"])
                reader = _Reader(raw)
                value_type = reader.u32()
                if value_type == 14:  # SCV_STRING: variable-length opaque
                    decoded = reader.string().decode("ascii")
                elif value_type == 3:  # SCV_U32
                    decoded = reader.u32()
                else:  # pragma: no cover
                    self.fail(f"unexpected SCValType {value_type}")
                self.assertTrue(reader.exhausted)
                self.assertEqual((decoded, value_type), expected[function])

    def test_observations_record_a_full_session_context(self) -> None:
        # Enough metadata for a reader to judge staleness and reproduce: the
        # date, the endpoint, the ledger, and the protocol the endpoint itself
        # reported (29, newer than this pack targets).
        for fixture_id, fx in self.fixtures.items():
            observation = fx["staged_observation"]
            with self.subTest(fixture=fixture_id):
                self.assertEqual(observation["observed_utc"], "2026-10-16")
                self.assertEqual(
                    observation["rpc_endpoint"], "https://soroban-testnet.stellar.org"
                )
                self.assertEqual(observation["latest_ledger"], 5216960)
                self.assertEqual(observation["endpoint_protocol_version"], 29)

    def test_the_upstream_commit_cited_still_is_the_one_audited(self) -> None:
        # The header comments pin a specific Protocol-Canary commit for the
        # "unsupported" claim. If a future edit re-audits against a newer
        # commit, this list must be updated in the same change rather than
        # leaving three files citing a revision nobody re-checked.
        for fixture_id, fx in self.fixtures.items():
            with self.subTest(fixture=fixture_id):
                self.assertIn(UPSTREAM_COMMIT, fx["_text"])


class StagingGuardTests(unittest.TestCase):
    """Nothing here may leak into the consumable corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.packs = sorted(p for p in REPO_ROOT.glob("protocol-*") if p.is_dir())
        cls.shipped = [
            validate.load_fixture(path, validate.Report())
            for pack in cls.packs
            for path in validate.find_fixture_files(pack)
        ]
        cls.shipped = [fx for fx in cls.shipped if fx is not None]

    def test_the_shipped_corpus_is_untouched_by_this_change(self) -> None:
        # The pack must still validate, and still contain no resource-estimation
        # fixture: this change adds staged material only.
        for pack in self.packs:
            with self.subTest(pack=pack.name):
                self.assertEqual(validate.validate_directory(pack).errors, [])
        self.assertTrue(self.shipped)

    def test_no_shipped_fixture_uses_the_proposed_kind(self) -> None:
        for fx in self.shipped:
            if fx.data.get("surface") != "soroban":
                continue
            with self.subTest(fixture=fx.data.get("id")):
                expect = fx.data.get("expect", {})
                self.assertNotEqual(expect.get("kind"), PROPOSED_KIND)
                self.assertEqual(
                    sorted(set(expect) - set(validate.SOROBAN_EXPECT_FIELDS)), []
                )

    def test_no_shipped_fixture_is_a_resource_estimation_fixture(self) -> None:
        # Belt and braces: a fixture could name the concept without using the
        # kind. `category` is the field a reviewer would scan.
        for fx in self.shipped:
            with self.subTest(fixture=fx.data.get("id")):
                self.assertNotIn("resource", str(fx.data.get("category", "")).lower())

    def test_the_shipped_validator_rejects_a_staged_fixture(self) -> None:
        # This is the empirical half of the staging rationale. If it ever stops
        # erroring, the loader has caught up and these files should be promoted.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path in STAGED_DIR.glob("*.toml"):
                shutil.copy(path, root / path.name)
            errors = validate.validate_directory(root).errors
        self.assertTrue(errors, "validate.py accepted the proposed kind")
        self.assertTrue(
            any("expect.kind must be one of" in e for e in errors),
            errors,
        )
        self.assertTrue(
            any("unexpected key(s)" in e for e in errors),
            errors,
        )

    def test_staged_files_are_outside_every_scan_root(self) -> None:
        # Both validate.py's and badge.py's pack discovery glob `protocol-*/`.
        # If either ever widened, these files would be parsed as fixtures.
        self.assertTrue(STAGED_DIR.is_dir())
        for pack in self.packs:
            self.assertNotIn(pack, STAGED_DIR.parents)
        self.assertFalse(str(STAGED_DIR.relative_to(REPO_ROOT)).startswith("protocol-"))
        self.assertEqual(badge.pack_roots(REPO_ROOT), self.packs)

    def test_the_fixture_count_badge_is_unaffected(self) -> None:
        # Staged files are not fixtures, so README.md's badge must not move.
        self.assertEqual(
            badge.count_fixtures(REPO_ROOT),
            sum(len(validate.find_fixture_files(pack)) for pack in self.packs),
        )
        self.assertEqual(badge.main(["--check"]), 0)

    def test_no_fixture_ids_collide_between_staged_and_shipped(self) -> None:
        # Ids must be unique repository-wide; a collision would break promotion.
        staged = set(load_staged())
        shipped = {str(fx.data.get("id")) for fx in self.shipped}
        self.assertEqual(staged & shipped, set())


class DocumentationTests(unittest.TestCase):
    """The gap record must describe what now exists, not just what is missing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = DOCS_PATH.read_text(encoding="utf-8")
        cls.staging_readme = (STAGED_DIR / "README.md").read_text(encoding="utf-8")

    def test_docs_name_every_staged_fixture(self) -> None:
        for fixture_id in STAGED_IDS:
            with self.subTest(fixture=fixture_id):
                self.assertIn(fixture_id, self.docs)

    def test_docs_point_at_the_staging_directory(self) -> None:
        self.assertIn("tests/vectors/resource_estimation", self.docs)

    def test_docs_name_the_proposed_schema_fragment(self) -> None:
        self.assertIn(
            "schemas/proposed/simulation-resource-estimate.expect.json", self.docs
        )

    def test_docs_record_the_engine_prerequisite(self) -> None:
        # Issue #301's acceptance criterion: "accurately documents that resource
        # assertion is an engine-extension prerequisite".
        for needle in ("SorobanAssertion", "unsupported", "canary-soroban"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.docs)

    def test_docs_cite_the_same_upstream_commit_as_the_fixtures(self) -> None:
        self.assertIn(UPSTREAM_COMMIT, self.docs)

    def test_docs_no_longer_claim_the_pack_has_no_resource_fixtures_at_all(self) -> None:
        # The pre-existing gap record said "Nothing, yet" for both auth and
        # resource estimation. Auth is still nothing; resource estimation now
        # has a staged corpus, and the doc must not understate what shipped.
        section = self.docs.split(
            "## Soroban simulation auth and resource estimation", 1
        )[1].split("\n## ", 1)[0]
        self.assertIn("staged", section.lower())
        self.assertNotIn("Nothing, yet", section)

    def test_staging_readme_lists_every_file_it_contains(self) -> None:
        for path in sorted(STAGED_DIR.glob("*.toml")):
            with self.subTest(file=path.name):
                self.assertIn(path.name, self.staging_readme)

    def test_relative_links_in_the_staging_readme_resolve(self) -> None:
        # CI's markdown-links job runs lychee over every *.md, and a broken
        # relative link fails it. Check the ones this change adds on disk.
        import re

        for match in re.finditer(r"\]\(([^)#][^)]*)\)", self.staging_readme):
            target = match.group(1).split("#")[0]
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            with self.subTest(target=target):
                self.assertTrue(
                    (STAGED_DIR / target).resolve().exists(),
                    f"README.md links to {target!r}, which does not exist",
                )


if __name__ == "__main__":
    unittest.main()

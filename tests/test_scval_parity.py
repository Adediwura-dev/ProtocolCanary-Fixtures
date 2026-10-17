"""ScVal serialization parity tests (issue #304, F-047).

Asserts that the golden vectors in ``tests/vectors/scval_parity/`` and the
standard-library codec in ``tests/scval_xdr.py`` agree with the ScVal wire
format defined by ``stellar/stellar-xdr`` at tag ``v28.0``, and that the
agreement is bit-for-bit: encoding a value and decoding it again must
reproduce the original bytes exactly, not merely an equal value.

The vectors' ``xdr_hex`` fields were derived by writing each encoding out
field by field from ``Stellar-contract.x`` (see the JSON file's
``description``), independently of the codec, so these tests compare two
derivations rather than checking a codec against its own output.

Coverage is the set issue #304 asks for -- integers, bytes, addresses,
vectors and maps -- plus the remaining primitive ScVal variants. The five
ScVal variants this repository has no use for (``SCV_ERROR``,
``SCV_CONTRACT_INSTANCE``, ``SCV_LEDGER_KEY_CONTRACT_INSTANCE``,
``SCV_LEDGER_KEY_NONCE``, ``SCV_EXECUTABLE_TAG``) are deliberately not
implemented, and ``test_unsupported_discriminants_are_rejected`` pins that
they are rejected rather than silently mis-decoded.

Offline and deterministic: no network, no subprocess, no live ledger state.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEC_PATH = Path(__file__).resolve().parent / "scval_xdr.py"
VECTORS_PATH = REPO_ROOT / "tests" / "vectors" / "scval_parity" / "scval_parity.json"

# Loaded by path rather than by import so the suite works under both
# `python3 -m unittest tests/test_scval_parity.py` and
# `python3 -m unittest discover tests`, matching the convention in
# tests/test_pack_protocol_28.py.
_spec = importlib.util.spec_from_file_location("scval_xdr", CODEC_PATH)
assert _spec is not None and _spec.loader is not None
scval_xdr = importlib.util.module_from_spec(_spec)
sys.modules["scval_xdr"] = scval_xdr
_spec.loader.exec_module(scval_xdr)

XdrDecodeError = scval_xdr.XdrDecodeError
XdrEncodeError = scval_xdr.XdrEncodeError

# ScValType discriminants this repository intentionally leaves unimplemented.
UNIMPLEMENTED_DISCRIMINANTS = {
    2: "SCV_ERROR",
    19: "SCV_CONTRACT_INSTANCE",
    20: "SCV_LEDGER_KEY_CONTRACT_INSTANCE",
    21: "SCV_LEDGER_KEY_NONCE",
    22: "SCV_EXECUTABLE_TAG",
}

# The type families issue #304's acceptance criteria require coverage of,
# mapped to the ScVal kinds that satisfy each.
REQUIRED_FAMILIES = {
    "integers": {"u32", "i32", "u64", "i64", "u128", "i128", "u256", "i256"},
    "bytes": {"bytes"},
    "addresses": {"address"},
    "vectors": {"vec"},
    "maps": {"map"},
}


def load_vectors() -> list[dict]:
    payload = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    return payload["vectors"]


def vector_bytes(vector: dict) -> bytes:
    return bytes.fromhex(vector["xdr_hex"])


def invisible_bit_positions(raw: bytes, expected: dict) -> set[tuple[int, int]]:
    """Bit positions a single flip cannot change the decoded value at.

    The ScVal bool payload is the one place the wire format has genuinely
    redundant encodings: stellar-xdr decodes it as ``let b = i == 1``, so for a
    ``false`` vector every payload word other than 1 still reads as false (see
    ``test_bool_payload_leniency_matches_upstream``). Flipping any of those bits
    is invisible by design, not a decoder bug. Enumerating them explicitly
    keeps the bit-flip assertion meaningful everywhere else instead of being
    dropped wholesale.
    """
    if expected.get("type") != "bool" or expected.get("value") is not False:
        return set()
    positions: set[tuple[int, int]] = set()
    for index in range(4, min(8, len(raw))):
        for bit in range(8):
            corrupted = bytearray(raw)
            corrupted[index] ^= 1 << bit
            if int.from_bytes(bytes(corrupted[4:8]), "big") != 1:
                positions.add((index, bit))
    return positions


class VectorFileTests(unittest.TestCase):
    """The vector file itself must be well-formed and honestly sourced."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
        cls.vectors = cls.payload["vectors"]

    def test_vectors_file_exists_and_is_non_empty(self) -> None:
        self.assertTrue(VECTORS_PATH.is_file(), VECTORS_PATH)
        self.assertGreater(len(self.vectors), 0)

    def test_vector_names_are_unique(self) -> None:
        names = [v["name"] for v in self.vectors]
        self.assertEqual(len(names), len(set(names)), "duplicate vector names")

    def test_every_xdr_hex_is_well_formed(self) -> None:
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                raw = v["xdr_hex"]
                self.assertEqual(len(raw) % 2, 0, "hex string has odd length")
                int(raw, 16)  # raises ValueError on a non-hex character
                self.assertEqual(len(raw) % 8, 0, "XDR is 4-byte aligned")

    def test_source_reference_is_pinned_to_a_commit(self) -> None:
        # CONTRIBUTING.md requires a GitHub link to name a commit or tag, not a
        # branch. The vectors are only meaningful against one XDR revision.
        ref = self.payload["source_reference"]
        self.assertIn("/blob/", ref)
        commit = ref.split("/blob/", 1)[1].split("/", 1)[0]
        self.assertEqual(len(commit), 40, f"expected a 40-hex commit SHA, got {commit!r}")
        int(commit, 16)
        self.assertEqual(self.payload["xdr_tag"], "v28.0")

    def test_covers_the_required_type_families(self) -> None:
        kinds = {v["scval"]["type"] for v in self.vectors}
        for family, required in REQUIRED_FAMILIES.items():
            with self.subTest(family=family):
                self.assertTrue(
                    required & kinds,
                    f"no vector covers the {family} family (expected one of {sorted(required)})",
                )

    def test_covers_every_discriminant_the_codec_implements(self) -> None:
        kinds = {v["scval"]["type"] for v in self.vectors}
        self.assertEqual(
            kinds,
            set(scval_xdr.KIND_TO_CODE),
            "vector coverage and the codec's implemented kinds have drifted apart",
        )


class ScValParityTests(unittest.TestCase):
    """Bit-for-bit agreement between the vectors and the codec."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vectors = load_vectors()

    def test_encode_reproduces_the_golden_bytes(self) -> None:
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                scval = scval_xdr.from_json(v["scval"])
                self.assertEqual(scval_xdr.encode(scval), vector_bytes(v))

    def test_decode_reproduces_the_golden_value(self) -> None:
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                decoded = scval_xdr.decode(vector_bytes(v))
                self.assertEqual(
                    scval_xdr.to_json(decoded),
                    v["scval"],
                )

    def test_roundtrip_is_byte_identical(self) -> None:
        # The core assertion of issue #304: re-encoding a decoded vector must
        # reproduce the original bytes exactly, so no padding, discriminant or
        # length prefix is normalised away on the way through.
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                raw = vector_bytes(v)
                self.assertEqual(scval_xdr.encode(scval_xdr.decode(raw)), raw)

    def test_roundtrip_is_value_identical(self) -> None:
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                scval = scval_xdr.from_json(v["scval"])
                self.assertEqual(
                    scval_xdr.to_json(scval_xdr.decode(scval_xdr.encode(scval))),
                    scval_xdr.to_json(scval),
                )

    def test_base64_form_agrees_with_the_hex_form(self) -> None:
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                text = base64.b64encode(vector_bytes(v)).decode("ascii")
                self.assertEqual(
                    scval_xdr.to_json(scval_xdr.decode_base64(text)), v["scval"]
                )
                scval = scval_xdr.from_json(v["scval"])
                self.assertEqual(scval_xdr.encode_base64(scval), text)

    def test_null_and_empty_containers_are_distinct(self) -> None:
        # SCVec/SCMap sit behind a pointer in SCVal, so a null container and a
        # present-but-empty one are different encodings of different values.
        # Collapsing them would hide a real wire-format difference.
        by_name = {v["name"]: vector_bytes(v) for v in self.vectors}
        self.assertNotEqual(by_name["vec_null"], by_name["vec_empty"])
        self.assertNotEqual(by_name["map_null"], by_name["map_empty"])
        self.assertIsNone(scval_xdr.decode(by_name["vec_null"])[1])
        self.assertEqual(scval_xdr.decode(by_name["vec_empty"])[1], [])
        self.assertIsNone(scval_xdr.decode(by_name["map_null"])[1])
        self.assertEqual(scval_xdr.decode(by_name["map_empty"])[1], [])


class ScValNegativeTests(unittest.TestCase):
    """Malformed input must be rejected, never silently accepted."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vectors = load_vectors()

    def test_truncated_input_is_rejected(self) -> None:
        for v in self.vectors:
            raw = vector_bytes(v)
            for length in range(len(raw)):
                with self.subTest(vector=v["name"], length=length):
                    with self.assertRaises(XdrDecodeError):
                        scval_xdr.decode(raw[:length])

    def test_trailing_bytes_are_rejected(self) -> None:
        for v in self.vectors:
            with self.subTest(vector=v["name"]):
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(vector_bytes(v) + b"\x00\x00\x00\x00")

    def test_single_bit_flip_never_decodes_to_the_same_value(self) -> None:
        # A parity suite that only checked "valid input decodes" would still
        # pass if the decoder ignored most of the buffer. Flipping one bit at
        # every position must therefore either raise or change the value. The
        # bool-payload positions invisible_bit_positions documents are asserted
        # to decode unchanged, so the exemption pins upstream's behaviour
        # instead of quietly skipping it.
        for v in self.vectors:
            raw = vector_bytes(v)
            expected = v["scval"]
            invisible = invisible_bit_positions(raw, expected)
            for index in range(len(raw)):
                for bit in range(8):
                    corrupted = bytearray(raw)
                    corrupted[index] ^= 1 << bit
                    with self.subTest(vector=v["name"], byte=index, bit=bit):
                        try:
                            decoded = scval_xdr.to_json(
                                scval_xdr.decode(bytes(corrupted))
                            )
                        except XdrDecodeError:
                            continue
                        if (index, bit) in invisible:
                            self.assertEqual(decoded, expected)
                            continue
                        self.assertNotEqual(
                            decoded,
                            expected,
                            f"a bit flip at byte {index} bit {bit} decoded unchanged",
                        )

    def test_unsupported_discriminants_are_rejected(self) -> None:
        for code, name in UNIMPLEMENTED_DISCRIMINANTS.items():
            with self.subTest(discriminant=name):
                payload = code.to_bytes(4, "big") + b"\x00" * 64
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(payload)

    def test_out_of_range_discriminant_is_rejected(self) -> None:
        for code in (23, 99, 0xFFFFFFFF):
            with self.subTest(discriminant=code):
                payload = code.to_bytes(4, "big") + b"\x00" * 64
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(payload)

    def test_non_zero_padding_is_rejected(self) -> None:
        # RFC 4506 padding must be zero; accepting anything else would let two
        # different byte strings decode to the same ScVal and break the
        # bit-for-bit roundtrip guarantee.
        checked = 0
        for v in self.vectors:
            raw = vector_bytes(v)
            kind = v["scval"]["type"]
            if kind not in ("bytes", "string", "symbol"):
                continue
            length = int.from_bytes(raw[4:8], "big")
            pad = (-length) % 4
            if not pad:
                continue
            corrupted = bytearray(raw)
            for offset in range(pad):
                corrupted[len(raw) - pad + offset] = 0xFF
            with self.subTest(vector=v["name"]):
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(bytes(corrupted))
            checked += 1
        self.assertGreater(checked, 0, "no padded vector was exercised")

    def test_length_prefix_exceeding_the_buffer_is_rejected(self) -> None:
        # A corrupt length must be caught against the bytes actually present,
        # not used to slice or allocate.
        for kind_code in (scval_xdr.SCV_BYTES, scval_xdr.SCV_STRING, scval_xdr.SCV_SYMBOL):
            with self.subTest(discriminant=kind_code):
                payload = kind_code.to_bytes(4, "big") + (0xFFFFFF).to_bytes(4, "big") + b"ab"
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(payload)

    def test_symbol_over_the_limit_is_rejected(self) -> None:
        limit = scval_xdr.SC_SYMBOL_LIMIT
        self.assertEqual(limit, 32)
        oversized = "a" * (limit + 1)
        with self.assertRaises(XdrEncodeError):
            scval_xdr.encode(("symbol", oversized))
        payload = (
            scval_xdr.SCV_SYMBOL.to_bytes(4, "big")
            + (limit + 1).to_bytes(4, "big")
            + oversized.encode()
        )
        with self.assertRaises(XdrDecodeError):
            scval_xdr.decode(payload)

    def test_invalid_utf8_in_string_and_symbol_is_rejected(self) -> None:
        for kind_code in (scval_xdr.SCV_STRING, scval_xdr.SCV_SYMBOL):
            with self.subTest(discriminant=kind_code):
                payload = kind_code.to_bytes(4, "big") + (2).to_bytes(4, "big") + b"\xff\xfe" + b"\x00\x00"
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(payload)

    def test_bool_payload_leniency_matches_upstream(self) -> None:
        # stellar-xdr's `impl ReadXdr for bool` is `let b = i == 1`, so only the
        # word 1 is true and *every* other word is false rather than an error.
        # A stricter decoder would reject payloads the reference implementation
        # accepts, so this pins the lenient rule the bit-flip test exempts.
        for payload, expected in (
            (0, False),
            (1, True),
            (2, False),
            (3, False),
            (0x7FFFFFFF, False),
            (0xFFFFFFFF, False),
        ):
            with self.subTest(payload=payload):
                raw = scval_xdr.SCV_BOOL.to_bytes(4, "big") + payload.to_bytes(4, "big")
                self.assertEqual(scval_xdr.decode(raw), ("bool", expected))

        # Re-encoding canonicalizes, which is why a non-canonical payload does
        # not round-trip byte-for-byte and why every parity vector is canonical.
        self.assertEqual(
            scval_xdr.encode(("bool", False)), (0).to_bytes(4, "big") * 2
        )

    def test_optional_present_flag_other_than_zero_or_one_is_rejected(self) -> None:
        # `SCVec *vec` and `SCMap *map` decode through
        # `impl<T: ReadXdr> ReadXdr for Option<T>`, which returns
        # Error::Invalid for any word other than 0 (None) or 1 (Some).
        for kind in ("vec", "map"):
            code = scval_xdr.KIND_TO_CODE[kind]
            for flag in (2, 3, 0x100, 0xFFFFFFFF):
                with self.subTest(kind=kind, flag=flag):
                    raw = (
                        code.to_bytes(4, "big")
                        + flag.to_bytes(4, "big")
                        + (0).to_bytes(4, "big")
                    )
                    with self.assertRaises(XdrDecodeError):
                        scval_xdr.decode(raw)

    def test_unsupported_address_type_is_rejected(self) -> None:
        # v28.0 defines muxed-account, claimable-balance and liquidity-pool
        # addresses; this codec covers only account and contract, so the
        # others must be refused rather than decoded as one of those.
        for address_type in (2, 3, 4, 99):
            with self.subTest(address_type=address_type):
                payload = (
                    scval_xdr.SCV_ADDRESS.to_bytes(4, "big")
                    + address_type.to_bytes(4, "big")
                    + b"\x00" * 40
                )
                with self.assertRaises(XdrDecodeError):
                    scval_xdr.decode(payload)

    def test_account_address_requires_the_ed25519_discriminant(self) -> None:
        payload = (
            scval_xdr.SCV_ADDRESS.to_bytes(4, "big")
            + scval_xdr.SC_ADDRESS_TYPE_ACCOUNT.to_bytes(4, "big")
            + (1).to_bytes(4, "big")  # not KEY_TYPE_ED25519
            + b"\x11" * 32
        )
        with self.assertRaises(XdrDecodeError):
            scval_xdr.decode(payload)

    def test_out_of_range_values_are_rejected_on_encode(self) -> None:
        cases = [
            ("u32", 1 << 32),
            ("u32", -1),
            ("i32", 1 << 31),
            ("i32", -(1 << 31) - 1),
            ("u64", 1 << 64),
            ("i64", 1 << 63),
            ("u128", 1 << 128),
            ("i128", 1 << 127),
            ("i128", -(1 << 127) - 1),
            ("u256", 1 << 256),
            ("i256", 1 << 255),
        ]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                with self.assertRaises(XdrEncodeError):
                    scval_xdr.encode((kind, value))

    def test_boundary_values_are_accepted_on_encode(self) -> None:
        cases = [
            ("u32", (1 << 32) - 1),
            ("i32", -(1 << 31)),
            ("i32", (1 << 31) - 1),
            ("u128", (1 << 128) - 1),
            ("i128", -(1 << 127)),
            ("i128", (1 << 127) - 1),
            ("u256", (1 << 256) - 1),
            ("i256", -(1 << 255)),
        ]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                raw = scval_xdr.encode((kind, value))
                self.assertEqual(scval_xdr.decode(raw), (kind, value))

    def test_wrong_value_type_is_rejected_on_encode(self) -> None:
        with self.assertRaises(XdrEncodeError):
            scval_xdr.encode(("bool", 1))  # an int is not a bool
        with self.assertRaises(XdrEncodeError):
            scval_xdr.encode(("void", 0))
        with self.assertRaises(XdrEncodeError):
            scval_xdr.encode(("string", b"bytes"))
        with self.assertRaises(XdrEncodeError):
            scval_xdr.encode(("address", {"type": "liquidity_pool"}))
        with self.assertRaises(XdrEncodeError):
            scval_xdr.encode(("not-a-kind", 1))

    def test_malformed_base64_is_rejected(self) -> None:
        with self.assertRaises(XdrDecodeError):
            scval_xdr.decode_base64("!!!not base64!!!")


if __name__ == "__main__":
    unittest.main()

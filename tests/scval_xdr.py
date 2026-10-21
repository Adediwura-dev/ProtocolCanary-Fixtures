"""Standard-library-only XDR codec for the Soroban ``ScVal`` union.

This module exists so ``tests/test_scval_parity.py`` can check the golden
vectors under ``tests/vectors/scval_parity/`` for bit-for-bit roundtrip
identity without a third-party Stellar SDK: this repository has no
third-party Python dependencies at all (see CONTRIBUTING.md, "Development
setup"), and the fixture packs themselves are declarative TOML data, never
code.

The wire format is transcribed from the authoritative XDR definition in
``stellar/stellar-xdr`` at tag ``v28.0`` (commit
``9c9c145953e80990d6ff1ae3a6a973a0ce6d0694``), file ``Stellar-contract.x``:
``enum SCValType`` (lines 1-25), ``union SCVal`` (lines 27-80),
``typedef SCVal SCVec<>`` (line 82), ``typedef SCMapEntry SCMap<>`` (line 83),
``union SCAddress`` (lines 85-95) and the
``UInt128Parts``/``Int128Parts``/``UInt256Parts``/``Int256Parts`` structs
(lines 107-130). The nested types required by the five variants added in
F-096 are transcribed from the same file: ``enum SCErrorType`` (lines 132-142),
``union SCError`` (lines 144-154), ``struct SCContractInstance`` (lines 156-163),
``struct SCNonceKey`` (lines 165-168), ``union ContractExecutable`` (lines
172-237). All line ranges are against the v28.0 tag.

Scope is deliberately narrow. Only the ``ScVal`` variants the parity vectors
cover are implemented; all 19 discriminants defined in v28.0 are now covered.

Two rules from RFC 4506 that are easy to get wrong and are enforced here:

* variable-length opaque and string data is followed by zero padding to a
  4-byte boundary, and a decoder must reject non-zero padding -- otherwise
  two different byte strings decode to the same value and "roundtrip byte
  identity" stops meaning anything;
* ``SCVec`` and ``SCMap`` appear in ``SCVal`` behind a pointer
  (``SCVec *vec``), so they carry a 4-byte present/absent flag *before* the
  element count. An absent (null) vec and a present-but-empty vec are
  therefore distinct encodings, and both are exercised by the vectors.

Where the XDR text leaves a decoder's strictness open, this module follows
what ``stellar-xdr``'s generated ``ReadXdr`` implementations actually do,
because "parity" means agreeing with the reference implementation rather than
with the RFC's ideal. Two places matter and are pinned by tests:

* that present/absent flag decodes via ``impl<T: ReadXdr> for Option<T>``,
  which accepts only 0 and 1 and returns ``Error::Invalid`` otherwise -- so a
  nonzero-but-not-1 flag is rejected here too;
* an ``SCV_BOOL`` payload decodes via ``impl ReadXdr for bool``, which is
  ``let b = i == 1`` -- so any word other than 1 is ``false``, *not* an error.
  A stricter decoder would reject bytes the reference accepts. The cost is
  that ``false`` has 2**32 - 1 valid encodings and only the canonical one
  round-trips byte-for-byte; the vectors are all canonical.
"""
from __future__ import annotations

import base64
import binascii
from typing import Any

# SCValType discriminants, Stellar-contract.x at v28.0 (lines 1-25).
SCV_BOOL = 0
SCV_VOID = 1
SCV_ERROR = 2
SCV_U32 = 3
SCV_I32 = 4
SCV_U64 = 5
SCV_I64 = 6
SCV_TIMEPOINT = 7
SCV_DURATION = 8
SCV_U128 = 9
SCV_I128 = 10
SCV_U256 = 11
SCV_I256 = 12
SCV_BYTES = 13
SCV_STRING = 14
SCV_SYMBOL = 15
SCV_VEC = 16
SCV_MAP = 17
SCV_ADDRESS = 18
SCV_CONTRACT_INSTANCE = 19
SCV_LEDGER_KEY_CONTRACT_INSTANCE = 20
SCV_LEDGER_KEY_NONCE = 21
SCV_EXECUTABLE_TAG = 22

# SCSYMBOL_LIMIT, Stellar-contract.x at v28.0.
SC_SYMBOL_LIMIT = 32

# SCAddressType discriminants, Stellar-contract.x at v28.0 (lines 85-95). The
# remaining variants (muxed account, claimable balance, liquidity pool) are not
# covered by the parity vectors and are rejected on decode.
SC_ADDRESS_TYPE_ACCOUNT = 0
SC_ADDRESS_TYPE_CONTRACT = 1

# PublicKeyType::KEY_TYPE_ED25519, Stellar-crypto.x at v28.0.
KEY_TYPE_ED25519 = 0

# SCErrorType discriminants, Stellar-contract.x at v28.0 (lines 132-142).
SCE_OK = 0
SCE_TYPE = 1
SCE_VALUE = 2
SCE_ACCESS = 3
SCE_BUDGET = 4
SCE_CONTEXT = 5
SCE_CONTRACT = 6
SCE_WASM_VM = 7
SCE_STORAGE = 8
SCE_CRYPTO = 9

# ContractExecutableType discriminants, Stellar-contract.x at v28.0 (lines
# 172-177).
CONTRACT_EXECUTABLE_WASM = 0
CONTRACT_EXECUTABLE_STELLAR_ASSET = 1
CONTRACT_EXECUTABLE_EXTERNAL_REF = 2

_MASK64 = (1 << 64) - 1

# kind name -> discriminant, for the variants this module implements.
KIND_TO_CODE = {
    "bool": SCV_BOOL,
    "void": SCV_VOID,
    "error": SCV_ERROR,
    "u32": SCV_U32,
    "i32": SCV_I32,
    "u64": SCV_U64,
    "i64": SCV_I64,
    "timepoint": SCV_TIMEPOINT,
    "duration": SCV_DURATION,
    "u128": SCV_U128,
    "i128": SCV_I128,
    "u256": SCV_U256,
    "i256": SCV_I256,
    "bytes": SCV_BYTES,
    "string": SCV_STRING,
    "symbol": SCV_SYMBOL,
    "vec": SCV_VEC,
    "map": SCV_MAP,
    "address": SCV_ADDRESS,
    "contract-instance": SCV_CONTRACT_INSTANCE,
    "ledger-key-contract-instance": SCV_LEDGER_KEY_CONTRACT_INSTANCE,
    "ledger-key-nonce": SCV_LEDGER_KEY_NONCE,
    "executable-tag": SCV_EXECUTABLE_TAG,
}
CODE_TO_KIND = {code: kind for kind, code in KIND_TO_CODE.items()}


class XdrDecodeError(Exception):
    """Raised when a byte string is not a valid encoding of what was asked for."""


class XdrEncodeError(Exception):
    """Raised when a Python value cannot be represented in XDR."""


class _Reader:
    """Cursor over a byte string that refuses to read past its end."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or n > self.remaining:
            raise XdrDecodeError(
                f"truncated: needed {n} byte(s) but only {self.remaining} remain"
            )
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def i32(self) -> int:
        return int.from_bytes(self.take(4), "big", signed=True)

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "big")

    def i64(self) -> int:
        return int.from_bytes(self.take(8), "big", signed=True)

    def opaque_var(self, limit: int | None = None) -> bytes:
        length = self.u32()
        if limit is not None and length > limit:
            raise XdrDecodeError(f"length {length} exceeds the limit of {limit}")
        # Checked against the bytes actually left *before* slicing, so a
        # corrupt length prefix cannot allocate or silently truncate.
        if length > self.remaining:
            raise XdrDecodeError(
                f"length prefix {length} exceeds the {self.remaining} byte(s) remaining"
            )
        payload = self.take(length)
        pad = (-length) % 4
        if pad:
            padding = self.take(pad)
            if padding != b"\x00" * pad:
                raise XdrDecodeError(
                    f"non-zero padding {padding!r} after a {length}-byte value"
                )
        return payload


class _Writer:
    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def u32(self, value: int) -> None:
        self.buf += value.to_bytes(4, "big")

    def i32(self, value: int) -> None:
        self.buf += int(value).to_bytes(4, "big", signed=True)

    def u64(self, value: int) -> None:
        self.buf += value.to_bytes(8, "big")

    def i64(self, value: int) -> None:
        self.buf += int(value).to_bytes(8, "big", signed=True)

    def fixed(self, payload: bytes) -> None:
        self.buf += payload

    def opaque_var(self, payload: bytes) -> None:
        self.u32(len(payload))
        self.buf += payload
        self.buf += b"\x00" * ((-len(payload)) % 4)


def _check_uint(kind: str, value: int, bits: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise XdrEncodeError(f"{kind} value must be an int, got {type(value).__name__}")
    if value < 0 or value >= (1 << bits):
        raise XdrEncodeError(f"{kind} value {value} is outside the unsigned {bits}-bit range")


def _check_int(kind: str, value: int, bits: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise XdrEncodeError(f"{kind} value must be an int, got {type(value).__name__}")
    if value < -(1 << (bits - 1)) or value >= (1 << (bits - 1)):
        raise XdrEncodeError(f"{kind} value {value} is outside the signed {bits}-bit range")


def _write_u128_parts(w: _Writer, value: int) -> None:
    _check_uint("u128", value, 128)
    w.u64(value >> 64)
    w.u64(value & _MASK64)


def _write_i128_parts(w: _Writer, value: int) -> None:
    _check_int("i128", value, 128)
    # Arithmetic shift keeps the sign in `hi`, so hi * 2**64 + lo == value.
    w.i64(value >> 64)
    w.u64(value & _MASK64)


def _write_u256_parts(w: _Writer, value: int) -> None:
    _check_uint("u256", value, 256)
    for shift in (192, 128, 64, 0):
        w.u64((value >> shift) & _MASK64)


def _write_i256_parts(w: _Writer, value: int) -> None:
    _check_int("i256", value, 256)
    w.i64(value >> 192)
    for shift in (128, 64, 0):
        w.u64((value >> shift) & _MASK64)


def _write_address(w: _Writer, address: dict) -> None:
    kind = address.get("type")
    if kind == "account":
        key = _as_bytes(address.get("ed25519"), "account ed25519 key")
        if len(key) != 32:
            raise XdrEncodeError(f"an ed25519 account key is 32 bytes, got {len(key)}")
        w.u32(SC_ADDRESS_TYPE_ACCOUNT)
        # AccountID is a PublicKey union: a discriminant, then the 32-byte key.
        w.u32(KEY_TYPE_ED25519)
        w.fixed(key)
    elif kind == "contract":
        contract_id = _as_bytes(address.get("contract_id"), "contract id")
        if len(contract_id) != 32:
            raise XdrEncodeError(f"a contract id is 32 bytes, got {len(contract_id)}")
        w.u32(SC_ADDRESS_TYPE_CONTRACT)
        # ContractID is `Hash`, a fixed 32-byte opaque: no length prefix.
        w.fixed(contract_id)
    else:
        raise XdrEncodeError(
            f"unsupported address type {kind!r}; expected 'account' or 'contract'"
        )


def _as_bytes(value: Any, label: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        try:
            return bytes.fromhex(value.removeprefix("0x"))
        except ValueError as exc:
            raise XdrEncodeError(f"{label} must be hex-encoded: {exc}") from exc
    raise XdrEncodeError(f"{label} must be bytes or a hex string, got {type(value).__name__}")


def _write_sc_error(w: _Writer, error: dict) -> None:
    """Write an SCError union value.
    
    Stellar-contract.x v28.0 lines 144-154:
    union SCError switch (SCErrorType type) {
    case SCE_OK:
        void;
    case SCE_TYPE:
    case SCE_VALUE:
    case SCE_ACCESS:
    case SCE_BUDGET:
    case SCE_CONTEXT:
    case SCE_CONTRACT:
    case SCE_WASM_VM:
    case SCE_STORAGE:
    case SCE_CRYPTO:
        uint32 code;
    };
    """
    error_type = error.get("type")
    if error_type == "ok":
        w.u32(SCE_OK)
    else:
        type_map = {
            "type": SCE_TYPE,
            "value": SCE_VALUE,
            "access": SCE_ACCESS,
            "budget": SCE_BUDGET,
            "context": SCE_CONTEXT,
            "contract": SCE_CONTRACT,
            "wasm-vm": SCE_WASM_VM,
            "storage": SCE_STORAGE,
            "crypto": SCE_CRYPTO,
        }
        if error_type not in type_map:
            raise XdrEncodeError(
                f"unsupported SCErrorType {error_type!r}; expected one of {sorted(type_map)}"
            )
        w.u32(type_map[error_type])
        code = error.get("code")
        if not isinstance(code, int) or code < 0 or code >= (1 << 32):
            raise XdrEncodeError(f"SCError code must be a u32, got {code!r}")
        w.u32(code)


def _write_contract_executable(w: _Writer, executable: dict) -> None:
    """Write a ContractExecutable union value.
    
    Stellar-contract.x v28.0 lines 172-237.
    """
    exec_type = executable.get("type")
    if exec_type == "wasm":
        wasm_hash = _as_bytes(executable.get("wasm_hash"), "wasm hash")
        if len(wasm_hash) != 32:
            raise XdrEncodeError(f"wasm hash must be 32 bytes, got {len(wasm_hash)}")
        w.u32(CONTRACT_EXECUTABLE_WASM)
        w.fixed(wasm_hash)
    elif exec_type == "stellar-asset":
        w.u32(CONTRACT_EXECUTABLE_STELLAR_ASSET)
    elif exec_type == "external-ref":
        w.u32(CONTRACT_EXECUTABLE_EXTERNAL_REF)
        # ContractExecutableExternalRef: owner (SCAddress) + tag (string)
        owner = executable.get("owner")
        if not isinstance(owner, dict):
            raise XdrEncodeError("external-ref owner must be a dict")
        _write_address(w, owner)
        tag = executable.get("tag")
        if not isinstance(tag, str):
            raise XdrEncodeError("external-ref tag must be a string")
        encoded = tag.encode("utf-8")
        w.u32(len(encoded))
        w.buf += encoded
        w.buf += b"\x00" * ((-len(encoded)) % 4)
    else:
        raise XdrEncodeError(
            f"unsupported ContractExecutableType {exec_type!r}; expected 'wasm', 'stellar-asset', or 'external-ref'"
        )


def _write_contract_instance(w: _Writer, instance: dict) -> None:
    """Write an SCContractInstance struct.
    
    Stellar-contract.x v28.0 lines 156-163:
    struct SCContractInstance {
        ContractExecutable executable;
        optional<ContractExecutable> storage;
    };
    """
    # executable (required)
    exec_val = instance.get("executable")
    if not isinstance(exec_val, dict):
        raise XdrEncodeError("contract instance executable must be a dict")
    _write_contract_executable(w, exec_val)
    # storage (optional)
    storage = instance.get("storage")
    if storage is None:
        w.u32(0)
    else:
        w.u32(1)
        if not isinstance(storage, dict):
            raise XdrEncodeError("contract instance storage must be a dict")
        _write_contract_executable(w, storage)


def _write_nonce_key(w: _Writer, nonce: dict) -> None:
    """Write an SCNonceKey struct.
    
    Stellar-contract.x v28.0 lines 165-168:
    struct SCNonceKey {
        SCAddress address;
        uint64 nonce;
    };
    """
    address = nonce.get("address")
    if not isinstance(address, dict):
        raise XdrEncodeError("nonce key address must be a dict")
    _write_address(w, address)
    nonce_val = nonce.get("nonce")
    if not isinstance(nonce_val, int) or nonce_val < 0 or nonce_val >= (1 << 64):
        raise XdrEncodeError(f"nonce must be a u64, got {nonce_val!r}")
    w.u64(nonce_val)


def encode(scval: tuple[str, Any]) -> bytes:
    """Encode an ``ScVal`` and return its XDR bytes."""
    kind, value = scval
    try:
        code = KIND_TO_CODE[kind]
    except KeyError:
        raise XdrEncodeError(
            f"unsupported ScVal kind {kind!r}; expected one of {sorted(KIND_TO_CODE)}"
        ) from None

    w = _Writer()
    w.u32(code)

    if kind == "bool":
        if not isinstance(value, bool):
            raise XdrEncodeError(f"bool value must be a bool, got {type(value).__name__}")
        w.u32(1 if value else 0)
    elif kind == "void":
        if value is not None:
            raise XdrEncodeError("void carries no payload")
    elif kind in ("u32",):
        _check_uint(kind, value, 32)
        w.u32(value)
    elif kind in ("i32",):
        _check_int(kind, value, 32)
        w.i32(value)
    elif kind in ("u64", "timepoint", "duration"):
        _check_uint(kind, value, 64)
        w.u64(value)
    elif kind == "i64":
        _check_int(kind, value, 64)
        w.i64(value)
    elif kind == "u128":
        _write_u128_parts(w, value)
    elif kind == "i128":
        _write_i128_parts(w, value)
    elif kind == "u256":
        _write_u256_parts(w, value)
    elif kind == "i256":
        _write_i256_parts(w, value)
    elif kind == "bytes":
        w.opaque_var(_as_bytes(value, "bytes"))
    elif kind == "string":
        if not isinstance(value, str):
            raise XdrEncodeError(f"string value must be a str, got {type(value).__name__}")
        w.opaque_var(value.encode("utf-8"))
    elif kind == "symbol":
        if not isinstance(value, str):
            raise XdrEncodeError(f"symbol value must be a str, got {type(value).__name__}")
        encoded = value.encode("utf-8")
        if len(encoded) > SC_SYMBOL_LIMIT:
            raise XdrEncodeError(
                f"symbol {value!r} is {len(encoded)} bytes; SCSYMBOL_LIMIT is {SC_SYMBOL_LIMIT}"
            )
        w.opaque_var(encoded)
    elif kind == "vec":
        _encode_optional_seq(w, value, _encode_vec_body)
    elif kind == "map":
        _encode_optional_seq(w, value, _encode_map_body)
    elif kind == "address":
        if not isinstance(value, dict):
            raise XdrEncodeError("address value must be a dict")
        _write_address(w, value)
    elif kind == "error":
        if not isinstance(value, dict):
            raise XdrEncodeError("error value must be a dict")
        _write_sc_error(w, value)
    elif kind == "contract-instance":
        if not isinstance(value, dict):
            raise XdrEncodeError("contract-instance value must be a dict")
        _write_contract_instance(w, value)
    elif kind == "ledger-key-contract-instance":
        # void arm - no payload
        if value is not None:
            raise XdrEncodeError("ledger-key-contract-instance carries no payload")
    elif kind == "ledger-key-nonce":
        if not isinstance(value, dict):
            raise XdrEncodeError("ledger-key-nonce value must be a dict")
        _write_nonce_key(w, value)
    elif kind == "executable-tag":
        if not isinstance(value, dict):
            raise XdrEncodeError("executable-tag value must be a dict")
        _write_contract_executable(w, value)
    else:  # pragma: no cover - guarded by KIND_TO_CODE above
        raise XdrEncodeError(f"unsupported ScVal kind {kind!r}")

    return bytes(w.buf)


def _encode_optional_seq(w: _Writer, value: Any, encode_body: Any) -> None:
    # `SCVec *vec` / `SCMap *map`: a present flag, then the sequence.
    if value is None:
        w.u32(0)
        return
    w.u32(1)
    encode_body(w, value)


def _encode_vec_body(w: _Writer, items: Any) -> None:
    if not isinstance(items, list):
        raise XdrEncodeError(f"a vec payload must be a list or None, got {type(items).__name__}")
    w.u32(len(items))
    for item in items:
        w.fixed(encode(item))


def _encode_map_body(w: _Writer, entries: Any) -> None:
    if not isinstance(entries, list):
        raise XdrEncodeError(f"a map payload must be a list or None, got {type(entries).__name__}")
    w.u32(len(entries))
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise XdrEncodeError("each map entry must be a (key, value) pair")
        w.fixed(encode(entry[0]))
        w.fixed(encode(entry[1]))


def _read_present_flag(r: _Reader, label: str) -> bool:
    """Read the pointer flag in front of an optional sequence.

    ``SCVec *vec`` and ``SCMap *map`` decode through stellar-xdr's
    ``impl<T: ReadXdr> ReadXdr for Option<T>``, which accepts only 0 (None) and
    1 (Some) and returns ``Error::Invalid`` for every other word. Treating any
    nonzero word as "present" would silently accept corrupt input.
    """
    flag = r.u32()
    if flag == 0:
        return False
    if flag == 1:
        return True
    raise XdrDecodeError(f"{label} present flag must be 0 or 1, got {flag}")


def _decode_scval(r: _Reader) -> tuple[str, Any]:
    code = r.u32()
    kind = CODE_TO_KIND.get(code)
    if kind is None:
        raise XdrDecodeError(
            f"unsupported ScVal discriminant {code}; this codec implements "
            f"{sorted(KIND_TO_CODE.values())}"
        )

    if kind == "bool":
        # stellar-xdr's `impl ReadXdr for bool` is `let b = i == 1`, so every
        # word other than 1 decodes to false instead of being rejected. This
        # mirrors that leniency deliberately: a stricter decoder would reject
        # payloads the reference implementation accepts, which is the exact
        # divergence a parity suite exists to catch.
        return kind, r.u32() == 1
    if kind == "void":
        return kind, None
    if kind == "u32":
        return kind, r.u32()
    if kind == "i32":
        return kind, r.i32()
    if kind in ("u64", "timepoint", "duration"):
        return kind, r.u64()
    if kind == "i64":
        return kind, r.i64()
    if kind == "u128":
        hi, lo = r.u64(), r.u64()
        return kind, (hi << 64) | lo
    if kind == "i128":
        hi, lo = r.i64(), r.u64()
        return kind, hi * (1 << 64) + lo
    if kind == "u256":
        parts = [r.u64() for _ in range(4)]
        value = 0
        for part in parts:
            value = (value << 64) | part
        return kind, value
    if kind == "i256":
        hi_hi = r.i64()
        rest = [r.u64() for _ in range(3)]
        value = hi_hi * (1 << 192)
        for index, part in enumerate(rest):
            value += part << (128 - 64 * index)
        return kind, value
    if kind == "bytes":
        return kind, r.opaque_var()
    if kind == "string":
        payload = r.opaque_var()
        try:
            return kind, payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise XdrDecodeError(f"SCV_STRING payload is not valid UTF-8: {exc}") from exc
    if kind == "symbol":
        payload = r.opaque_var(SC_SYMBOL_LIMIT)
        try:
            return kind, payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise XdrDecodeError(f"SCV_SYMBOL payload is not valid UTF-8: {exc}") from exc
    if kind == "vec":
        if not _read_present_flag(r, "SCVec"):
            return kind, None
        count = r.u32()
        return kind, [_decode_scval(r) for _ in range(count)]
    if kind == "map":
        if not _read_present_flag(r, "SCMap"):
            return kind, None
        count = r.u32()
        return kind, [(_decode_scval(r), _decode_scval(r)) for _ in range(count)]
    if kind == "address":
        return kind, _decode_address(r)
    raise XdrDecodeError(f"unsupported ScVal discriminant {code}")  # pragma: no cover


def _decode_address(r: _Reader) -> dict:
    address_type = r.u32()
    if address_type == SC_ADDRESS_TYPE_ACCOUNT:
        key_type = r.u32()
        if key_type != KEY_TYPE_ED25519:
            raise XdrDecodeError(
                f"unsupported PublicKeyType {key_type}; only KEY_TYPE_ED25519 (0) is covered"
            )
        return {"type": "account", "ed25519": r.take(32)}
    if address_type == SC_ADDRESS_TYPE_CONTRACT:
        return {"type": "contract", "contract_id": r.take(32)}
    raise XdrDecodeError(
        f"unsupported SCAddressType {address_type}; this codec covers account (0) and contract (1)"
    )


def _decode_sc_error(r: _Reader) -> dict:
    """Decode an SCError union value."""
    error_type = r.u32()
    if error_type == SCE_OK:
        return {"type": "ok"}
    type_map = {
        SCE_TYPE: "type",
        SCE_VALUE: "value",
        SCE_ACCESS: "access",
        SCE_BUDGET: "budget",
        SCE_CONTEXT: "context",
        SCE_CONTRACT: "contract",
        SCE_WASM_VM: "wasm-vm",
        SCE_STORAGE: "storage",
        SCE_CRYPTO: "crypto",
    }
    if error_type not in type_map:
        raise XdrDecodeError(f"unsupported SCErrorType discriminant {error_type}")
    code = r.u32()
    return {"type": type_map[error_type], "code": code}


def _decode_contract_executable(r: _Reader) -> dict:
    """Decode a ContractExecutable union value."""
    exec_type = r.u32()
    if exec_type == CONTRACT_EXECUTABLE_WASM:
        return {"type": "wasm", "wasm_hash": r.take(32).hex()}
    if exec_type == CONTRACT_EXECUTABLE_STELLAR_ASSET:
        return {"type": "stellar-asset"}
    if exec_type == CONTRACT_EXECUTABLE_EXTERNAL_REF:
        # owner (SCAddress) + tag (string)
        owner = _decode_address(r)
        tag_len = r.u32()
        if tag_len > r.remaining:
            raise XdrDecodeError("external-ref tag length exceeds remaining bytes")
        tag_bytes = r.take(tag_len)
        pad = (-tag_len) % 4
        if pad:
            padding = r.take(pad)
            if padding != b"\x00" * pad:
                raise XdrDecodeError("non-zero padding after external-ref tag")
        try:
            tag = tag_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise XdrDecodeError(f"external-ref tag is not valid UTF-8: {exc}") from exc
        return {"type": "external-ref", "owner": owner, "tag": tag}
    raise XdrDecodeError(f"unsupported ContractExecutableType discriminant {exec_type}")


def _decode_contract_instance(r: _Reader) -> dict:
    """Decode an SCContractInstance struct."""
    executable = _decode_contract_executable(r)
    # storage (optional)
    flag = r.u32()
    if flag == 0:
        storage = None
    elif flag == 1:
        storage = _decode_contract_executable(r)
    else:
        raise XdrDecodeError(f"contract instance storage flag must be 0 or 1, got {flag}")
    return {"executable": executable, "storage": storage}


def _decode_nonce_key(r: _Reader) -> dict:
    """Decode an SCNonceKey struct."""
    address = _decode_address(r)
    nonce = r.u64()
    return {"address": address, "nonce": nonce}


def _decode_scval(r: _Reader) -> tuple[str, Any]:
    code = r.u32()
    kind = CODE_TO_KIND.get(code)
    if kind is None:
        raise XdrDecodeError(
            f"unsupported ScVal discriminant {code}; this codec implements "
            f"{sorted(KIND_TO_CODE.values())}"
        )

    if kind == "bool":
        # stellar-xdr's `impl ReadXdr for bool` is `let b = i == 1`, so every
        # word other than 1 decodes to false instead of being rejected. This
        # mirrors that leniency deliberately: a stricter decoder would reject
        # payloads the reference implementation accepts, which is the exact
        # divergence a parity suite exists to catch.
        return kind, r.u32() == 1
    if kind == "void":
        return kind, None
    if kind == "u32":
        return kind, r.u32()
    if kind == "i32":
        return kind, r.i32()
    if kind in ("u64", "timepoint", "duration"):
        return kind, r.u64()
    if kind == "i64":
        return kind, r.i64()
    if kind == "u128":
        hi, lo = r.u64(), r.u64()
        return kind, (hi << 64) | lo
    if kind == "i128":
        hi, lo = r.i64(), r.u64()
        return kind, hi * (1 << 64) + lo
    if kind == "u256":
        parts = [r.u64() for _ in range(4)]
        value = 0
        for part in parts:
            value = (value << 64) | part
        return kind, value
    if kind == "i256":
        hi_hi = r.i64()
        rest = [r.u64() for _ in range(3)]
        value = hi_hi * (1 << 192)
        for index, part in enumerate(rest):
            value += part << (128 - 64 * index)
        return kind, value
    if kind == "bytes":
        return kind, r.opaque_var()
    if kind == "string":
        payload = r.opaque_var()
        try:
            return kind, payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise XdrDecodeError(f"SCV_STRING payload is not valid UTF-8: {exc}") from exc
    if kind == "symbol":
        payload = r.opaque_var(SC_SYMBOL_LIMIT)
        try:
            return kind, payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise XdrDecodeError(f"SCV_SYMBOL payload is not valid UTF-8: {exc}") from exc
    if kind == "vec":
        if not _read_present_flag(r, "SCVec"):
            return kind, None
        count = r.u32()
        return kind, [_decode_scval(r) for _ in range(count)]
    if kind == "map":
        if not _read_present_flag(r, "SCMap"):
            return kind, None
        count = r.u32()
        return kind, [(_decode_scval(r), _decode_scval(r)) for _ in range(count)]
    if kind == "address":
        return kind, _decode_address(r)
    if kind == "error":
        return kind, _decode_sc_error(r)
    if kind == "contract-instance":
        return kind, _decode_contract_instance(r)
    if kind == "ledger-key-contract-instance":
        # void arm - no payload
        return kind, None
    if kind == "ledger-key-nonce":
        return kind, _decode_nonce_key(r)
    if kind == "executable-tag":
        return kind, _decode_contract_executable(r)
    raise XdrDecodeError(f"unsupported ScVal discriminant {code}")  # pragma: no cover


def decode(data: bytes) -> tuple[str, Any]:
    """Decode exactly one ``ScVal`` from ``data``, rejecting trailing bytes."""
    r = _Reader(data)
    scval = _decode_scval(r)
    if r.remaining:
        raise XdrDecodeError(f"{r.remaining} trailing byte(s) after a complete ScVal")
    return scval


def decode_base64(text: str) -> tuple[str, Any]:
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise XdrDecodeError(f"vector is not valid base64: {exc}") from exc
    return decode(raw)


def encode_base64(scval: tuple[str, Any]) -> str:
    return base64.b64encode(encode(scval)).decode("ascii")


def to_json(scval: tuple[str, Any]) -> dict:
    """Convert a decoded ``ScVal`` into a JSON-serialisable dict."""
    kind, value = scval
    return {"type": kind, "value": _value_to_json(kind, value)}


def _value_to_json(kind: str, value: Any) -> Any:
    if kind == "bytes":
        return value.hex()
    if kind == "vec":
        return None if value is None else [to_json(item) for item in value]
    if kind == "map":
        if value is None:
            return None
        return [{"key": to_json(k), "val": to_json(v)} for k, v in value]
    if kind == "address":
        if value["type"] == "account":
            return {"type": "account", "ed25519": value["ed25519"].hex()}
        return {"type": "contract", "contract_id": value["contract_id"].hex()}
    if kind == "error":
        if value["type"] == "ok":
            return {"type": "ok"}
        return {"type": value["type"], "code": value["code"]}
    if kind == "contract-instance":
        return {
            "executable": _contract_executable_to_json(value["executable"]),
            "storage": None if value["storage"] is None else _contract_executable_to_json(value["storage"]),
        }
    if kind == "ledger-key-contract-instance":
        return None
    if kind == "ledger-key-nonce":
        return {
            "address": _value_to_json("address", value["address"]),
            "nonce": value["nonce"],
        }
    if kind == "executable-tag":
        return _contract_executable_to_json(value)
    return value


def _contract_executable_to_json(executable: dict) -> dict:
    """Convert a ContractExecutable dict to JSON-serializable form."""
    exec_type = executable.get("type")
    if exec_type == "wasm":
        return {"type": "wasm", "wasm_hash": executable["wasm_hash"]}
    if exec_type == "stellar-asset":
        return {"type": "stellar-asset"}
    if exec_type == "external-ref":
        return {
            "type": "external-ref",
            "owner": _value_to_json("address", executable["owner"]),
            "tag": executable["tag"],
        }
    raise XdrEncodeError(f"unsupported ContractExecutable type {exec_type!r}")


def from_json(obj: dict) -> tuple[str, Any]:
    """Inverse of :func:`to_json`."""
    kind = obj["type"]
    return kind, _value_from_json(kind, obj["value"])


def _value_from_json(kind: str, value: Any) -> Any:
    if kind == "void":
        return None
    if kind == "bytes":
        return bytes.fromhex(value)
    if kind == "vec":
        return None if value is None else [from_json(item) for item in value]
    if kind == "map":
        if value is None:
            return None
        return [(from_json(e["key"]), from_json(e["val"])) for e in value]
    if kind == "address":
        if value["type"] == "account":
            return {"type": "account", "ed25519": bytes.fromhex(value["ed25519"])}
        return {"type": "contract", "contract_id": bytes.fromhex(value["contract_id"])}
    if kind == "error":
        if value["type"] == "ok":
            return {"type": "ok"}
        return {"type": value["type"], "code": value["code"]}
    if kind == "contract-instance":
        return {
            "executable": _contract_executable_from_json(value["executable"]),
            "storage": None if value["storage"] is None else _contract_executable_from_json(value["storage"]),
        }
    if kind == "ledger-key-contract-instance":
        return None
    if kind == "ledger-key-nonce":
        return {
            "address": _value_from_json("address", value["address"]),
            "nonce": value["nonce"],
        }
    if kind == "executable-tag":
        return _contract_executable_from_json(value)
    return value


def _contract_executable_from_json(executable: dict) -> dict:
    """Convert a JSON ContractExecutable back to internal dict form."""
    exec_type = executable.get("type")
    if exec_type == "wasm":
        return {"type": "wasm", "wasm_hash": executable["wasm_hash"]}
    if exec_type == "stellar-asset":
        return {"type": "stellar-asset"}
    if exec_type == "external-ref":
        return {
            "type": "external-ref",
            "owner": _value_from_json("address", executable["owner"]),
            "tag": executable["tag"],
        }
    raise XdrEncodeError(f"unsupported ContractExecutable type {exec_type!r}")

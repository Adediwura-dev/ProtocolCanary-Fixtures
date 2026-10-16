# Fixture conformance vectors (issue [#283](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/283))

This directory is shared test data for implementations that parse Protocol
Canary fixture files. It exercises the repository's Python structural
validator; Rust and TypeScript consumers can use the same TOML files as parser
inputs.

## Layout and outcomes

`vectors.json` is the machine-readable index. Every case names one fixture,
its surface, and its expected outcome. Accepted cases have no errors. Rejected
cases name an `error_code` and a diagnostic fragment; the test harness requires
both a non-zero validation result and that diagnostic.

The codes are stable labels owned by this vector corpus, not codes emitted by
`tools/validate/validate.py` (which currently reports diagnostic messages).
The message fragments tie each label to the actual validator behavior without
changing that existing interface. A rejected vector's fragment must be a
prefix of the message its code documents, so a vector cannot claim an error
class it does not actually produce.

| ID | Surface | Expected outcome |
|---|---|---|
| `valid-xdr-decode-success` | XDR | Accept |
| `valid-xdr-roundtrip` | XDR | Accept |
| `valid-xdr-encode-equals` | XDR | Accept |
| `valid-xdr-decode-failure` | XDR | Accept; the fixture expects a downstream XDR decode failure |
| `valid-xdr-oversized-payload` | XDR | Accept a 1 KiB inline base64 payload; no size limit is defined by this validator |
| `valid-rpc-field-exists` | RPC | Accept |
| `valid-rpc-field-type` | RPC | Accept |
| `valid-rpc-field-equals` | RPC | Accept |
| `valid-rpc-field-absent` | RPC | Accept |
| `valid-soroban-simulation-success` | Soroban | Accept |
| `valid-soroban-simulation-error` | Soroban | Accept |
| `valid-soroban-empty-args-boundary` | Soroban | Accept empty args and sequence number zero |
| `invalid-xdr-toml-syntax` | XDR | Reject: `invalid-toml` |
| `invalid-xdr-missing-type` | XDR | Reject: `missing-required-field` |
| `invalid-xdr-base64` | XDR | Reject: `invalid-base64` |
| `invalid-xdr-encode-equals-missing-expected` | XDR | Reject: `missing-conditional-field` |
| `invalid-rpc-missing-method` | RPC | Reject: `missing-required-field` |
| `invalid-rpc-unsupported-method` | RPC | Reject: `unsupported-rpc-method` |
| `invalid-rpc-empty-assertions` | RPC | Reject: `missing-rpc-assertions` |
| `invalid-rpc-assert-missing-field` | RPC | Reject: `missing-rpc-assert-field` |
| `invalid-soroban-missing-expect` | Soroban | Reject: `missing-required-field` |
| `invalid-soroban-sequence-type` | Soroban | Reject: `wrong-field-type` |
| `invalid-soroban-unknown-expect-key` | Soroban | Reject: `unknown-soroban-expect-key` |
| `invalid-soroban-message-type` | Soroban | Reject: `wrong-soroban-message-type` |

The corpus deliberately does not treat an unknown top-level key as invalid:
the fixture contract permits those. The unknown-key negative vector is inside
Soroban's `[expect]` table, where the upstream parser rejects unknown keys.

## What these vectors do and do not assert

They assert **fixture-format** behavior only: whether a parser accepts or
rejects a fixture file, and, when it rejects one, which diagnostic class it
reports. Nothing here is executed, decoded against a network, or checked for
protocol semantics, and these files are not members of any protocol pack.

That matters for two of the cases in particular:

- The XDR `value_base64` values are deliberately tiny or repetitive placeholders
  (`AAAAAA==`, a 1 KiB run) chosen so the oversized-payload case is about the
  parser's handling of a large inline field, not about a decodable
  `StellarValue`. A conforming fixture *parser* accepts the file; an XDR
  *decoder* would reject the payload, which is the correct outcome for a
  `decode-failure` case and irrelevant for the others. Do not read these bytes
  as a worked example of a protocol encoding — the pack fixtures in
  `protocol-28/` are where verified encodings live.
- The boundary vector uses `protocol = 1` and `sequence_number = 0` because
  those are the validator's own lower boundaries, not because a Protocol 1
  fixture is meaningful here.

The oversized case documents the *validator*: it defines no inline-payload size
limit, so a 1 KiB value is accepted. Pack-level limits (bytes per file, files
per pack) are the registry generator's business, under CF-02, and are not
re-tested here.

## Run

```bash
python3 -m unittest tests/test_conformance_vectors.py
```

The test runner reads this directory directly and invokes the same
`validate_directory` implementation used by the command-line validator. The
vectors are not in `protocol-*`, so they are not counted as consumable protocol
fixtures or included in the fixture-count badge; the suite asserts both, and
that no vector id collides with a shipped fixture id.
# Shared contracts

The interfaces this repository shares with `Protocol-Canary` and
`ProtocolCanary-Action` are defined once, in
[`Protocol-Canary/docs/contracts/`](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/README.md).
This page lists what each contract asks of this repository. It does not repeat
the rules; read the linked contract before implementing anything.

Most of the work below does not exist yet. Until a unit is implemented, the
current behavior is the one described in the README and `CONTRIBUTING.md`.
Implemented so far: the shared conformance vectors (`tests/vectors/cf-02/`,
checked by `tests/test_cf02_vectors.py`), which is CF-02-A; the registry JSON
Schema; and the registry generator (`tools/registry/generate.py`,
`make registry`), which is CF-02-B and CF-02-C. The generated
`registry-v1.json` is not committed yet: that, and the CI freshness check that
goes with it, is CF-02-E. Also still open: CF-02-D (the registry validator
rules), CF-02-F (the Rust pack walker and digest that must match CF-02-A) and
CF-02-H (`.gitattributes` pinning LF for pack files).

| Contract | What this repository owns |
|---|---|
| [CF-02 Registry and pack digest](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-02-registry-and-digest.md) | The registry generator, the registry JSON Schema, the validator rules and the CI freshness check. Python must agree byte for byte with the Rust implementation in the engine; the shared conformance vectors (CF-02-A) live in [`tests/vectors/cf-02/`](../tests/vectors/cf-02/). |
| [CF-04 Verification evidence](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-04-verification-evidence.md) | Evidence records for fixtures, the freshness report, and the Protocol 29 boundary text. No Protocol 29 fixture is added without live evidence recorded under that contract. |
| [CF-06 Canonical fixture releases](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-06-fixture-releases.md) | The deterministic archive builder, the release manifest, the release workflow and the immutability rules. The existing `protocol-28` tag keeps its meaning. |
| [CF-07 Project detection](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-07-project-roots-and-detection.md) | Only the `required_capabilities` vocabulary: it must match the engine's `Capability` set, enforced by a cross-language test. |

## Portable paths and safe text

Two repository-wide rules apply to every pack, independent of which protocol
or surface a fixture targets:

- **Portable path names (F-049).** Every pack-relative path segment must be a
  name Windows can create, because the engine extracts packs on Windows as
  well as on Linux and macOS. A segment may not be a reserved device name
  (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`) in any letter
  case or with any extension, may not end in a dot or a space, and may not
  contain `<`, `>`, `:`, `"`, `|`, `?` or `*`. The rule lives in
  [`tools/validate/portable_names.py`](../tools/validate/portable_names.py)
  and is called by the validator, the registry generator and the archive
  verifier so the three tools cannot disagree. The engine's own extractor
  does not yet list Windows device names in its rule set; that remains an
  engine-side decision and is not changed from this repository.
- **Safe text (F-048).** Fixture TOML files and verification evidence JSON
  read through `--evidence` must be valid UTF-8 and may not contain
  bidirectional override or isolate characters (U+202A-U+202E and
  U+2066-U+2069), zero-width or other `Cf` format characters, a byte order
  mark anywhere except the first character, or `Cc` control characters other
  than tab, line feed and carriage return. The validator reports the file,
  1-based line, 1-based column and code point in `U+XXXX` form. It never
  rewrites or strips a file: the contributor fixes the source.

## Facts about this repository that the contracts rely on

Checked against `main` at `828b41c` on 2026-10-09:

- `protocol-28/` holds 7 fixtures (4 XDR, 2 RPC, 1 Soroban); `protocol-27/` is
  intentionally empty.
- The GitHub release `protocol-28` has no uploaded assets and its text describes
  5 fixtures.
- `p28-rpc-network` and `p28-rpc-latest-ledger` assert `protocolVersion = 28`. On
  2026-10-09 the public Testnet endpoint reported protocol 29, and those two
  fixtures failed. CF-04 lists the options and leaves the decision to the
  maintainers.
- Verification dates exist only as prose in fixture header comments and in
  `docs/protocol-28.md`.

## Vocabulary the validator mirrors from the engine

The validator and schema are structural mirrors, not independent
specifications; the authoritative lists live in `Protocol-Canary`. Three of
them are closed vocabularies that contributors hit first:

- **XDR `type` names (F-050).** `tools/validate/validate.py` accepts exactly
  the five names `crates/canary-xdr/src/decoder.rs` decodes — `StellarValue`,
  `ContractExecutable`, `ScVal`, `TransactionEnvelope`, `LedgerEntry` — as
  read at Protocol-Canary commit
  `9b3fefcacde3de03b3cb34bfea4d47124ebc4d82`.
- **Soroban `[[args]]` (F-051).** Each entry is one closed `{ kind, value }`
  table. The accepted `kind` values (`bool`, `u32`, `i32`, `u64`, `i64`,
  `i128`, `u128`, `i256`, `u256`, `void`, `duration`, `timepoint`, `symbol`,
  `string`, `bytes`, `address`, `vec`, `map`) and the value shape for each
  mirror `parse_arg_at` in `crates/canary-soroban/src/runner.rs`. Nested
  `vec`/`map` containers are limited to `MAX_ARG_DEPTH = 8`, the constant in
  `crates/canary-soroban/src/builder.rs`; `void` is the only kind that may
  omit `value`.
- **RPC `[[assert]]` assertion kinds (F-080).** `tools/validate/validate.py`
  and `schemas/fixture-v1.schema.json` accept `field-exists`, `field-absent`,
  `field-equals`, `field-type` and `field-number-range`, mirroring
  `crates/canary-rpc/src/runner.rs` (read at Protocol-Canary commit
  `4418690`) and contract `ca-01-rpc-requests-and-assertions.md`.
  `field-number-range` accepts `field`, and at least one of `min` and `max`
  (integers or finite floats, with `min <= max`).

`tools/validate/schema_sync.py` keeps the validator and
[`schemas/fixture-v1.schema.json`](../schemas/fixture-v1.schema.json) in
agreement on these lists, and `tests/test_validate.py` pins both, so a
vocabulary change has to land in the validator, the schema and the tests in
one change.

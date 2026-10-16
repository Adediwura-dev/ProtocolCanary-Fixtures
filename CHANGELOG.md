# Changelog

All notable changes to this project are documented in this file.

Every entry below links to the pull request that introduced it — or, for
changes that were pushed directly to `main` without a pull request, to the
introducing commit — so each line can be traced back to its review
discussion and diff. New entries must include that link; see
[CONTRIBUTING.md](CONTRIBUTING.md#updating-changelogmd).

## [Unreleased]

### Added

- Repository scaffold: `schemas/`, `tools/validate/`, `tests/`, `docs/`,
  CI validation workflow.
  ([757e1e7], [5142110], [764111c], [806549c], [c8d8e75])
- `schemas/fixture-v1.schema.json` documenting the fixture format
  implemented by `StellarCanary/Protocol-Canary`'s `canary-fixtures` crate.
  ([5142110])
- `tools/validate/validate.py`: structural validator (schema conformance,
  unique IDs, protocol/surface enums, source references, referenced-file
  existence). No live-network or execution behavior. ([764111c])
- Protocol 28 compatibility pack (`protocol-28/`): ([c5b882c])
  - `p28-xdr-cap83-empty-tx-set` — CAP-0083 `StellarValue`
    (`STELLAR_VALUE_EMPTY_TX_SET`) round-trip, built with the official
    `stellar-xdr` 28.0.0 crate. ([2b3d07b])
  - `p28-xdr-cap83-empty-tx-set-malformed` — a truncated encoding of the
    same CAP-0083 `StellarValue` shape is correctly rejected, mirroring the
    CAP-0085 malformed-input fixture. ([PR #97])
  - `p28-xdr-cap85-external-ref-roundtrip` and
    `p28-xdr-cap85-external-ref-malformed` — CAP-0085
    `ContractExecutable` (`CONTRACT_EXECUTABLE_EXTERNAL_REF`) round-trip
    and malformed-input rejection. ([3f097d1])
  - `p28-rpc-network` — Protocol 28 `getNetwork` identity check, verified
    live against `soroban-testnet.stellar.org`. ([0f54e20])
  - `p28-rpc-latest-ledger` — Protocol 28 `getLatestLedger` identity check
    (a numeric `sequence`, `protocolVersion` 28, a string ledger `id`, and
    no `getNetwork`-only fields), verified live against
    `soroban-testnet.stellar.org` with `stellar-canary` 0.1.1.
    ([PR #236])
  - `p28-soroban-native-asset-name` — a Soroban simulation smoke fixture
    (SEP-41 `name()` on the reserved native-asset contract), verified live
    against `soroban-testnet.stellar.org`. ([ea8b63b])
- `docs/protocol-28.md` documenting exactly what this pack checks, what it
  does not, and why. ([806549c])
- A fixture-count badge in README.md's badge row, plus `tools/badge/badge.py`,
  a stdlib-only generator/checker that counts fixtures using the same
  discovery rule as the validator and rewrites only the marked region of
  README.md. `make badge` regenerates it; CI runs `--check`, so the number
  cannot silently drift from the fixture tree. ([ab88ca4])
- `tools/validate/schema_sync.py`, a standard-library-only check that fails
  when `schemas/fixture-v1.schema.json`'s enums or required-field lists drift
  from `tools/validate/validate.py`'s constants, with coverage in
  `tests/test_validate.py`. `schemas/fixture-v1.schema.json` now also lists
  `source_reference` as required, matching the validator. ([PR #231])
- `tests/test_documentation_vocabulary.py`, which parses README.md's
  assertion-vocabulary table and CONTRIBUTING.md's per-surface field table and
  asserts they name exactly the RPC methods, assertion kinds and surfaces
  `tools/validate/validate.py` accepts — the documentation leg
  `schema_sync.py` does not cover, and the drift that issue #162 was about.
  ([PR #236])
- `tests/scval_xdr.py`, `tests/vectors/scval_parity/scval_parity.json` and
  `tests/test_scval_parity.py`: a standard-library-only XDR codec for the
  `ScVal` union, 36 golden vectors, and 29 tests asserting **bit-for-bit**
  roundtrip identity (not merely value equality). The vectors were derived
  field by field from `Stellar-contract.x` at `stellar/stellar-xdr` tag
  `v28.0` (commit `9c9c145`) independently of the codec, so the suite
  compares two derivations rather than checking a codec against its own
  output. Coverage spans integers (`u32`/`i32`/`u64`/`i64` and all four
  128/256-bit hi/lo forms), bytes, addresses (both `SCAddress` arms),
  vectors, maps and the remaining primitive variants; the five `ScVal`
  variants this repository has no use for are rejected on decode rather
  than silently skipped. Where the XDR text leaves strictness open the
  codec follows `stellar-xdr`'s generated `ReadXdr` implementations,
  because parity means agreeing with the reference implementation. Closes
  [issue #304](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/304).
  ([PR #308])
- Four `protocol-28/soroban/` fixtures asserting the failure side of the
  construct → `simulateTransaction` → result pipeline:
  `p28-soroban-sim-error-unknown-function` (`Error(Value, InvalidInput)`),
  `p28-soroban-sim-error-missing-contract` (`Error(Storage, MissingValue)`),
  `p28-soroban-sim-error-arg-count` (`Error(Context, UnexpectedSize)`) and
  `p28-soroban-sim-error-arg-type` (`Error(Value, UnexpectedType)`). Each
  `(type, code)` pair is a real `SCErrorType`/`SCErrorCode` member of
  `Stellar-contract.x` at `v28.0`, observed live against
  `soroban-testnet.stellar.org` on 2026-10-15 (UTC); only that stable form
  is pinned, never the host's diagnostic-event wording. The observations
  were bootstrapped by a `name()`/`decimals()` positive control that
  reproduced the values `p28-soroban-native-asset-name` records from
  2026-09-02, and the method is written up in `docs/protocol-28.md`.
  ([PR #308])
- `tests/test_sim_error_fixtures.py` (21 tests): derives the rendered
  host-error spellings from the `SCErrorType`/`SCErrorCode` enum names and
  checks every fixture's `message_contains` against them, so an invented
  error name cannot be committed; verifies the suite spans distinct codes
  and at least three host subsystems; pins the determinism rules (reserved
  or provably absent contracts only, the unfunded all-zero source account,
  no live-varying state); and covers the validator's `[expect]` rejections.
  ([PR #308])
- `tests/vectors/conformance/` and `tests/test_conformance_vectors.py`: the
  cross-language fixture parser conformance corpus (F-021) — 24 vectors, 12
  accepted and 12 rejected, covering the XDR, RPC and Soroban surfaces and
  indexed by `vectors.json`. Every rejected vector names an error code and a
  diagnostic fragment, and the harness requires the fragment to be both a
  prefix of that code's documented message and present in the validator's
  actual report, so a vector cannot claim an error class it does not produce;
  the rejected half covers missing required and conditional fields, wrong field
  types, an unknown key inside soroban's closed `[expect]` table, an
  unsupported RPC method, malformed TOML and a non-base64 payload, while the
  accepted half covers every assertion kind plus a 1 KiB inline payload and the
  validator's lower boundaries (`protocol = 1`, `sequence_number = 0`, empty
  `args`). The vectors assert fixture-format behavior only — the XDR bodies are
  deliberately placeholder values and nothing is executed or checked against a
  network — and the harness pins the corpus outside every `protocol-*/` scan
  root, so the fixture-count badge cannot move. Closes
  [issue #283](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/283).
  ([PR #315])

### Changed

- `tools/validate/validate.py` and `schemas/fixture-v1.schema.json` now
  reject a soroban `[expect]` table containing a key other than `kind` or
  `message_contains`, a non-string `message_contains`, and an empty one.
  This closes a real divergence rather than adding a new rule:
  `canary-soroban` calls `unknown_body_keys(expect, ["kind",
  "message_contains"])` and reads the needle with
  `.and_then(toml::Value::as_str)`, so an unknown key is a loader error and
  a non-string is **silently dropped** — degrading the fixture to "the
  simulation failed" while it still looks like it pins a message. An empty
  needle matches every message for the same reason. The field remains
  genuinely optional, matching the loader's `Option<String>`;
  `test_message_contains_is_optional` pins that so it is not "fixed" into a
  divergence later. ([PR #308])

- The structural validator now rejects an empty-string `description`,
  matching `schemas/fixture-v1.schema.json`'s `minLength: 1` and the
  existing `id`/`category` checks; a regression test covers the case.
  ([PR #232])
- Documented that `protocol` is intentionally unbounded above: a code
  comment in `tools/validate/validate.py` and a new "Protocol version
  range" subsection in `CONTRIBUTING.md` state that a stray or typo'd value
  is expected to be caught by pack-level tests rather than by structural
  validation. ([PR #232])
- `schemas/fixture-v1.schema.json`'s RPC `assert.value` property gained a
  description explaining that its type is intentionally unconstrained but
  must match the JSON type the targeted RPC field actually returns.
  ([PR #232])
- README.md's Validation section now distinguishes the structural
  conformance CI re-checks on every run from the point-in-time live-network
  verification recorded in fixture header comments and `docs/protocol-28.md`.
  ([PR #232])
- The structural validator now treats a missing `source_reference` as an
  error rather than a warning, so a fixture with no authoritative provenance
  reference fails `tools/validate/validate.py` (and therefore CI) instead of
  passing with a printed advisory. `CONTRIBUTING.md`, `SECURITY.md`,
  `README.md` and `schemas/fixture-v1.schema.json` were updated to describe
  the rule as enforced. ([PR #143])
- Documented the reciprocal RPC type-list maintenance relationship, linked
  the malformed-input review guidance into the fixture walkthrough, and
  recorded the current CAP-0085 CLI status and tracked deployment gap.
  ([PR #176])
- `schemas/fixture-v1.schema.json` gained a top-level `examples` array
  containing one minimal, schema-valid XDR fixture mirroring
  `protocol-28/xdr/cap-0085/p28-xdr-cap85-external-ref-roundtrip.toml`,
  so schema-aware editors (e.g. Even Better TOML/Taplo) can offer a
  worked completion example.
- `protocol-27/README.md` now links its "contribution policy" reference
  directly to `CONTRIBUTING.md`, where the pack-population verification
  policy is spelled out.
- `.github/PULL_REQUEST_TEMPLATE.md`'s "New or changed fixture(s)?" section
  now links directly to `CONTRIBUTING.md`'s "Adding a fixture" checklist
  alongside the README's Provenance reference, so PR authors are pointed at
  the fuller step-by-step walkthrough (source, determinism, ID stability)
  that the template's review questions map to. ([PR #219])
- README.md's fixture-format section states which RPC methods the `rpc`
  surface currently supports (`get-network` and `get-latest-ledger`) and
  links `CONTRIBUTING.md` for adding a new one. ([PR #231])
- `CONTRIBUTING.md`'s fixture-schema section documents that `--protocol`
  filtering skips, rather than fails, fixtures whose `protocol` does not
  match, cross-referencing `schemas/fixture-v1.schema.json`, and describes
  the schema/validator sync enforcement added above. ([PR #231])
- `docs/protocol-28.md` records why an RPC fixture can only assert fields
  `canary-rpc` exposes to assertions: each response is serialized into its
  typed model (`NetworkInfo`, `LatestLedger`) before asserts run, so
  `getLatestLedger`'s raw `closeTime`/`headerXdr`/`metadataXdr` are not
  observable, and a `field-absent` assertion is meaningful only for a field
  the other method would report. ([PR #236])
- `.github/PULL_REQUEST_TEMPLATE.md`'s "Tests performed" checklist now
  includes `python3 tools/badge/badge.py --check`, the CI step that fails a
  fixture PR whose badge was not regenerated. ([PR #236])
- `tests/test_validate.py`'s invalid-RPC-method test now asserts the
  rejection message quotes the rejected method and enumerates the accepted
  ones, for both `get-balance` and `getTransactions`, instead of only
  checking that some error mentioned `method`. ([PR #236])
- `schemas/fixture-v1.schema.json`'s `category` description now states that
  `tools/validate/validate.py` rejects the vague values `misc`, `other`,
  `test` and `general` as errors, in any letter case, instead of only
  advising against them; a new test pins the mixed-case rejection.
  ([PR #252])

### Known gaps

- **CAP-0086 is not covered.** CAP-0086 (sparse-map host functions) has no
  corresponding top-level XDR type — testing it for real requires a
  deployed Soroban contract that calls
  `sparse_map_new_from_linear_memory`/`sparse_map_unpack_to_linear_memory`.
  As of this release, the latest published `soroban-sdk` (27.0.6) does not
  expose these host functions, so no such contract can be built and
  verified without hand-crafting the host-function ABI — which this
  project's no-guessing rule forbids. See `docs/protocol-28.md` for
  details and what would need to be true upstream before this gap can be
  closed. ([757e1e7])
- **CAP-0085 Soroban-level (not just XDR-level) behavior is not covered.**
  The XDR fixtures above prove the wire representation round-trips; they
  do not exercise an actual deployed externally-managed-executable
  contract fleet end-to-end, which would require deploying and verifying a
  real Protocol 28 contract using this brand-new executable type. The Rust
  `stellar` CLI **27.1.0** installed while authoring this pack had no
  supported external-reference deployment flow. The current **28.0.0**
  release can resolve and invoke existing external references, but its
  `stellar contract deploy` command still has no direct
  `--executable-owner`/`--executable-tag` construction option; the draft
  [`stellar-cli` PR #2659](https://github.com/stellar/stellar-cli/pull/2659)
  is the upstream candidate to unblock that gap. The exact commands,
  re-check date, and distinction between reference resolution and direct
  deployment are recorded in [`docs/protocol-28.md`](docs/protocol-28.md) and
  tracked by [issue #8](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/8).
  ([757e1e7])
- **Soroban simulation `auth` and resource-estimation assertions are not
  covered.** [Issue #300](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/300)
  (F-043) asks for fixtures asserting on the `auth` array a
  `simulateTransaction` response carries, and
  [issue #301](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/301)
  (F-044) for fixtures asserting on `minResourceFee`/`transactionData`.
  Neither is expressible in the fixture contract this repository mirrors:
  read against `StellarCanary/Protocol-Canary` at `2a88756`,
  `SimulationResponse` has no `auth` field at all, `SorobanAssertion` has
  exactly two variants (`SimulationSuccess`, `SimulationError`) and rejects
  any other `expect.kind` or any extra `[expect]` key, and
  `min_resource_fee`/`transaction_data` are deserialized but read by no
  production code. Extending this repository's schema anyway would invert
  CONTRIBUTING.md's ownership rule — the schema mirrors the loader, never
  leads — and would break every consumer, because `canary-runner`'s
  scheduler calls `SorobanFixture::from_loaded` with `?`, so one unparseable
  fixture aborts the whole run as exit 4 rather than being skipped.
  `required_capabilities` cannot gate it either: `Capability` is a closed
  five-member enum with no member meaning auth or resource estimation. The
  gap closes once upstream
  [#517](https://github.com/StellarCanary/Protocol-Canary/issues/517)
  (E-119, open) and an equivalent resource-estimation assertion land and
  are released. See `docs/protocol-28.md`, "Soroban simulation auth and
  resource estimation". ([PR #308])
- **Contract panics and contract-defined error codes are not covered by the
  simulation-error suite.** `SCErrorType`'s tenth member, `SCE_CONTRACT`,
  carries a user-defined `uint32` rather than an `SCErrorCode`, and a WASM
  trap produces `Error(WasmVm, …)` from code this repository does not ship;
  reaching either needs a deployed contract, which "What never belongs in a
  fixture" rules out. `test_no_fixture_claims_a_contract_defined_error`
  guards the gap so it cannot be closed by fabrication. ([PR #308])
- `protocol-27/` is intentionally empty; see `protocol-27/README.md`.
  ([757e1e7])

### Upstream dependency change

- `StellarCanary/Protocol-Canary`'s `canary-xdr` crate gained
  `ContractExecutable` decode/encode support (previously only
  `StellarValue` was supported), so that the CAP-0085 fixtures above are
  actually runnable rather than merely well-formed TOML. See that
  repository's own changelog for the corresponding entry. ([757e1e7])

<!-- Link definitions: the pull request or commit that introduced each
     entry above. Commits listed here were pushed directly to `main`
     without a pull request; PR #97, PR #143, PR #176, PR #219, PR #231,
     PR #232, PR #236, PR #252, PR #308 and PR #315 are the [Unreleased]
     entries that originated from pull requests. -->

[757e1e7]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/757e1e777489bb5c20e7500b245370de227c66b3
[5142110]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/51421106999811666502b2e7da7ae3b9e351fd9c
[764111c]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/764111cb2fecc02a0a300feeecf46c9e38c379a0
[c5b882c]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/c5b882c347a1913bf4c035ce51b7950232a964b6
[2b3d07b]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/2b3d07b32923b0274170f106575915ad85fc2251
[3f097d1]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/3f097d176b1bcf80c3c8fe255015d2b7ee0a12ff
[0f54e20]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/0f54e20410747e669d8797c825b698cdc0d19d5d
[ea8b63b]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/ea8b63b5eb46118b8475568c2332f9d43f571ee2
[c8d8e75]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/c8d8e75b741b35873e8b5074fac6f8321bc94197
[806549c]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/806549c1ab8f21dbfd44d5091eb899b776ea5767
[ab88ca4]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/ab88ca4ee03e4e189c003402fe68aa8430411d00
[PR #97]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/97
[PR #143]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/143
[PR #176]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/176
[PR #219]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/219
[PR #231]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/231
[PR #232]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/232
[PR #236]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/236
[PR #252]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/252
[PR #308]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/308
[PR #315]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/315

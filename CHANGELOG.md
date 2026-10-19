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

- `tools/validate/validate.py` validates verification evidence records
  (CF-04 section 3, `verificationVersion` 1): `validate_evidence(record)`
  and `--evidence PATH`, which takes a record file or a directory of
  `*.json` records. Missing or unknown fields, a hash that is not 64
  lowercase hex characters, a `checkedAt` that is not a UTC instant, a
  `live-*` record without `network` (name, passphrase and
  `observedProtocol`), `endpointHost` or `evidenceRef`, and a record that
  names a fixture that does not exist or whose hash is not the current
  file's, are errors. Nothing is scanned unless `--evidence` is given.
  ([PR #309])
- `tools/pack/pack.py` and `make pack VERSION=...`: the deterministic
  `protocolcanary-fixtures-VERSION.tar.gz` for a pack (CF-06 section 2):
  entries sorted in UTF-8 byte order, uid and gid 0 with empty names,
  mtime 0, mode 0644 for files and 0755 for directories, a gzip header
  with no name or time, the pack membership plus `registry.json`, and the
  D-06 limits (2,000 entries, 16 MiB, 1 MiB per file, depth 8). Building
  twice gives the same bytes. ([PR #309])
- `tools/release/manifest.py` and `schemas/release-manifest-v1.schema.json`:
  the release manifest for an archive (CF-06 section 3), with the archive
  hash, byte size, entry count, uncompressed size and `packDigest`
  recomputed from the archived files. Compatible engines are exact
  versions recorded as unverified. ([PR #309])
- `tools/pack/verify_archive.py`: extracts an archive under the CF-06
  section 6 safe-extraction rules into a temporary directory, checks the
  registry and pack digest, runs the validator over the contents and
  removes the directory on every path. Exit code 0 valid, 1 invalid, 2
  unreadable. ([PR #309])
- `tools/registry/generate.py`, the generator for a pack's `registry-v1.json`
  (CF-02-B): every fixture and referenced payload hashed with SHA-256 over
  the raw bytes, fixtures sorted by `id` and payloads by `path`, and the
  `packDigest`, validated against `schemas/registry-v1.schema.json` before it
  is written. An invalid pack (symbolic link, escaping payload, names that
  differ only by case) fails without writing anything. `make registry`
  writes `PACK/registry-v1.json`; the generated file is not committed yet
  (CF-02-E). Adds `tools/validate/jsonschema_lite.py`, a standard-library
  JSON Schema validator that rejects any keyword it does not implement.
  ([PR #306])
- `upstream-matrix.json` and `schemas/upstream-matrix.json`: a machine-readable
  map from Protocol 28 and 29 to the semver range of the Stellar Core,
  `soroban-rpc` and `rs-soroban-env` releases that belong to each. ([PR #306])
- `docs/upstream-matrix.md`, linked from the README: the Stellar Core, Stellar
  RPC, Stellar Horizon and Soroban host environment release and commit for
  Protocol 28 and 29 on Testnet and Mainnet, and the network passphrases,
  with `tests/test_upstream_matrix_docs.py` keeping the page and the matrix
  in agreement. ([PR #306])
- `tests/fixtures/capabilities.json`: the canonical `required_capabilities`
  vocabulary (the 5 approved capability strings, CF-07 section 9), pinned by
  `tests/test_validate.py` against `CAPABILITIES` in
  `tools/validate/validate.py` and the schema's enum, with a positive test
  for every approved capability and negative tests for unapproved strings
  (e.g. `fake-cap`). Engine-side parity is tracked in
  [Protocol-Canary#512](https://github.com/StellarCanary/Protocol-Canary/issues/512).
  <!-- TODO: switch to the PR link once this PR is opened. -->
  ([#288])
- `protocol-29/` baseline pack scaffold: `registry-v1.json` declaring
  `registryVersion: 1`, `protocol: 29` (integer) and the empty
  `fixtures: []` array, with `rpc/`, `xdr/` and `soroban/` subdirectories,
  and `tests/test_protocol29_baseline.py` asserting the manifest against
  `schemas/registry-v1.schema.json`, the canonical CF-02 section 3/5
  encoding and `packDigest`, and that the validator accepts the new
  directory without diagnostics. The empty baseline is a directory
  scaffold only, never reportable as compatibility coverage;
  `protocol-28/` is untouched.
  ([PR #318])

- Six `protocol-28/xdr/stellar-value/` fixtures covering the two
  previously untested `StellarValue.ext` arms,
  `STELLAR_VALUE_BASIC` and `STELLAR_VALUE_SIGNED`, so a decoder that
  handles only the CAP-0083 `STELLAR_VALUE_EMPTY_TX_SET` arm can no
  longer pass this pack: `p28-xdr-stellar-value-basic-roundtrip`,
  `p28-xdr-stellar-value-basic-decode-success` and
  `p28-xdr-stellar-value-basic-decode-failure`, plus
  `p28-xdr-stellar-value-signed-roundtrip`,
  `p28-xdr-stellar-value-signed-decode-success` and
  `p28-xdr-stellar-value-signed-decode-failure`. Every payload was
  produced by the official `stellar-xdr` 28.0.1 crate (features
  `base64`, `alloc` — `rustc 1.99.1`, generated 2026-10-19 UTC) via a
  recorded construction expression, never hand-assembled; the two
  decode-failure payloads are documented truncations of those
  crate-produced values that the same crate's `from_xdr_base64`
  rejects. The signed variant's node-id and signature bytes are
  labelled synthetic structural values in the headers; no key material
  that could be real is used, and nothing claims a network
  observation. Each header cites `Stellar-ledger.x`/`Stellar-types.x`
  at `stellar/stellar-xdr` v28.0 (commit `9c9c145`) with file and line
  ranges. Closes
  [issue #358](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/358).
  <!-- TODO: switch to the PR link once this PR is opened. -->
  ([#358])

- `protocol-29/evidence/testnet/`: read-only live Testnet evidence corpus
  (F-034, CF-04-H) — raw JSON-RPC captures of `getNetwork`,
  `getLatestLedger` and `getVersionInfo` taken 2026-10-18 UTC from
  `soroban-testnet.stellar.org` (all three report `protocolVersion` 29),
  stored as data only per CF-04 section 7, plus
  `tests/test_protocol29_evidence.py` enforcing the closed capture shape,
  RFC 3339 UTC instants, the endpoint allowlist, the read-only method
  guard and a no-credentials scan. The corpus establishes the evidence
  references required before any Protocol 29 fixture is authored.
  ([PR #319])


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
- `source_reference` must now be a CAP identifier (`CAP-0083`) or an absolute
  `https://` URL, and a `github.com` or `raw.githubusercontent.com` link must
  name a full commit SHA or a release tag, not a branch. Free text, relative
  links, `http://` and URLs with credentials are rejected, with a message
  that says what to change. The same pattern is in
  `schemas/fixture-v1.schema.json`, and `schema_sync.py` fails when the two
  differ. The four CAP-0083 and CAP-0085 fixtures that cited
  `.../blob/master/...` now cite commit `9cd7030`, whose files are identical;
  no fixture ID or expected value changed. ([PR #306])
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
- `tools/validate/validate.py` and `schemas/fixture-v1.schema.json` now
  reject unknown keys in a fixture (F-031): an undeclared top-level field
  (a typo such as `descriptoin`), a key that belongs to another surface,
  and a misspelled key inside an `[[assert]]` table are errors, with the
  diagnostic naming the offending key. The schema sets
  `additionalProperties: false` on every object and declares the union of
  the common fields and all surface keys at the top level so the closure
  does not reject valid fixtures. The test that pinned the previous
  permissive behavior and `CONTRIBUTING.md`'s "Unknown top-level fields"
  section were updated to match.
  ([PR #316])

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
     PR #232, PR #236, PR #252, PR #306, PR #308, PR #309 and PR #315 are the
     [Unreleased] entries that originated from pull requests. -->

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
[PR #306]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/306
[PR #308]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/308
[PR #309]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/309
[PR #315]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/315
[PR #316]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/316
[PR #318]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/318
[PR #319]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/319
[#358]: https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/358

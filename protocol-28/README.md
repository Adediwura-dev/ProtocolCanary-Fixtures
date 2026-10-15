# Protocol 28 compatibility pack

This pack contains **11 fixtures**.

Implemented in Stellar Core 28.0.0, Stellar RPC 28.0.0 (integration-tested
against Soroban host 28.0.1), and the official `stellar-xdr` 28.0.0 crate.
Includes CAP-0083, CAP-0085, and CAP-0086.

This pack's fixtures are written against **fixture format
`schema_version` 1** ([`schemas/fixture-v1.schema.json`](../schemas/fixture-v1.schema.json)).
See [`../docs/protocol-28.md`](../docs/protocol-28.md#fixture-format-version)
for details, and [`../CONTRIBUTING.md`](../CONTRIBUTING.md#fixture-schema)
for why future packs should record their target version too.

This directory holds no `manifest.toml` — see the repository root
[`README.md`](../README.md#repository-relationship) for why: the
`Protocol-Canary` loader parses every `*.toml` file it finds as a fixture,
so a separate enumeration file in this format would either be ignored or,
worse, misparsed as a broken fixture. The fixture files below *are* the
enumeration.

| Fixture | Surface | CAP | What it proves |
|---|---|---|---|
| [`xdr/cap-0083/p28-xdr-cap83-empty-tx-set.toml`](xdr/cap-0083/p28-xdr-cap83-empty-tx-set.toml) | xdr | CAP-0083 | A `StellarValue` using `STELLAR_VALUE_EMPTY_TX_SET` round-trips byte-for-byte. |
| [`xdr/cap-0083/p28-xdr-cap83-empty-tx-set-malformed.toml`](xdr/cap-0083/p28-xdr-cap83-empty-tx-set-malformed.toml) | xdr | CAP-0083 | A truncated `StellarValue` using `STELLAR_VALUE_EMPTY_TX_SET` is correctly rejected, not silently accepted. |
| [`xdr/cap-0085/p28-xdr-cap85-external-ref-roundtrip.toml`](xdr/cap-0085/p28-xdr-cap85-external-ref-roundtrip.toml) | xdr | CAP-0085 | A `ContractExecutable` using `CONTRACT_EXECUTABLE_EXTERNAL_REF` round-trips byte-for-byte. |
| [`xdr/cap-0085/p28-xdr-cap85-external-ref-malformed.toml`](xdr/cap-0085/p28-xdr-cap85-external-ref-malformed.toml) | xdr | CAP-0085 | A truncated `ContractExecutable::ExternalRef` encoding is correctly rejected, not silently accepted. |
| [`rpc/p28-rpc-network.toml`](rpc/p28-rpc-network.toml) | rpc | — | A configured RPC endpoint's `getNetwork` reports protocol 28 with a `passphrase` field. |
| [`rpc/p28-rpc-latest-ledger.toml`](rpc/p28-rpc-latest-ledger.toml) | rpc | — | The same endpoint's `getLatestLedger` reports protocol 28, a numeric `sequence`, a string ledger `id`, and no `getNetwork`-only fields. |
| [`soroban/p28-soroban-native-asset-name.toml`](soroban/p28-soroban-native-asset-name.toml) | soroban | — | The full construct → `simulateTransaction` → result pipeline works against real Protocol 28 infrastructure. |
| [`soroban/p28-soroban-sim-error-unknown-function.toml`](soroban/p28-soroban-sim-error-unknown-function.toml) | soroban | — | Invoking a function the Stellar Asset Contract does not export fails with `Error(Value, InvalidInput)` in `result.error`. |
| [`soroban/p28-soroban-sim-error-missing-contract.toml`](soroban/p28-soroban-sim-error-missing-contract.toml) | soroban | — | A well-formed contract id with no stored instance fails with `Error(Storage, MissingValue)` rather than being treated as an empty contract. |
| [`soroban/p28-soroban-sim-error-arg-count.toml`](soroban/p28-soroban-sim-error-arg-count.toml) | soroban | — | Calling SEP-41 `transfer()` with zero arguments instead of three fails with `Error(Context, UnexpectedSize)`. |
| [`soroban/p28-soroban-sim-error-arg-type.toml`](soroban/p28-soroban-sim-error-arg-type.toml) | soroban | — | Calling SEP-41 `transfer()` with symbols in both address positions fails with `Error(Value, UnexpectedType)`. |

> **Column legend** — *Fixture*: the fixture file, linked to its path inside
> this pack (the fixture ID is the file's name without the `.toml`
> extension). *Surface*: the Protocol-Canary checker the fixture targets —
> `xdr` (decode/encode, via `canary-xdr`), `rpc` (a read-only JSON-RPC
> call, via `canary-rpc`), or `soroban` (a simulated contract invocation,
> via `canary-soroban`) — and matches the fixture's own `surface` field.
> *CAP*: the Stellar Core Advancement Proposal number the fixture is
> specific to, or `—` when it asserts general Protocol 28 behavior rather
> than one CAP. *What it proves*: the single concrete assertion it makes.

See [`../docs/protocol-28.md`](../docs/protocol-28.md) for what this pack
checks, what it deliberately does not (including why CAP-0086 has no
fixture yet, and why there are no simulation-`auth` or resource-estimation
fixtures), and source provenance for every assertion above.

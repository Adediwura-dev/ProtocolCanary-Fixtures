# Staged resource-estimation fixtures (issue #301)

These three files are **staged fixtures**, not fixtures. They are complete,
review-ready assertions about `simulateTransaction`'s resource estimate that
this repository cannot ship yet, held here so the work behind them is visible
and reviewable now instead of being redone when the prerequisite lands.

They use `expect.kind = "simulation-resource-estimate"`, which does not exist.
At `StellarCanary/Protocol-Canary` commit
`4d2cb9978127ecd1045b4cca022a4681875d0052`, `SorobanAssertion`
(`crates/canary-soroban/src/runner.rs`) has exactly two variants —
`SimulationSuccess` and `SimulationError { message_contains }` — and rejects any
other kind. [`CONTRIBUTING.md`](../../../CONTRIBUTING.md#fixture-schema) makes
that loader authoritative and permits an unsupported assertion here "only … once
that support exists and is released".

Shipping them anyway would not merely omit a check. `canary-runner`'s scheduler
calls `SorobanFixture::from_loaded` with `?`, so a single fixture the loader
cannot parse aborts the **entire** run as an invalid-fixture error (exit 4)
rather than being skipped — every consumer of this pack would break. `required_capabilities`
cannot rescue them either: `Capability` is a closed five-member enum with no
member meaning "resource estimation", so borrowing one would misdescribe the
fixture to get it skipped.

## Why this directory, and not `protocol-28/soroban/`

Both `tools/validate/validate.py` and `tools/badge/badge.py` scan only
`protocol-*/` directories, so nothing here is parsed as a fixture, counted in
README.md's badge, or fed to the loader. `python3 -m unittest discover tests`
looks for `test*.py`, so these `.toml` files are not collected either.
[`tests/test_sim_resource_fixtures.py`](../../test_sim_resource_fixtures.py) is
what validates them — including a guard asserting that nothing under
`protocol-*/` uses the unsupported kind.

This mirrors the existing
[`tests/vectors/scval_parity/`](../scval_parity/) precedent: verified data that
CI checks offline, kept out of the consumable corpus.

## What is here

| File | Function invoked | What it asserts |
|---|---|---|
| `p28-soroban-sim-resource-min-fee-positive.toml` | `name()` | The estimate is present, positive, and within the XDR widths that carry it (`instructions` uint32, `resourceFee` int64). |
| `p28-soroban-sim-resource-fee-matches-transaction-data.toml` | `decimals()` | `SorobanTransactionData.resourceFee` inside `transactionData` equals the response's `minResourceFee`. |
| `p28-soroban-sim-resource-pure-read-footprint.toml` | `symbol()` | A read-only accessor is estimated as read-only: one-entry `readOnly` footprint naming the invoked contract, empty `readWrite`, zero `diskReadBytes` and `writeBytes`. |

All three invoke the reserved, network-wide testnet Stellar Asset Contract for
native XLM from the all-zero unfunded account — the same deterministic setup
[`p28-soroban-native-asset-name`](../../../protocol-28/soroban/p28-soroban-native-asset-name.toml)
already uses. Every observation was made on **2026-10-16 UTC** against
`https://soroban-testnet.stellar.org` at ledger 5216960 and is recorded verbatim
in each file's `[staged_observation]` table, which the test suite decodes
byte-exactly offline. The endpoint reported `protocolVersion` 29 while this pack
targets protocol 28; see
[`docs/protocol-28.md`](../../../docs/protocol-28.md#live-networks-that-report-a-newer-protocol).

## The proposed assertion vocabulary

[`schemas/proposed/simulation-resource-estimate.expect.json`](../../../schemas/proposed/simulation-resource-estimate.expect.json)
defines the `[expect]` block these fixtures use. It is deliberately **not**
referenced from [`schemas/fixture-v1.schema.json`](../../../schemas/fixture-v1.schema.json),
which must keep mirroring the released loader. Every numeric bound in it is an
XDR field width from
[`stellar-xdr` v28.0](https://github.com/stellar/stellar-xdr/blob/9c9c145953e80990d6ff1ae3a6a973a0ce6d0694/Stellar-transaction.x),
not a guessed "reasonable" number.

Note that fees and instruction counts are **live-varying**, so the vocabulary is
bounds-shaped rather than equality-shaped; the one equality it offers,
`resource_fee_matches_min_resource_fee`, compares two fields of the same
response and so cannot go stale.

## Promotion criteria

Move these files into `protocol-28/soroban/`, merge the proposed fragment into
`schemas/fixture-v1.schema.json`, extend `SOROBAN_EXPECT_KINDS` /
`SOROBAN_EXPECT_FIELDS` in `tools/validate/validate.py`, refresh
`EXPECTED_IDS_BY_SURFACE` in `tests/test_pack_protocol_28.py` and README.md's
badge — but only once **all** of the following hold:

1. `canary-soroban`'s `SorobanAssertion` has a resource-estimation variant in a
   **released** `Protocol-Canary` version (tracked upstream by
   [#517](https://github.com/StellarCanary/Protocol-Canary/issues/517) and
   [#504](https://github.com/StellarCanary/Protocol-Canary/issues/504));
2. that variant's field names match the proposed fragment, or the fragment is
   corrected to match it — the loader wins;
3. `[staged_observation]` is deleted from each file, since it is a staging
   artifact and not part of the fixture contract.

Until then, see
[`docs/protocol-28.md`](../../../docs/protocol-28.md#soroban-simulation-auth-and-resource-estimation)
for the gap record.

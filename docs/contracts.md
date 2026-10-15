# Shared contracts

The interfaces this repository shares with `Protocol-Canary` and
`ProtocolCanary-Action` are defined once, in
[`Protocol-Canary/docs/contracts/`](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/README.md).
This page lists what each contract asks of this repository. It does not repeat
the rules; read the linked contract before implementing anything.

Most of the work below does not exist yet. Until a unit is implemented, the
current behavior is the one described in the README and `CONTRIBUTING.md`.
Implemented so far: the registry JSON Schema and the registry generator
(`tools/registry/generate.py`, `make registry`), which is CF-02-B and
CF-02-C. The generated `registry-v1.json` is not committed yet: that, and the
CI freshness check that goes with it, is CF-02-E.

| Contract | What this repository owns |
|---|---|
| [CF-02 Registry and pack digest](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-02-registry-and-digest.md) | The registry generator, the registry JSON Schema, the validator rules and the CI freshness check. Python must agree byte for byte with the Rust implementation in the engine; shared conformance vectors live here. |
| [CF-04 Verification evidence](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-04-verification-evidence.md) | Evidence records for fixtures, the freshness report, and the Protocol 29 boundary text. No Protocol 29 fixture is added without live evidence recorded under that contract. |
| [CF-06 Canonical fixture releases](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-06-fixture-releases.md) | The deterministic archive builder, the release manifest, the release workflow and the immutability rules. The existing `protocol-28` tag keeps its meaning. |
| [CF-07 Project detection](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-07-project-roots-and-detection.md) | Only the `required_capabilities` vocabulary: it must match the engine's `Capability` set, enforced by a cross-language test. |

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

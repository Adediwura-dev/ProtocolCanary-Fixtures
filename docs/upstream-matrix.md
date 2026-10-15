# Upstream version matrix

Which upstream releases the Protocol 28 and Protocol 29 fixture baselines
correspond to: the Stellar Core, Stellar RPC, Stellar Horizon and Soroban host
environment versions, the commit each release tag points at, and the network
passphrases a client must use.

This page is for people. The same information as data, for tooling, is in
[`upstream-matrix.json`](../upstream-matrix.json), described by
[`schemas/upstream-matrix.json`](../schemas/upstream-matrix.json). A test
(`tests/test_upstream_matrix_docs.py`) checks that every release on this page
falls inside the range that file records.

## Where this comes from

- **Versions and dates:** the Stellar developer documentation's
  [Software versions](https://developers.stellar.org/docs/networks/software-versions)
  page, read on 2026-10-15. That page is the authority for which release
  belongs to which protocol and network.
- **Commits:** each release tag was resolved to the commit it points at in the
  upstream repository (`git ls-remote`, and the GitHub API to confirm that each
  tag is a lightweight tag, so the commit is the tag's own). The tags carry no
  annotation, so there is no separate tag object to cite.
- **Repository names:** Stellar RPC is `stellar/stellar-rpc` (the project was
  formerly `soroban-rpc`, and `stellar/soroban-rpc` redirects to it). Stellar
  Horizon is `stellar/stellar-horizon`; releases up to `horizon-v24.0.0` live in
  `stellar/go`. The Soroban host environment is `stellar/rs-soroban-env`.

The upstream major version tracks the protocol version: every release listed
here for Protocol N is `N.x`.

## Network passphrases

| Network | Passphrase |
|---|---|
| Testnet | `Test SDF Network ; September 2015` |
| Futurenet | `Test SDF Future Network ; October 2022` |
| Mainnet | `Public Global Stellar Network ; September 2015` |

The passphrase is part of what a transaction signature covers, so a transaction
built for one network is not valid on another. Mainnet is listed for
completeness; the fixtures in this repository are checked against Testnet.

## Protocol 28

Protocol 28 introduces CAP-0083, CAP-0085 and CAP-0086; XDR definitions are
`v28.0`.

### Testnet

Released to Testnet on 2026-08-27.

| Component | Release | Commit |
|---|---|---|
| Stellar Core | `v28.0.0` | [`a9b86132`](https://github.com/stellar/stellar-core/commit/a9b8613218e141ddb89d621e5f04d4c75a149d36) |
| Stellar RPC (`soroban-rpc`) | `v28.0.0` | [`e29c911f`](https://github.com/stellar/stellar-rpc/commit/e29c911f2a720e2bf05028014c00492450547ccc) |
| Stellar Horizon | `v28.0.0` | [`8be4ddda`](https://github.com/stellar/stellar-horizon/commit/8be4ddda1e1cffc1a8f0ad98e37a95c25df45f95) |
| Soroban host environment (`rs-soroban-env`) | `v28.0.1` | [`7a7629cf`](https://github.com/stellar/rs-soroban-env/commit/7a7629cfef0328881497d60089f9b8acf202a495) |

### Mainnet

Released to Mainnet on 2026-09-16.

| Component | Release | Commit |
|---|---|---|
| Stellar Core | `v28.0.1` | [`947aad84`](https://github.com/stellar/stellar-core/commit/947aad8413c189d85504acf72207e85eeda9b021) |
| Stellar RPC (`soroban-rpc`) | `v28.0.1` | [`273f19e4`](https://github.com/stellar/stellar-rpc/commit/273f19e4fcb183b568948bd2b810abfe87150a9c) |
| Stellar Horizon | `v28.0.1` | [`a70eb47f`](https://github.com/stellar/stellar-horizon/commit/a70eb47f76985d372de3e59f4d75c7f8542752f7) |
| Soroban host environment (`rs-soroban-env`) | `v28.0.2` | [`5061e9c4`](https://github.com/stellar/rs-soroban-env/commit/5061e9c40ce6fc74ade9b9b3b49465b0cf7fdccb) |

## Protocol 29

Protocol 29 is a security-focused release that fixes vulnerabilities in Stellar
Core. It introduces no new CAPs, and XDR definitions stay at `v28.0`, so there
is no CAP to cite for a Protocol 29 fixture (see
[`protocol-29-rpc-observations.md`](protocol-29-rpc-observations.md)).

### Testnet

Released to Testnet on 2026-09-29.

| Component | Release | Commit |
|---|---|---|
| Stellar Core | `v29.0.0` | [`a9d72b0c`](https://github.com/stellar/stellar-core/commit/a9d72b0cac3a89ebbcf926af449ac73089e0f8f7) |
| Stellar RPC (`soroban-rpc`) | `v29.0.0` | [`8a401697`](https://github.com/stellar/stellar-rpc/commit/8a40169717723697e430c1e51c21ef0bf244ecda) |
| Stellar Horizon | `v29.0.0` | [`c9e48b64`](https://github.com/stellar/stellar-horizon/commit/c9e48b645f0777f19135109d3de5c46160cea737) |
| Soroban host environment (`rs-soroban-env`) | `v29.0.0` | [`a721944b`](https://github.com/stellar/rs-soroban-env/commit/a721944b5a4ea830a4bf5c2dbb8dbe8c1e1354c5) |

### Mainnet

Released to Mainnet on 2026-10-01. The software is the same as on Testnet.

| Component | Release | Commit |
|---|---|---|
| Stellar Core | `v29.0.0` | [`a9d72b0c`](https://github.com/stellar/stellar-core/commit/a9d72b0cac3a89ebbcf926af449ac73089e0f8f7) |
| Stellar RPC (`soroban-rpc`) | `v29.0.0` | [`8a401697`](https://github.com/stellar/stellar-rpc/commit/8a40169717723697e430c1e51c21ef0bf244ecda) |
| Stellar Horizon | `v29.0.0` | [`c9e48b64`](https://github.com/stellar/stellar-horizon/commit/c9e48b645f0777f19135109d3de5c46160cea737) |
| Soroban host environment (`rs-soroban-env`) | `v29.0.0` | [`a721944b`](https://github.com/stellar/rs-soroban-env/commit/a721944b5a4ea830a4bf5c2dbb8dbe8c1e1354c5) |

## A node's reported build is not always the tag's commit

`getVersionInfo` and `captiveCoreVersion` report the commit a node was built
from, and for Protocol 29 that is not the commit the public tag points at.
On 2026-10-09 both public RPC endpoints in
[`protocol-29-rpc-observations.md`](protocol-29-rpc-observations.md) reported:

| Reported | Value | Relation to the `v29.0.0` tag above |
|---|---|---|
| Stellar RPC `version` | `29.0.0-b2b701685c79aee17fe4eb22dbd08a5dfd11594d` | The commit does not exist in `stellar/stellar-rpc`, so it cannot be compared with the tag. |
| Stellar Core `captiveCoreVersion` | `stellar-core 29.0.0 (4eb83337380a29ad0907f4e32196ce97b8dc7649)` | An ancestor of the tag: the tag `v29.0.0` is one commit after it. |

Compare the release (`29.0.0`), not the commit hash, when checking a node
against this page. The commits above identify what each public release is.

## Refreshing this page

When a new protocol or release ships:

1. Read the Software versions page above and record its date.
2. Resolve each tag to its commit and add it:

   ```bash
   git ls-remote https://github.com/stellar/stellar-core.git refs/tags/v29.0.0
   ```

   Do this for each component's repository listed above.
3. Update [`upstream-matrix.json`](../upstream-matrix.json) and, for a new
   protocol, the `required` list in
   [`schemas/upstream-matrix.json`](../schemas/upstream-matrix.json).
4. Run `python3 -m unittest discover tests`; the documentation test fails when
   a release falls outside the matrix.

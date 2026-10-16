# CF-02 conformance vectors (issue [#286](https://github.com/StellarCanary/ProtocolCanary-Fixtures/issues/286))

Shared conformance data for **CF-02**, the fixture registry and pack digest
contract. Both implementations of that contract — the Python tooling in this
repository and the Rust engine — must agree *byte for byte*, so the vectors
here are the ground truth they are checked against: a small pack directory
tree plus the file hashes, pack digest and canonical registry manifest that
tree must produce.

The contract itself is authoritative and lives upstream:
[`Protocol-Canary/docs/contracts/cf-02-registry-and-digest.md`](https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/cf-02-registry-and-digest.md).
This directory implements its unit **CF-02-A** ("shared conformance vectors: a
small directory tree plus expected file hashes and pack digest, as test data")
and is what CF-02-F, the Rust pack walker and digest, is checked against.
Nothing here restates the rules; read the contract for the reasoning.

## What a vector is

Each vector is three things:

| Path | What it is |
|---|---|
| `<vector>/pack/` | The pack root. Every file under it is a pack file per CF-02 section 2: every `*.toml` at any depth, plus every file a fixture names in `input_file`/`expected_file`. |
| `<vector>/expected.json` | The vector's verified values: every pack file's path, size and SHA-256 in canonical order, the resulting `packDigest`, and the SHA-256 of the golden manifest. |
| `<vector>/expected-registry-v1.json` | The canonical registry manifest the pack must produce, in the byte-exact encoding of CF-02 section 3. |

The digest and the per-file hashes in `expected.json` were computed with
`sha256sum`, `wc -c` and `printf` — not with this repository's generator — and
the generator and the golden manifest are then checked *against* them. The
recipe is reproduced [below](#the-digest-input), so a reviewer can redo it.

## The vectors

| Vector | Fixtures | Pack files | Max directory depth | `packDigest` |
|---|---|---|---|---|
| `minimal` | 1 (`xdr`) | 1 | 0 | `sha256:747ca470959eecf9c9dd70aa33c2e9d686e2a25929a3476c2593f9cd64b0ee05` |
| `standard` | 2 (`rpc`, `xdr`) | 4 | 3 | `sha256:83191069a8cdfab9f1c4d5fc9d1b6695b167c9dc364006e939b36fe6f525de10` |
| `complex` | 4 (`rpc`, `soroban`, `xdr` ×2) | 5 | 5 | `sha256:77eff8c3c5a31534db7943636f7ef4486cc318059745fe7a5400d1916a3bfbba` |

`minimal` is the smallest pack the contract admits. `standard` adds payloads
and the optional registry metadata. `complex` is the pack that separates byte
order from component-wise order and shares one payload between two fixtures.
Each vector's `expected.json` lists, under `exercises`, exactly what that pack
is there to pin.

### `minimal`

One fixture at the pack root, no payloads, no `required_capabilities`. One
registry entry, one digest input line: the header-plus-one-line shape.

### `standard`

Fixture ids sort *opposite* to pack-relative paths inside one pack:
`p28-rpc-cf02-standard-alpha` is first in the registry's `fixtures` array and
last in the digest input, and `p28-xdr-cf02-standard-beta` is the other way
round. It also carries two payloads (named by `input_file` and by
`expected_file`, so the registry records `deep/level1/payloads/...`), an
explicit empty `required_capabilities` (which CF-02 section 3 says must still
appear as `"requiredCapabilities": []`) and a non-empty one.

### `complex`

- **Byte order versus component order.** The pack holds
  `deep/level1/set-b/...` and `deep/level1/set/a/...`. Comparing the paths byte
  by byte stops at the first differing byte — `-` (`0x2d`) in `set-b/` against
  `/` (`0x2f`) in `set/a/` — so `set-b` comes first. Comparing them as
  sequences of path *components* puts `set/a` first, because `set` is a prefix
  of `set-b`. CF-02 section 4 requires the byte comparison, so a walker that
  sorts components (for example a Rust implementation that sorts `PathBuf`
  values with their `Ord`) cannot reproduce this digest.
- **Case in a path.** `ZZ-upper/...` sorts before every lowercase directory
  (`Z` is `0x5a`), so the file that is last by fixture id is first in the
  digest input.
- **A payload shared by two fixtures.** `payloads/shared.bin` is named by two
  fixtures in the same directory, once as `input_file` and once as
  `expected_file`: CF-02 section 4 makes it two `payloads` entries in the
  registry and exactly one line of the digest input.
- **Non-ASCII text.** One fixture's `description` contains an accented Latin
  word and CJK text. The registry is UTF-8, so the golden manifest must carry
  those bytes literally, not as `\u` escapes.
- All three surfaces in one registry.

## The digest input

Per CF-02 section 5, `packDigest` is `sha256:` followed by the lowercase hex
SHA-256 of: the ASCII line `canary-pack-v1` and a newline, then one
`<sha256 hex> <decimal byte length> <pack-relative path>` line per *distinct*
pack file, sorted by path (UTF-8 bytes).

A reviewer can recompute any vector's digest without this repository's code —
this is the same command the values in the table above were produced with:

```bash
cd tests/vectors/cf-02/complex/pack
{ printf 'canary-pack-v1\n'
  find . -type f | sed 's|^\./||' | LC_ALL=C sort | while read -r path; do
      printf '%s %s %s\n' \
        "$(sha256sum "$path" | cut -c1-64)" \
        "$(wc -c < "$path" | tr -d ' ')" \
        "$path"
    done
} | sha256sum
```

`LC_ALL=C` matters: it is what makes `sort` compare UTF-8 *bytes* rather than
the host's collation order.

## Verifying

[`tests/test_cf02_vectors.py`](../../test_cf02_vectors.py) is the test runner.
It re-derives every hash and digest from the files on disk, checks the
reference generator (`tools/registry/generate.py`) against the recorded
values and the golden manifests, and covers the negative cases — an altered
byte, a reordered manifest key and a reordered `fixtures` array must each
change what the contract accepts.

```bash
python3 -m unittest tests/test_cf02_vectors.py
```

## Why the pack paths are ASCII-only

The non-ASCII case is carried in fixture *content*, never in a file name. A
path is compared and hashed as bytes, and two checkouts can disagree about a
non-ASCII name without any code being wrong: macOS file systems normalize
Unicode names (NFC/NFD), so a name that is one byte sequence on one machine can
be another on the next. A vector whose digest depended on that would be a
flaky CI failure rather than a conformance check, so this set exercises byte
ordering with ASCII-only names — case, and `-` against `/`, are enough to
separate the two plausible orderings. The contract's own rule that no two paths
may be equal after Unicode case folding is left to
[`tests/test_registry_generator.py`](../../test_registry_generator.py), which
tests it directly.

## How these files relate to the rest of the repository

They are **not fixtures**. `tools/validate/validate.py` and
`tools/badge/badge.py` both scope discovery to `protocol-*/`, so nothing here
is validated as a fixture, counted in README.md's badge, or loaded by the
engine unless someone points it at this directory deliberately. The pack files
are nonetheless complete, loader-valid fixtures — a conformance pack that the
loader would reject could not stand in for one — and the test suite asserts
that, along with the fact that no vector fixture pins a live-varying value.

This is the same relationship
[`tests/vectors/scval_parity/`](../scval_parity/) and
[`tests/vectors/resource_estimation/`](../resource_estimation/) have to the
consumable corpus: verified data that CI checks offline.

## Changing a vector

Do not regenerate `expected.json` with the code under test and commit the
result — that is exactly the circularity the vectors exist to break. Change the
pack deliberately, recompute the per-file hashes and the digest with the
command above, and update the recorded values only after the new digest
explains itself. If the two implementations then disagree, that disagreement is
the finding: fix the implementation the contract describes, not the vector.

See [`docs/contracts.md`](../../../docs/contracts.md) for where CF-02 stands in
the wider contract set.

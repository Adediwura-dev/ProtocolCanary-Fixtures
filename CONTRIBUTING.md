# Contributing

## Adding a fixture

A fixture is accepted only if a reviewer can answer all of the following
from the fixture file and its header comment alone, without running a
script:

1. What Stellar behavior does this test?
2. Why is that behavior important?
3. What protocol/CAP introduced it?
4. What is the expected result, and where does that expectation come from?
5. Is the test deterministic?

To add one:

**Short version** (the detailed walkthrough follows below):

- **Source it** — identify the upstream behavior from an authoritative source
  you have actually checked, and record it in `source_reference` plus a header
  comment explaining how the expected value was derived.
- **Write it** — stable, repository-unique `p<protocol>-<surface>-<slug>` ID;
  deterministic input; an explicit expected result using the surface's typed
  assertion vocabulary.
- **Check it** — run `make validate` (or `python3 tools/validate/validate.py` directly).
- **Document, test & refresh it** — update the relevant `docs/protocol-NN.md`
  table (and the pack's `README.md` for a new CAP or surface), run
  `make test` (or `python3 -m unittest discover tests` directly), then run
  `make badge` so README.md's fixture-count badge reflects the new fixture.
- **Cover the pack** — if these are the first fixtures in a protocol pack, add
  that pack's dedicated `tests/test_pack_protocol_NN.py` file in the same
  change (see "Pack-level test files" below).

## Recording verification dates

When a fixture's header comment records when a live-network observation was
made (for example, the date an RPC endpoint or a simulation was last checked
to still return the documented result), write the date as **`YYYY-MM-DD` in
UTC** — e.g. `2026-09-02`. State the `UTC` designation the first time a date
appears in a header comment (or otherwise make clear it is UTC).

Rationale: these dates exist so a future reader can judge how stale an
observation may be. Recording them in local time would make a date recorded
near a day boundary ambiguous by up to a day, defeating that purpose.

1. **Identify the upstream behavior.** Read the CAP text, the upstream XDR
   definition, the upstream implementation, or the official release/API
   docs — in that order of preference. Never cite a source you have not
   actually checked describes the specific behavior you are asserting.
2. **Add source provenance.** Every fixture sets `source_reference` to an
   authoritative CAP identifier (`CAP-0083`) or an absolute `https://` URL —
   omitting it is a hard validator error, not a warning, and so is free text
   such as `see issue`, a relative link, `http://`, or a URL with
   credentials. A link into GitHub (`github.com/.../blob/...` or
   `raw.githubusercontent.com/...`) must name a full commit SHA or a release
   tag, not a branch such as `master`, so it keeps describing what the
   fixture was written against; documentation pages have no ref to pin. The
   header comment (a `#`
   comment block above the TOML body) explains, in prose, how the expected
   value was derived or observed — e.g. "built with the official
   `stellar-xdr` 28.0.0 crate against the CAP-0083 `StellarValue` type",
   not "looks right". If the header comment records *when* an observation
   was made, use the [date convention above](#recording-verification-dates).
3. **Define a stable ID.** Follow `p<protocol>-<surface>-<slug>` (e.g.
   `p28-xdr-cap85-external-ref-roundtrip`). IDs are lowercase, unique
   across the *entire* repository (the loader validates this across all
   `*.toml` files under a given `--fixtures-dir`, not just one file), and
   never renamed just because an implementation detail changed — if the
   semantic assertion itself changes, add a new fixture ID instead of
   silently repurposing an old one.
4. **Create deterministic input.** No fixture may depend on ledger state
   that changes between runs (a current ledger sequence, "the latest
   anything") unless the assertion is explicitly scoped as a live-network
   check and documented as such. For an XDR `decode-failure` fixture, follow
   the [malformed-input guidance in `SECURITY.md`](SECURITY.md#parser-safety-in-mind):
   prefer a specific documented rejection, such as truncation or an invalid
   discriminant, over arbitrary fuzzing input, and explain the malformation in
   the fixture's header comment.
5. **Define an explicit expected result** using the assertion vocabulary
   the target surface actually supports (see "Fixture schema" below) — no
   generic string matching when a typed assertion exists.
6. **Validate the fixture**: `make validate` — or
   `python3 tools/validate/validate.py` directly. Only errors produce a
   non-zero exit code: the validator exits 1 if the report contains at
   least one error and 0 otherwise. CI runs this exact command
   (`.github/workflows/validate.yml`), so that exit code is what CI
   treats as pass/fail — any error fails the workflow on your PR. In
   particular, a missing `source_reference` (step 2) is an error, so it
   fails CI rather than passing with a printed advisory. Add
   `--format json` for the same run as a single JSON document on stdout
   (see [README.md](README.md#validation)); the exit codes are unchanged.
7. **Add/update documentation**: the relevant `docs/protocol-NN.md` table
   and, if you added a new CAP or surface, the pack's `README.md`.
8. **Run the repository tests**: `make test` — or
   `python3 -m unittest discover tests` directly.
9. **Refresh the fixture-count badge** in `README.md`: `make badge` — or
   `python3 tools/badge/badge.py` directly. CI runs
   `python3 tools/badge/badge.py --check` and fails if the committed badge
   no longer matches the fixture tree (see
   [README.md](README.md#fixtures-badge)).
10. **If you populated a previously empty pack**, add that pack's
    `tests/test_pack_protocol_NN.py` in this same change — see
    "Pack-level test files" below. A pack-level test is *not* a substitute for
    any of steps 6-9, and `tests/test_validate.py` does not cover it.

Before pushing, `make check` runs all of the above in one command, in
the same order CI (`.github/workflows/validate.yml`) runs them.

> **Note**: Do not add a `manifest.toml` or similar discovery/enumeration file. The loader recursively treats every `*.toml` file under `--fixtures-dir` as a fixture, so a manifest `.toml` file would be mis-parsed as a malformed fixture and fail the run (see [README.md](README.md#repository-relationship)).

## Pack-level test files

Every populated protocol pack ships with a dedicated pack-level test file,
`tests/test_pack_protocol_NN.py`, next to the generic `tests/test_validate.py`.
[`tests/test_pack_protocol_28.py`](tests/test_pack_protocol_28.py) is the
existing example: a structural, offline suite (no network, no fixture
execution) asserting that the pack matches its own documented inventory, for
example that

- every fixture under `protocol-NN/` targets `protocol = NN` and carries a
  `source_reference`;
- the set of fixture IDs per surface matches the pack's documented inventory
  exactly;
- fixture IDs named in the `## [Unreleased]` section of `CHANGELOG.md` still
  exist, or are explicitly marked removed/deprecated;
- the pack's own documented invariants hold (for protocol-28, the "no fixture
  claims CAP-0086" gap guard, and the "protocol-27 is still intentionally
  empty" policy check).

This is separate from `tests/test_validate.py`, which only proves the generic
format rules. The validator cannot know which fixtures a given pack is
*supposed* to contain — the pack-level file is what keeps a pack's
`docs/protocol-NN.md`, its `README.md`, `CHANGELOG.md`, and its actual
`protocol-NN/` directory from drifting apart. (The "Protocol version range"
note in the [Fixture schema](#fixture-schema) section covers the related case
of a mistyped `protocol` value passing structural validation.)

**When you populate a new protocol pack** — for example
[`protocol-27/README.md`](protocol-27/README.md), which anticipates exactly
this future change — add the equivalent `tests/test_pack_protocol_NN.py` in
the same change that adds the pack's first fixtures. Model it on the
protocol-28 file: replace `EXPECTED_IDS_BY_SURFACE` with your pack's verified
inventory, assert `protocol = NN` for every fixture, and drop the invariants
that only make sense for a different or empty pack. No workflow change is
needed — CI already discovers the file through
`python3 -m unittest discover tests`.

No fixture should be merged solely because it makes some consumer's CI
green. If you cannot pin down the exact expected wire representation or
host-function behavior from an authoritative source, **stop** — do not
guess a byte sequence or invent an undocumented host function because it
"looks right". Open an issue describing the gap instead.

## Fixture schema

This repository's fixture files must conform exactly to what
`StellarCanary/Protocol-Canary`'s `canary-fixtures`/`canary-xdr`/
`canary-rpc`/`canary-soroban` crates actually parse — that implementation,
documented in its `docs/fixture-contract.md`, is authoritative. This
repository does not define its own competing schema. `schemas/fixture-v1.schema.json`
here is a convenience JSON Schema mirroring that contract for editor/CI
linting; if the two ever disagree, `Protocol-Canary`'s implementation wins
and this repository's schema/validator must be corrected to match — never
the other way around.

That schema is kept from silently drifting away from
`tools/validate/validate.py` by `tools/validate/schema_sync.py` — a
standard-library-only check (there is no third-party JSON Schema engine in
this repository's dependency set) that compares the schema's enums and
required-field lists against the validator's constants. The repository test
suite runs it against the shipped schema (`tests/test_validate.py`), so a
validator change that adds, removes, or renames a rule — a new XDR `type`, a
new RPC `method`, a newly required field, a new capability, and so on —
fails CI unless the same pull request updates `schemas/fixture-v1.schema.json`
to match. `python3 tools/validate/schema_sync.py` runs the check on its own
and exits non-zero on any disagreement.

The fixture format is versioned: `schemas/fixture-v1.schema.json` is titled
"Protocol Canary fixture (schema_version 1)". **Every protocol pack must
state, in its `docs/protocol-NN.md` or the pack's `README.md`, which fixture
format `schema_version` its fixtures were written against** — the
`protocol-28` pack, for instance, targets `schema_version 1`. The schema
version is a per-pack property recorded in prose, not a field repeated in
each fixture file. Recording it from the start is what lets a future format
revision (e.g. `schema_version 2`) be scoped per pack rather than
retrofitted by guesswork.

### Protocol filtering is skip-not-fail

A fixture's `protocol` field declares which protocol pack it belongs to.
When a consumer runs with a protocol filter — for example
`stellar-canary check --fixtures-dir <checkout> --protocol 28 --json` — every
fixture whose `protocol` does not equal the requested value is **skipped,
not failed**. This matches `schemas/fixture-v1.schema.json`'s `protocol`
description verbatim: "A run whose `--protocol` does not match is skipped,
not failed."

Once this repository holds more than one populated protocol pack (see the
`protocol-27/` pack, expected to be populated eventually), that has a
practical consequence for CI: a run scoped with `--protocol` over the whole
repository reports fewer fixtures than the tree contains, and a mostly
skipped run is **not** a validation failure. Do not read a low fixture count
under `--protocol` filtering as something being wrong with the fixtures.
`tools/validate/validate.py` itself does not filter by protocol — it
validates every fixture it finds — so use it (or `make validate`) when you
want the whole corpus checked regardless of pack.

Common fields (every fixture):

```toml
id = "unique-string"
protocol = 28
surface = "xdr" # | "rpc" | "soroban"
category = "cap-0083"
description = "..."
source_reference = "CAP-0083"          # required for protocol-specific fixtures; missing it is an error
required_capabilities = []              # optional, see fixture-contract.md
input_file = "..."                      # optional, path relative to this file (currently unused by any fixture)
expected_file = "..."                   # optional (currently unused by any fixture)
```

`input_file` and `expected_file` are the external-file alternatives to an
XDR fixture's inline `value_base64`/`expected_base64` fields: a fixture
supplies a given value one way or the other, not both. Only the referenced
file's *existence* is checked by the validator — never its contents. Prefer
an external file for large payloads (e.g. a multi-kilobyte binary blob that
would make the `*.toml` unreadable) and inline base64 for short XDR values,
which is what every fixture in this repository currently does.

### Protocol version range

`protocol` is **intentionally unbounded above**. The validator checks only
that it is an integer `>= 1`; it deliberately does not maintain a list of
supported Stellar protocol versions, so shipping a new protocol never
requires a validator change here. The trade-off is that a stray or typo'd
value (for example `82` where `28` was meant) passes structural validation
silently. Catching that is a pack-level responsibility, not the validator's:
a pack's own test suite asserts that every fixture under `protocol-NN/`
targets `NN` — see `tests/test_pack_protocol_28.py`'s
`test_every_fixture_targets_protocol_28` — so a mistyped `protocol` is caught
there, and a new pack should add the equivalent test.

Per-surface body (everything else in the file):

| Surface | Fields |
|---|---|
| `xdr` | `type` (currently `"StellarValue"` or `"ContractExecutable"`), `kind` (`"decode-success"` \| `"decode-failure"` \| `"roundtrip"` \| `"encode-equals"`), `value_base64`, `expected_base64` (only for `encode-equals`) |
| `rpc` | `method` (`"get-network"` \| `"get-latest-ledger"`), one or more `[[assert]]` tables (`{kind, field, value?, expected_type?}`) |
| `soroban` | `source_account`, `contract_id`, `function`, `sequence_number`, optional `[[args]]`, `[expect]` (`{kind = "simulation-success"}` or `{kind = "simulation-error", message_contains?}`) |

### Unknown top-level fields

Both `tools/validate/validate.py` and `schemas/fixture-v1.schema.json`
reject an unrecognized top-level field (F-031): the top level of a fixture
is closed to the union of the common fields plus exactly the keys its own
`surface` consumes, and the schema sets `additionalProperties: false` on
every object, including the `[[assert]]` tables and the `[expect]` table.
The validator's diagnostic names the offending key
(`unexpected top-level key 'soure_reference'; ...`), so a typo'd field name
fails loudly instead of being silently ignored while the intended field is
reported missing.

Introducing a genuinely new fixture field therefore needs the key added to
`SURFACE_FIELDS`/`COMMON_OPTIONAL_FIELDS` in `tools/validate/validate.py`
and to `properties` in `schemas/fixture-v1.schema.json` in the same change
that first uses it — and, as before, the loader contract in
`Protocol-Canary`'s `docs/fixture-contract.md` stays authoritative: add the
field upstream first, never here alone.

### Protocol filtering behavior

The `protocol` field specifies the Stellar protocol version targeted by the fixture. In accordance with [`schemas/fixture-v1.schema.json`](schemas/fixture-v1.schema.json) (which notes that *"A run whose `--protocol` does not match is skipped, not failed"*), runners filtering fixtures via `--protocol <version>` silently skip fixtures targeting other protocol versions rather than failing the run. When this repository contains fixtures across multiple protocol packs (e.g. `protocol-27/` and `protocol-28/`), executing a run with `--protocol` filtering skips fixtures for other versions by design; this skip-not-fail behavior is normal and should not be misconstrued as a validation failure.

If you need an XDR `type` this repository does not yet support, that is a
`Protocol-Canary` limitation, not something to work around here — open an
issue/PR against `Protocol-Canary`'s `canary-xdr` crate first (see its own
`CONTRIBUTING.md`), and only add the fixture here once that support exists
and is released.

The same applies to an RPC `method` outside the `get-network` |
`get-latest-ledger` pair the table above lists: open an issue/PR against
`Protocol-Canary`'s `canary-rpc` crate first, and only add the fixture here
once that support exists and is released.

## What never belongs in a fixture

- Shell commands, scripts, or any embedded JavaScript/Python/Bash/Rust —
  fixtures are declarative data, never code. There is no `exec`,
  `shell_command`, `pre_run`, or `post_run` field, and none will ever be
  added.
- Private keys, seed phrases, or funded-account secrets.
- A request for `Protocol-Canary` to submit a real, state-changing
  transaction. Fixtures may require simulation, decoding/encoding, or a
  read-only RPC call — never submission.
- A claim about current live network state (a specific ledger sequence, a
  specific balance) unless the fixture is explicitly and narrowly scoped as
  a live-network check with its assumptions documented.
- A `manifest.toml` or any discovery/enumeration file. The loader recursively
  parses every `*.toml` file under `--fixtures-dir` as a fixture, so a manifest
  file would be mis-parsed as a malformed fixture and fail validation (see
  [README.md](README.md#repository-relationship)).

## Deprecating a fixture

Do not silently delete a fixture that is still referenced by a released
`Protocol-Canary` version's tests or documentation. Mark it deprecated in
its header comment with the reason, note it in `CHANGELOG.md`, and remove
it in a later, separate change once nothing depends on it.

Note that deprecation is communicated **only** via this header comment
convention. There is intentionally no `deprecated` field or equivalent
structured marker in the schema. This is by design: deprecation is meant
for humans reading the fixture or reviewing a PR, while automated consumers
parsing the file via the schema treat it like any other fixture until it is
fully removed.

## Common validation errors

Quick reference for what contributors hit most often. Each line is the error as
`make validate` reports it, followed by the usual cause and fix.

| Error | Cause and fix |
|---|---|
| `ModuleNotFoundError: No module named 'tomllib'` | Your `python3` is older than 3.11. `tomllib` is a 3.11+ stdlib module and the validator has no third-party dependencies, so there is nothing to install — use a newer interpreter (CI runs 3.11.16). |
| `no 'source_reference' set; ...` | The fixture omits `source_reference`. Add it: a CAP id (`CAP-0083`) or an authoritative URL such as the RPC method reference. It is an **error**, not a warning. |
| `field 'category' is too vague ('misc'); use a specific CAP/topic slug` | `category` is one of `misc`, `other`, `test`, `general`. Use a specific CAP or topic slug instead, e.g. `cap-0083` or `network`. |
| `field 'source_reference' links to GitHub ref 'master', which is a branch name and moves; pin ...` | The link follows a branch. Replace the branch with the full commit SHA of the revision you checked (`.../blob/<40-hex commit>/...`) or a release tag. |
| `field 'source_reference' must be a CAP identifier such as 'CAP-0083' or an absolute https:// URL ...` | The value is free text or a relative link. Use `CAP-0083`-style identifiers, or the full `https://` URL of the page you checked. |
| `duplicate fixture id '...': already defined in ...` | Two fixtures share an `id`. Ids must be unique across the whole tree — rename the new one. |
| `unexpected key(s) '...' in 'expect'; canary-soroban accepts only ['kind', 'message_contains']` | Unlike the top level of a fixture, `[expect]` is closed: `canary-soroban` rejects an unknown key there. Usually a typo (`message_contain`) or a field that belongs at the top level. |
| `expect.message_contains must be a string, got ...` / `expect.message_contains must not be empty; ...` | The loader reads the field with `toml::Value::as_str`, so a non-string is silently dropped and an empty string matches every message — either way the fixture asserts less than it appears to. Quote the needle, or omit the key entirely to assert only that the simulation fails. |
| `README.md's fixtures badge is stale; run ...` | You added or removed a `*.toml`, so the fixture count in README.md is now wrong. Run `make badge` and commit the result. |
| `missing required field '...'` with no obvious cause | Historically a typo'd duplicate of the intended field (e.g. `soure_reference`). Unknown keys are now rejected outright and named — see [Unknown top-level fields](#unknown-top-level-fields). |

`make check` runs these in CI's order (`validate` → `badge-check` → `test`) and
stops at the first failure, so fix and re-run after each change rather than
chasing several reported errors at once.

## Updating CHANGELOG.md

Every user-visible change — a new fixture, a validator behavior change, new
tooling — gets an entry under `CHANGELOG.md`'s `## [Unreleased]` section in
the same pull request that makes the change.

Each entry must include a link to the pull request that introduced it, so a
reader can jump straight from the changelog line to the review discussion
and the diff:

```markdown
- `p28-xdr-example` — what the fixture checks.
  ([PR #123](https://github.com/StellarCanary/ProtocolCanary-Fixtures/pull/123))
```

If a change is pushed directly to `main` without a pull request, link the
introducing commit instead:

```markdown
- Something else. ([abc1234](https://github.com/StellarCanary/ProtocolCanary-Fixtures/commit/abc1234))
```

A changelog entry without one of these links is not ready for review.

## Development setup

No build system is required. `tools/validate/validate.py` uses only the
Python 3.11+ standard library (`tomllib`), so there is nothing to install.

For convenience, a `Makefile` wraps the commands CI runs:

- `make validate` — structural fixture validation.
- `make badge` — regenerates README.md's fixture-count badge.
- `make badge-check` — fails if that badge is stale.
- `make test` — the repository test suite.
- `make check` — all of them, in CI's order, stopping at the first failure.

The underlying commands work identically if run directly, so `make` is
not a requirement for contributing — it only saves typing.

# Threat Model for ProtocolCanary-Fixtures

This document identifies the assets, actors, trust boundaries, and attack surfaces
in this repository, and maps each threat to the controls that mitigate it.
Every control claim cites the exact file and function that implements it.
Unverified claims are marked **not verified**.

---

## Scope and Assumptions

**In scope:** the fixture packs under `protocol-*/`, the tools that validate, package,
and release them, and the GitHub Actions workflows that run those tools.

**Out of scope:** the `Protocol-Canary` engine that consumes these fixtures,
the `ProtocolCanary-Action` GitHub Action, and the Stellar network itself.

**Assumptions:**

- Fixture files are declarative data, never code (enforced by validator).
- All tools run with the Python 3.11+ standard library only; no third-party deps.
- Consumers verify the pack digest against a pinned manifest before use.
- The GitHub Actions runner environment is trusted for CI execution.

---

## Assets

| Asset | Description | Location |
|-------|-------------|----------|
| Fixture content | TOML files declaring protocol behavior assertions | `protocol-*/**/*.toml` |
| Registry & pack digest | `registry-v1.json` (CF-02) binding fixture IDs to file hashes | generated at pack time |
| Release archive | Deterministic `.tar.gz` containing fixtures + registry (CF-06) | built by `tools/pack/pack.py` |
| Release manifest | `*.manifest.json` with archive hash, pack digest, git revision (CF-06) | built by `tools/release/manifest.py` |
| Evidence records | JSON files recording live-network observations | `protocol-*/evidence/` |
| Verification evidence | JSON files passed via `--evidence` to the engine | provided by consumer |

---

## Actors

| Actor | Role | Trust Level |
|-------|------|-------------|
| Contributor | Proposes fixture additions/changes via PR | Untrusted input |
| Maintainer | Reviews and merges PRs; cuts releases | Trusted |
| Release workflow | Automated archive + manifest build on tag push | Trusted (pinned actions) |
| Consumer | Downloads pack, verifies manifest, runs engine | Trusts manifest + digest |
| Engine (`stellar-canary`) | Loads fixtures, executes assertions | Trusts fixture format contract |

---

## Trust Boundaries

```text
┌─────────────────────────────────────────────────────────────┐
│                    Contributor PR                           │
│  (untrusted fixture TOML, header comments, evidence JSON)  │
└──────────────────────┬──────────────────────────────────────┘
                       │ PR review + CI validation
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Main Branch                              │
│  (validated fixtures, passing tests, current badge)        │
└──────────────────────┬──────────────────────────────────────┘
                       │ Tag push (vX.Y.Z)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Release Workflow                         │
│  (tools/pack/pack.py → tools/release/manifest.py)          │
│  Outputs: archive.tar.gz, archive.manifest.json            │
└──────────────────────┬──────────────────────────────────────┘
                       │ Consumer verifies manifest
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Consumer Environment                     │
│  (tools/pack/verify_archive.py → stellar-canary check)     │
└─────────────────────────────────────────────────────────────┘
```

---

## Attack Surfaces and Threats

### 1. Malicious Fixture Content

| Threat | Description | Control | Status |
|--------|-------------|---------|--------|
| T1.1 | Fixture contains shell command / executable code | Validator rejects unknown top-level keys; fixture contract has no exec fields | **Controlled** — `tools/validate/validate.py::validate_top_level_keys` (line 745-770) enforces closed top-level schema; `SURFACE_FIELDS` whitelists only declarative keys |
| T1.2 | Fixture embeds private key / secret | Validator does not scan for secrets; relies on contributor discipline and review | **Partially controlled** — No automated secret scanning in validator; `SECURITY.md` states "no secrets", but detection is manual |
| T1.3 | Fixture asserts false protocol behavior (fabricated expected values) | `source_reference` required + pinned to commit/tag; header comment documents derivation | **Controlled** — `tools/validate/validate.py::check_source_reference` (line 462-535) requires CAP id or pinned HTTPS URL; `validate_common_fields` (line 624-698) makes missing `source_reference` an error |
| T1.4 | Fixture uses unsupported XDR type / RPC method / Soroban kind | Validator whitelists `XDR_TYPES`, `RPC_METHODS`, `SOROBAN_EXPECT_KINDS` | **Controlled** — `tools/validate/validate.py` constants (lines 79-97, 137-139) synced with upstream; `validate_xdr_body`/`validate_rpc_body`/`validate_soroban_body` reject unknown values |
| T1.5 | Fixture claims capability not in closed enum | Validator checks `required_capabilities` against `CAPABILITIES` set | **Controlled** — `tools/validate/validate.py::validate_common_fields` (lines 700-724); synced with `tests/fixtures/capabilities.json` via `test_validate.py` |

### 2. Path and Link Tricks

| Threat | Description | Control | Status |
|--------|-------------|---------|--------|
| T2.1 | Symlink in pack points outside pack (arbitrary file read) | Registry generator and pack tool reject any symlink/junction | **Controlled** — `tools/registry/generate.py::_is_link_like` (line 159-163) + `scan_pack` (line 166-218) raises `PackError` on symlink; `tools/pack/pack.py::collect_members` (line 99-121) calls same generator |
| T2.2 | Path traversal via `../` in `input_file` / `expected_file` | `check_reference` rejects `..` segments; `check_pack_path` validates path | **Controlled** — `tools/registry/generate.py::check_reference` (line 138-146) and `check_pack_path` (line 107-135) |
| T2.3 | Windows reserved names (CON, PRN, AUX, etc.) cause extraction issues | Portable name validator rejects reserved names in all tools | **Controlled** — `tools/validate/portable_names.py` (shared via `_load_portable_names`); used by validator (F-049), registry generator, and archive verifier |
| T2.4 | Case-only duplicate paths (exploits case-insensitive FS) | Registry generator and archive verifier track casefolded names | **Controlled** — `tools/registry/generate.py::scan_pack` (lines 192-197); `tools/pack/verify_archive.py::extract_safely` (lines 151-155) |
| T2.5 | Path depth exceeds limit (archive extraction DoS) | `MAX_DEPTH = 8` enforced in generator, packer, verifier | **Controlled** — `tools/registry/generate.py` (line 69); `tools/pack/pack.py` (line 53); `tools/pack/verify_archive.py` (line 48) |

### 3. Archive Extraction Attacks

| Threat | Description | Control | Status |
|--------|-------------|---------|--------|
| T3.1 | Archive contains absolute paths → write outside destination | `verify_archive.py::_check_member_path` rejects absolute paths | **Controlled** — `tools/pack/verify_archive.py` lines 94-116 |
| T3.2 | Archive contains symlinks/hard links/special files | Extractor only allows regular files and directories | **Controlled** — `tools/pack/verify_archive.py::extract_safely` (lines 140-146) rejects non-file/dir entries |
| T3.3 | Archive sets setuid/setgid/sticky bits | Extractor rejects entries with `_SPECIAL_BITS` (0o7000) | **Controlled** — `tools/pack/verify_archive.py` line 147-148 |
| T3.4 | Archive entry overwrites another (duplicate paths) | `seen` dict tracks names; duplicate raises error | **Controlled** — `tools/pack/verify_archive.py::extract_safely` (lines 149-150) |
| T3.5 | Decompression bomb (huge uncompressed size) | `MAX_TOTAL_BYTES = 16 MiB`, `MAX_FILE_BYTES = 1 MiB` enforced during extraction | **Controlled** — `tools/pack/verify_archive.py::extract_safely` (lines 163-170) |
| T3.6 | Archive has more entries than limit | `MAX_ENTRIES = 2000` enforced | **Controlled** — `tools/pack/verify_archive.py` line 136-137 |
| T3.7 | Archive is not a valid gzip/tar stream | `UnreadableArchive` exception caught and reported | **Controlled** — `tools/pack/verify_archive.py::extract_safely` (lines 183-184) |

### 4. Tampered Release

| Threat | Description | Control | Status |
|--------|-------------|---------|--------|
| T4.1 | Archive modified after build (SHA-256 mismatch) | Manifest records archive SHA-256; consumer verifies via `verify_archive.py --sha256` | **Controlled** — `tools/release/manifest.py::build_manifest` (line 133-134) records hash; `tools/pack/verify_archive.py::verify_archive` (lines 250-256) checks it |
| T4.2 | Registry in archive doesn't match archived files (pack digest mismatch) | Manifest tool recomputes pack digest from archived files and compares to registry's `packDigest` | **Controlled** — `tools/release/manifest.py::build_manifest` (lines 156-162) |
| T4.3 | Manifest itself is forged | No signature on manifest; consumer must trust GitHub release page or pin manifest hash out-of-band | **Not controlled today** — **Gap tracked by D-07 / issue #332** (provenance attestation). Checksums protect against corruption, not authenticity. |
| T4.4 | Git revision in manifest doesn't match actual source | Manifest requires full 40-char commit SHA; release workflow must pass correct revision | **Controlled** — `tools/release/manifest.py` line 122-125 validates 40-hex format; release workflow must supply correct value |

### 5. Stale or Forged Evidence

| Threat | Description | Control | Status |
|--------|-------------|---------|--------|
| T5.1 | Evidence JSON modified after live observation | Evidence files in repo are immutable once committed; live observations recorded in fixture headers with date | **Partially controlled** — Fixture headers record observation date (UTC) per `CONTRIBUTING.md`; no cryptographic binding of evidence to fixture |
| T5.2 | Fixture cites source_reference that moves (branch name) | Validator requires pinned commit SHA or release tag for GitHub URLs | **Controlled** — `tools/validate/validate.py::check_source_reference` (lines 497-534) extracts ref from GitHub URLs and validates via `_is_pinned_ref` (line 458-459) |
| T5.3 | Evidence passed via `--evidence` contains malicious content | Evidence text scanned for forbidden characters (same as fixtures) | **Controlled** — `tools/validate/validate.py::report_unsafe_characters` (line 442-455) called on evidence in `main()` |

### 6. Workflow Compromise

| Threat | Description | Control | Status |
|--------|-------------|---------|--------|
| T6.1 | Unpinned GitHub Action used (supply chain attack) | All `uses:` in workflows pinned to full commit SHA | **Controlled** — `.github/workflows/validate.yml` and `engine-drift.yml` pin actions to 40-char SHAs (e.g. `actions/checkout@3d3c42e5...`) |
| T6.2 | Workflow has excessive permissions (write access) | Top-level `permissions: contents: read` on both workflows | **Controlled** — `.github/workflows/validate.yml` line 13-14; `.github/workflows/engine-drift.yml` line 33-34 |
| T6.3 | `pull_request_target` used without justification | Neither workflow uses `pull_request_target` | **Controlled** — Verified by inspection; test in `test_workflow_policy.py` will enforce |
| T6.4 | Secrets used in pull_request workflow (except GITHUB_TOKEN) | No secrets used in either workflow | **Controlled** — Verified by inspection; test in `test_workflow_policy.py` will enforce |
| T6.5 | Dependabot doesn't update action pins | `.github/dependabot.yml` configured to update action SHAs | **Controlled** — `.github/dependabot.yml` exists and targets workflow files |

---

## Known Gaps and Residual Risks

| Gap | Description | Tracking |
|-----|-------------|----------|
| **Release signatures (D-07)** | Manifest and archive have checksums but no cryptographic signatures. A compromised release asset on GitHub could be swapped without detection. | Decision D-07: deferred; issue #332 (provenance attestation) open |
| **Build provenance attestation** | No SLSA/SBOM or signed provenance linking archive to source commit and build workflow run. | Issue #332 open |
| **Archive verifier accepts some non-standard tar shapes** | Issue #346 open questions about whether verifier accepts all valid CF-06 archives and rejects all invalid ones. | Issue #346 open |
| **Secret scanning in fixtures** | No automated detection of private keys, tokens in fixture files or evidence. | Relies on manual review; no tool yet |
| **Evidence binding** | Live-network evidence in fixture headers has no cryptographic link to the fixture content or observation timestamp. | Stale observations possible; date convention only |
| **Consumer trust in manifest** | Consumer must obtain manifest out-of-band (GitHub release page) and trust it; no in-band verification of manifest authenticity. | Gap if release page is compromised |

---

## Control Verification Checklist

Each control above should be spot-checkable in under a minute. Key references:

| Control | File | Function/Constant | Line(s) |
|---------|------|-------------------|---------|
| Top-level key whitelist | `tools/validate/validate.py` | `validate_top_level_keys`, `SURFACE_FIELDS` | 745-770, 263-275 |
| Source reference pinning | `tools/validate/validate.py` | `check_source_reference`, `_is_pinned_ref` | 462-535, 458-459 |
| Symlink rejection | `tools/registry/generate.py` | `_is_link_like`, `scan_pack` | 159-163, 166-218 |
| Path traversal rejection | `tools/registry/generate.py` | `check_reference`, `check_pack_path` | 138-146, 107-135 |
| Windows portable names | `tools/validate/portable_names.py` | `check_segment`, `check_path` | (entire file) |
| Case-fold duplicate detection | `tools/registry/generate.py` | `scan_pack` folded dict | 192-197 |
| Archive safe extraction | `tools/pack/verify_archive.py` | `extract_safely`, `_check_member_path` | 119-185, 94-116 |
| Archive size limits | `tools/pack/verify_archive.py` | `MAX_ENTRIES`, `MAX_TOTAL_BYTES`, `MAX_FILE_BYTES`, `MAX_DEPTH` | 45-48 |
| Manifest pack digest verification | `tools/release/manifest.py` | `build_manifest`, `_pack_digest` | 102-162, 64-70 |
| Workflow action pinning | `.github/workflows/validate.yml` | `uses:` lines | 23, 25, 56, 58, 88, 91, 122, 124 |
| Workflow permissions | `.github/workflows/validate.yml` | `permissions:` | 13-14 |
| CODEOWNERS review requirement | `.github/CODEOWNERS` | `*` pattern | 16 |

---

## Residual Risk Statement

Even with all implemented controls, a determined attacker who compromises the GitHub
release page or the maintainer's signing key (once signatures exist) could distribute
a malicious pack. Consumers should:

1. Pin the pack digest (from `registry-v1.json` or manifest) in their CI configuration.
2. Verify the archive SHA-256 against the manifest using `verify_archive.py --sha256`.
3. Monitor for security advisories on this repository.

The threat model will be updated when D-07 (signatures), #332 (provenance), and #346
(archive verifier findings) are resolved.

---

*Document version: 1.0*
*Author: Generated per issue #356*
*Review: Spot-check at least 5 cited controls against source code before approval.*
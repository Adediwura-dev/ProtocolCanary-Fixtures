"""Pack-level checks for the Protocol 28 fixture pack.

These are structural/offline checks (no network, no execution) that this
specific release's pack matches its own documented inventory. The actual
end-to-end verification that these fixtures pass when run by
Protocol-Canary against live testnet is recorded in each fixture's header
comment and in docs/protocol-28.md, not re-run here — see CONTRIBUTING.md
on why CI does not require a live RPC endpoint.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"

_spec = importlib.util.spec_from_file_location("validate", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate"] = validate
_spec.loader.exec_module(validate)

PACK = REPO_ROOT / "protocol-28"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

EXPECTED_IDS_BY_SURFACE = {
    "xdr": {
        "p28-xdr-cap83-empty-tx-set",
        "p28-xdr-cap83-empty-tx-set-malformed",
        "p28-xdr-cap85-external-ref-roundtrip",
        "p28-xdr-cap85-external-ref-malformed",
        "p28-xdr-stellar-value-basic-roundtrip",
        "p28-xdr-stellar-value-basic-decode-success",
        "p28-xdr-stellar-value-basic-decode-failure",
        "p28-xdr-stellar-value-signed-roundtrip",
        "p28-xdr-stellar-value-signed-decode-success",
        "p28-xdr-stellar-value-signed-decode-failure",
    },
    "rpc": {"p28-rpc-network", "p28-rpc-latest-ledger"},
    "soroban": {
        "p28-soroban-native-asset-name",
        "p28-soroban-sim-error-unknown-function",
        "p28-soroban-sim-error-missing-contract",
        "p28-soroban-sim-error-arg-count",
        "p28-soroban-sim-error-arg-type",
    },
}


class Protocol28PackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = validate.validate_directory(PACK)
        fixtures = [
            validate.load_fixture(p, validate.Report())
            for p in validate.find_fixture_files(PACK)
        ]
        self.fixtures = [fx for fx in fixtures if fx is not None]

    def test_pack_is_structurally_valid(self) -> None:
        self.assertEqual(self.report.errors, [])

    def test_every_fixture_targets_protocol_28(self) -> None:
        for fx in self.fixtures:
            self.assertEqual(fx.data.get("protocol"), 28, fx.path)

    def test_fixture_paths_match_declared_surface(self) -> None:
        for fx in self.fixtures:
            relative_path = fx.path.relative_to(PACK)
            self.assertEqual(relative_path.parts[0], fx.data.get("surface"), fx.path)

    def test_fixture_ids_follow_the_p_protocol_surface_convention(self) -> None:
        # CONTRIBUTING.md documents `p<protocol>-<surface>-<slug>` for fixture
        # IDs, and the schema's pattern only requires lowercase/non-empty. This
        # per-pack check enforces the documented prefix for protocol-28.
        for fx in self.fixtures:
            expected_prefix = f"p{fx.data.get('protocol')}-{fx.data.get('surface')}-"
            self.assertTrue(
                str(fx.data.get("id", "")).startswith(expected_prefix),
                f"{fx.path}: id {fx.data.get('id')!r} does not start with {expected_prefix!r}",
            )

    def test_every_fixture_has_a_source_reference(self) -> None:
        for fx in self.fixtures:
            self.assertTrue(fx.data.get("source_reference"), fx.path)

    def test_every_fixture_starts_with_a_header_comment(self) -> None:
        for path in validate.find_fixture_files(PACK):
            first_non_empty = next(
                (line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()),
                "",
            )
            self.assertTrue(first_non_empty.lstrip().startswith("#"), path)

    def test_expected_fixture_ids_are_present_per_surface(self) -> None:
        by_surface: dict[str, set[str]] = {"xdr": set(), "rpc": set(), "soroban": set()}
        for fx in self.fixtures:
            surface = fx.data.get("surface")
            if surface in by_surface:
                by_surface[surface].add(fx.data.get("id"))
        self.assertEqual(by_surface, EXPECTED_IDS_BY_SURFACE)

    def test_changelog_fixture_ids_still_exist_or_are_marked_removed(self) -> None:
        changelog = CHANGELOG.read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## ", 1)[0]
        actual_ids = {str(fx.data.get("id")) for fx in self.fixtures}

        for line in unreleased.splitlines():
            for fixture_id in re.findall(r"`(p\d+-[a-z0-9-]+)`", line):
                if fixture_id not in actual_ids:
                    status = line.lower()
                    self.assertTrue(
                        "removed" in status or "deprecated" in status,
                        f"CHANGELOG.md references missing fixture id {fixture_id!r}",
                    )

    def test_no_fixture_claims_cap_0086(self) -> None:
        # CAP-0086 is a documented gap (see docs/protocol-28.md); a fixture
        # claiming to cover it would be fabricated coverage.
        for fx in self.fixtures:
            self.assertNotIn("cap-0086", fx.data.get("category", "").lower(), fx.path)
            self.assertNotIn("CAP-0086", fx.data.get("source_reference", ""), fx.path)

    def test_protocol_27_pack_has_no_fixtures_yet(self) -> None:
        p27 = REPO_ROOT / "protocol-27"
        self.assertEqual(validate.find_fixture_files(p27), [])

        # protocol-27/README.md documents the pack as intentionally empty as a
        # matter of policy (no placeholder fixtures). The *.toml-only check
        # above cannot see stray non-fixture content — an accidentally
        # committed scratch file, a draft fixture saved with the wrong
        # extension, or a leftover subdirectory — so assert the documented
        # invariant directly: the pack's only file is README.md and every
        # directory inside it is empty.
        self.assertEqual(
            sorted(str(p.relative_to(p27)) for p in p27.rglob("*") if p.is_file()),
            ["README.md"],
        )
        non_empty_dirs = [p for p in p27.rglob("*") if p.is_dir() and any(p.iterdir())]
        self.assertEqual(non_empty_dirs, [])


    def test_no_fixture_files_outside_allowed_subdirectories(self) -> None:
        allowed_dirs = {"xdr", "rpc", "soroban"}
        for toml_path in PACK.rglob("*.toml"):
            relative_path = toml_path.relative_to(PACK)
            self.assertIn(
                relative_path.parts[0],
                allowed_dirs,
                f"Fixture file {relative_path} is located outside allowed subdirectories ({allowed_dirs})",
            )


if __name__ == "__main__":
    unittest.main()


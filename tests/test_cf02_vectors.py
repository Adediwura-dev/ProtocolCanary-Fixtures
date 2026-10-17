"""CF-02 conformance vector tests (issue #286, F-030).

``tests/vectors/cf-02/`` holds the shared conformance data for CF-02, the
fixture registry and pack digest contract
(``Protocol-Canary/docs/contracts/cf-02-registry-and-digest.md``, authoritative).
This file is the runner that asserts the vectors, the reference implementation
in ``tools/registry/generate.py`` and the shipped registry schema all agree.

What is being checked, and why it is not circular. The per-file hashes and the
``packDigest`` recorded in each vector's ``expected.json`` were computed with
``sha256sum``/``wc -c``/``printf`` (see the vector set's README), not with the
generator. So each test below compares two independent derivations: the
recorded values against a fresh walk of the files, and then the generator
against those recorded values. A change to the hashing, the digest-input
format, the canonical ordering or the manifest encoding fails here instead of
silently changing every digest in the ecosystem.

Three jobs, beyond re-deriving the digests:

1. Pin the *negative* half of the contract, which a digest that merely matched
   would not prove. The digest is over raw bytes with no normalization, so a
   byte a TOML parser cannot even see -- one extra space, a CRLF -- must change
   it; and the manifest is canonical, so a reordered key or a reordered
   ``fixtures`` array must not be.
2. Make the ordering rules in CF-02 section 4 observable. The ``complex`` pack
   is built so that byte order and component-wise order disagree, so an
   implementation that sorts path *components* (for example Rust's ``PathBuf``
   ordering) produces a different digest.
3. Keep the vectors out of the consumable corpus: they are not fixtures, are
   not counted in README.md's badge, and must not collide with any id the
   protocol packs use.

Run with: python3 -m unittest tests/test_cf02_vectors.py
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VECTORS_DIR = REPO_ROOT / "tests" / "vectors" / "cf-02"
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry-v1.schema.json"
CONTRACT_REFERENCE = (
    "https://github.com/StellarCanary/Protocol-Canary/blob/main/docs/contracts/"
    "cf-02-registry-and-digest.md"
)
MANIFEST_NAME = "expected-registry-v1.json"

# The vector set, in the order the README documents it. A fourth vector is a
# deliberate change, not a drive-by addition: each one is shared test data for
# another implementation.
VECTOR_NAMES = ("minimal", "standard", "complex")

# Directory depth below the pack root, in directories. Kept here rather than
# read from the vector so the vector cannot silently stop being the depth
# ladder it claims to be ("varying directory depths" is half of what this set
# is for); the test recomputes the real depth from the files as well.
EXPECTED_MAX_DEPTH = {"minimal": 0, "standard": 3, "complex": 5}

RECORD_FIELDS = (
    "vector",
    "contract",
    "contractReference",
    "packRoot",
    "protocol",
    "purpose",
    "exercises",
    "maxDepth",
    "fixtureIds",
    "packFiles",
    "packDigest",
    "registry",
)

DIGEST_HEADER = "canary-pack-v1\n"


def _load(name: str, path: Path):
    """Import a stdlib-only repository module by path, without a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate = _load("cf02_generate", REPO_ROOT / "tools" / "registry" / "generate.py")
validate = _load("cf02_validate", REPO_ROOT / "tools" / "validate" / "validate.py")
lite = _load("cf02_jsonschema_lite", REPO_ROOT / "tools" / "validate" / "jsonschema_lite.py")
badge = _load("cf02_badge", REPO_ROOT / "tools" / "badge" / "badge.py")


# ---------------------------------------------------------------- the contract


def byte_key(text: str) -> bytes:
    """CF-02 section 4: ordering compares UTF-8 bytes, never a locale."""
    return text.encode("utf-8")


def files_on_disk(pack: Path) -> list[str]:
    """Every regular file under ``pack``, pack member or not."""
    return sorted(
        path.relative_to(pack).as_posix()
        for path in pack.rglob("*")
        if path.is_file()
    )


def walk_pack(pack: Path) -> dict[str, bytes]:
    """Every pack file as ``{pack-relative path: raw bytes}``, per CF-02 section 2.

    Membership is reimplemented here rather than borrowed from
    ``tools/registry/generate.py``, because this walk is what proves the
    recorded hashes describe the files on disk: every ``*.toml`` file at any
    depth outside a ``.git`` directory, plus every file a fixture names in
    ``input_file`` or ``expected_file`` -- and nothing else, so a README or a
    stale manifest beside the pack is outside it and cannot affect the digest.
    A symbolic link anywhere under the pack makes a pack invalid, so one is an
    error here rather than something to follow.
    """
    members: dict[str, bytes] = {}
    for path in sorted(pack.rglob("*")):
        relative = path.relative_to(pack)
        if path.is_symlink():
            raise AssertionError(f"{path} is a symbolic link; a pack may not contain one")
        if path.is_dir() or ".git" in relative.parts or path.suffix != ".toml":
            continue
        members[relative.as_posix()] = path.read_bytes()

    for relative, data in list(members.items()):
        with (pack / relative).open("rb") as handle:
            fixture = tomllib.load(handle)
        for field in ("input_file", "expected_file"):
            if field not in fixture:
                continue
            referenced = (pack / relative).parent / fixture[field]
            key = referenced.relative_to(pack).as_posix()
            members.setdefault(key, referenced.read_bytes())
    return members


def file_records(files: dict[str, bytes]) -> list[dict]:
    """The per-file facts the digest input is built from, in canonical order."""
    return [
        {"path": path, "sha256": hashlib.sha256(files[path]).hexdigest(), "size": len(files[path])}
        for path in sorted(files, key=byte_key)
    ]


def digest_input(records: list[dict]) -> bytes:
    """CF-02 section 5's digest input, rebuilt from path/hash/size triples.

    Written out here rather than borrowing the generator's own construction:
    if this function and the generator disagree, one of them is wrong and this
    test says so.
    """
    lines = [DIGEST_HEADER]
    lines += [f"{r['sha256']} {r['size']} {r['path']}\n" for r in records]
    return "".join(lines).encode("utf-8")


def pack_digest(records: list[dict]) -> str:
    return "sha256:" + hashlib.sha256(digest_input(records)).hexdigest()


def digest_of_pack(pack: Path) -> str:
    return pack_digest(file_records(walk_pack(pack)))


def swap_lines(data: bytes, first: bytes, second: bytes) -> bytes:
    """Return ``data`` with the lines starting with ``first`` and ``second`` swapped."""
    lines = data.splitlines(keepends=True)
    positions = {}
    for needle in (first, second):
        matches = [index for index, line in enumerate(lines) if line.startswith(needle)]
        if len(matches) != 1:
            raise AssertionError(f"{needle!r} is not on exactly one line of the fixture")
        positions[needle] = matches[0]
    lines[positions[first]], lines[positions[second]] = (
        lines[positions[second]],
        lines[positions[first]],
    )
    return b"".join(lines)


def write_pack(destination: Path, files: dict[str, bytes]) -> Path:
    """Materialize ``files`` under ``destination`` in the given insertion order."""
    if destination.exists():
        shutil.rmtree(destination)
    for relative, data in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return destination


def load_record(name: str) -> dict:
    return json.loads((VECTORS_DIR / name / "expected.json").read_text(encoding="utf-8"))


def load_manifest(name: str) -> dict:
    return json.loads((VECTORS_DIR / name / MANIFEST_NAME).read_text(encoding="utf-8"))


def manifest_bytes(name: str) -> bytes:
    return (VECTORS_DIR / name / MANIFEST_NAME).read_bytes()


def canonical_manifest(manifest: dict) -> bytes:
    """The canonical encoding of CF-02 section 3: UTF-8, indent 2, one newline."""
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def vector_names_on_disk() -> list[str]:
    return sorted(p.name for p in VECTORS_DIR.iterdir() if p.is_dir())


def fixture_paths(name: str) -> list[Path]:
    return sorted((VECTORS_DIR / name / "pack").rglob("*.toml"))


# ---------------------------------------------------------------- the vectors


class VectorSetTests(unittest.TestCase):
    """The set itself: three vectors, at three depths, with three digests."""

    def test_exactly_the_expected_vectors_exist(self) -> None:
        self.assertEqual(vector_names_on_disk(), sorted(VECTOR_NAMES))

    def test_every_vector_has_a_pack_a_record_and_a_manifest(self) -> None:
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                self.assertTrue((VECTORS_DIR / name / "pack").is_dir())
                self.assertTrue((VECTORS_DIR / name / "expected.json").is_file())
                self.assertTrue((VECTORS_DIR / name / MANIFEST_NAME).is_file())

    def test_every_record_declares_exactly_the_documented_fields(self) -> None:
        # A strict key set, so a typo in expected.json cannot silently pass as
        # an extra field nothing reads.
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                self.assertEqual(set(load_record(name)), set(RECORD_FIELDS))

    def test_every_record_names_the_contract_it_conforms_to(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            with self.subTest(vector=name):
                self.assertEqual(record["contract"], "CF-02")
                self.assertEqual(record["contractReference"], CONTRACT_REFERENCE)
                self.assertEqual(record["packRoot"], "pack")
                self.assertEqual(record["protocol"], 28)

    def test_each_vector_is_a_deeper_directory_tree_than_the_last(self) -> None:
        # "Varying directory depths" is an acceptance criterion: the recorded
        # depth must match the tree, and the three must be distinct.
        depths = {}
        for name in VECTOR_NAMES:
            files = walk_pack(VECTORS_DIR / name / "pack")
            depths[name] = max(len(path.split("/")) - 1 for path in files)
            with self.subTest(vector=name):
                self.assertEqual(depths[name], load_record(name)["maxDepth"])
                self.assertEqual(depths[name], EXPECTED_MAX_DEPTH[name])
        self.assertEqual(
            sorted(depths.values()), [depths[n] for n in VECTOR_NAMES], depths
        )
        self.assertEqual(len(set(depths.values())), 3, depths)

    def test_each_vector_says_what_it_exercises(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            with self.subTest(vector=name):
                self.assertTrue(record["purpose"].strip())
                self.assertGreaterEqual(len(record["exercises"]), 2)
                for exercise in record["exercises"]:
                    self.assertTrue(exercise.strip())

    def test_the_three_pack_digests_are_distinct(self) -> None:
        digests = {load_record(name)["packDigest"] for name in VECTOR_NAMES}
        self.assertEqual(len(digests), len(VECTOR_NAMES), digests)

    def test_pack_paths_are_ascii_only(self) -> None:
        # A non-ASCII path would make the digest depend on the checkout's
        # Unicode normalization (macOS normalizes file names), which is a flaky
        # failure rather than a conformance check. Non-ASCII text lives in
        # fixture content instead; see the README.
        for name in VECTOR_NAMES:
            for path in walk_pack(VECTORS_DIR / name / "pack"):
                with self.subTest(vector=name, path=path):
                    self.assertEqual(path, path.encode("ascii", "strict").decode("ascii"))


class RecordedValueTests(unittest.TestCase):
    """The recorded hashes and digests describe the files actually committed."""

    def test_recorded_files_are_exactly_the_pack_files_on_disk(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            with self.subTest(vector=name):
                self.assertEqual(
                    [entry["path"] for entry in record["packFiles"]],
                    sorted(walk_pack(VECTORS_DIR / name / "pack"), key=byte_key),
                )

    def test_a_vector_directory_holds_only_pack_files(self) -> None:
        # Everything under `pack/` must be a member. A file that is not one --
        # a README, a manifest, an editor backup -- would be silently outside
        # the digest, so it is rejected here rather than left to rot beside the
        # vector looking authoritative.
        for name in VECTOR_NAMES:
            pack = VECTORS_DIR / name / "pack"
            with self.subTest(vector=name):
                self.assertEqual(files_on_disk(pack), sorted(walk_pack(pack), key=byte_key))

    def test_recorded_hashes_and_sizes_match_the_files(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            files = walk_pack(VECTORS_DIR / name / "pack")
            for entry in record["packFiles"]:
                with self.subTest(vector=name, path=entry["path"]):
                    data = files[entry["path"]]
                    self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
                    self.assertEqual(entry["size"], len(data))

    def test_recorded_files_are_in_canonical_byte_order(self) -> None:
        for name in VECTOR_NAMES:
            paths = [entry["path"] for entry in load_record(name)["packFiles"]]
            with self.subTest(vector=name):
                self.assertEqual(paths, sorted(paths, key=byte_key))

    def test_recorded_files_are_distinct(self) -> None:
        # A payload shared by two fixtures is one pack file and one digest
        # input line, never two.
        for name in VECTOR_NAMES:
            paths = [entry["path"] for entry in load_record(name)["packFiles"]]
            with self.subTest(vector=name):
                self.assertEqual(len(paths), len(set(paths)))

    def test_the_recorded_digest_is_reproducible_from_the_recorded_values(self) -> None:
        # The whole point of a vector: everything needed to recompute the
        # digest is in the record, with no access to the pack.
        for name in VECTOR_NAMES:
            record = load_record(name)
            with self.subTest(vector=name):
                self.assertEqual(pack_digest(record["packFiles"]), record["packDigest"])

    def test_the_recorded_digest_matches_a_fresh_walk_of_the_pack(self) -> None:
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                self.assertEqual(
                    digest_of_pack(VECTORS_DIR / name / "pack"),
                    load_record(name)["packDigest"],
                )

    def test_the_digest_input_is_the_documented_shape(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            text = digest_input(record["packFiles"]).decode("utf-8")
            lines = text.splitlines(keepends=True)
            with self.subTest(vector=name):
                self.assertEqual(lines[0], DIGEST_HEADER)
                self.assertEqual(len(lines), len(record["packFiles"]) + 1)
                for line, entry in zip(lines[1:], record["packFiles"]):
                    self.assertEqual(
                        line,
                        f"{entry['sha256']} {entry['size']} {entry['path']}\n",
                    )
                self.assertTrue(text.endswith("\n"))


class ReferenceImplementationTests(unittest.TestCase):
    """The reference generator must reproduce the vectors byte for byte."""

    def test_the_generator_digest_matches_the_recorded_digest(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            registry = generate.build_registry(
                generate.scan_pack(VECTORS_DIR / name / "pack")
            )
            with self.subTest(vector=name):
                self.assertEqual(registry["packDigest"], record["packDigest"])

    def test_the_generator_renders_the_golden_manifest_exactly(self) -> None:
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                text = generate.generate(VECTORS_DIR / name / "pack")
                self.assertEqual(text.encode("utf-8"), manifest_bytes(name))

    def test_the_generator_rejects_nothing_in_a_vector_pack(self) -> None:
        # A vector pack is a pack the generator must accept, not merely hash:
        # if one became invalid, its digest would no longer mean anything.
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                self.assertTrue(generate.generate(VECTORS_DIR / name / "pack"))

    def test_every_vector_pack_is_a_structurally_valid_pack(self) -> None:
        # The same rules the loader implements, which is what makes these packs
        # usable as shared conformance data rather than synthetic stubs.
        for name in VECTOR_NAMES:
            report = validate.validate_directory(VECTORS_DIR / name / "pack")
            with self.subTest(vector=name):
                self.assertEqual(report.errors, [])


class GoldenManifestTests(unittest.TestCase):
    """The canonical registry manifest of CF-02 section 3."""

    def test_the_manifest_hash_is_the_recorded_hash(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            with self.subTest(vector=name):
                self.assertEqual(
                    hashlib.sha256(manifest_bytes(name)).hexdigest(),
                    record["registry"]["sha256"],
                )
                self.assertEqual(record["registry"]["file"], MANIFEST_NAME)

    def test_the_manifest_is_its_own_canonical_encoding(self) -> None:
        # Section 6 rule 5: re-encoding must reproduce the committed bytes.
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                self.assertEqual(canonical_manifest(load_manifest(name)), manifest_bytes(name))

    def test_the_manifest_uses_lf_and_one_trailing_newline(self) -> None:
        for name in VECTOR_NAMES:
            text = manifest_bytes(name).decode("utf-8")
            with self.subTest(vector=name):
                self.assertNotIn("\r", text)
                self.assertTrue(text.endswith("}\n"))
                self.assertFalse(text.endswith("\n\n"))
                self.assertTrue(text.startswith('{\n  "registryVersion": 1,\n'))

    def test_the_manifest_conforms_to_the_registry_schema(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        for name in VECTOR_NAMES:
            with self.subTest(vector=name):
                self.assertEqual(lite.validate(load_manifest(name), schema), [])

    def test_the_manifest_lists_the_recorded_fixture_ids(self) -> None:
        for name in VECTOR_NAMES:
            record, manifest = load_record(name), load_manifest(name)
            with self.subTest(vector=name):
                self.assertEqual([e["id"] for e in manifest["fixtures"]], record["fixtureIds"])
                self.assertEqual(manifest["protocol"], record["protocol"])

    def test_fixtures_are_sorted_by_id_in_byte_order(self) -> None:
        for name in VECTOR_NAMES:
            ids = [entry["id"] for entry in load_manifest(name)["fixtures"]]
            with self.subTest(vector=name):
                self.assertEqual(ids, sorted(ids, key=byte_key))

    def test_the_registry_covers_exactly_the_packs_fixture_files(self) -> None:
        for name in VECTOR_NAMES:
            manifest = load_manifest(name)
            with self.subTest(vector=name):
                self.assertEqual(
                    sorted(entry["path"] for entry in manifest["fixtures"]),
                    sorted(
                        path.relative_to(VECTORS_DIR / name / "pack").as_posix()
                        for path in fixture_paths(name)
                    ),
                )

    def test_registry_metadata_mirrors_the_toml(self) -> None:
        for name in VECTOR_NAMES:
            pack = VECTORS_DIR / name / "pack"
            for entry in load_manifest(name)["fixtures"]:
                path = pack / entry["path"]
                with path.open("rb") as handle:
                    fixture = tomllib.load(handle)
                with self.subTest(vector=name, fixture=entry["id"]):
                    self.assertEqual(entry["id"], fixture["id"])
                    self.assertEqual(
                        entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
                    )
                    self.assertEqual(entry["surface"], fixture["surface"])
                    self.assertEqual(entry["category"], fixture["category"])
                    self.assertEqual(entry["description"], fixture["description"])
                    self.assertEqual(
                        entry["sourceReference"], fixture["source_reference"]
                    )
                    # An absent list is absent, an explicit empty one is `[]`.
                    if "required_capabilities" in fixture:
                        self.assertEqual(
                            entry["requiredCapabilities"], fixture["required_capabilities"]
                        )
                    else:
                        self.assertNotIn("requiredCapabilities", entry)

    def test_payloads_are_sorted_resolved_and_hashed(self) -> None:
        for name in VECTOR_NAMES:
            pack = VECTORS_DIR / name / "pack"
            for entry in load_manifest(name)["fixtures"]:
                with (pack / entry["path"]).open("rb") as handle:
                    fixture = tomllib.load(handle)
                directory = str(Path(entry["path"]).parent)
                referenced = [
                    f"{directory}/{fixture[field]}" if directory != "." else fixture[field]
                    for field in ("input_file", "expected_file")
                    if field in fixture
                ]
                paths = [payload["path"] for payload in entry["payloads"]]
                with self.subTest(vector=name, fixture=entry["id"]):
                    self.assertEqual(paths, sorted(paths, key=byte_key))
                    self.assertEqual(paths, sorted(referenced, key=byte_key))
                    for payload in entry["payloads"]:
                        data = (pack / payload["path"]).read_bytes()
                        self.assertEqual(payload["sha256"], hashlib.sha256(data).hexdigest())

    def test_non_ascii_text_survives_as_utf8(self) -> None:
        # The registry is UTF-8. An encoder escaping non-ASCII ("\\u00e9")
        # would produce different manifest bytes, so the vectors include a
        # description that makes that visible.
        text = manifest_bytes("complex").decode("utf-8")
        self.assertIn("café", text)
        self.assertIn("中文", text)
        self.assertNotIn("\\u", text)


class OrderingTests(unittest.TestCase):
    """CF-02 section 4: id order, path order, and their divergence."""

    def test_id_order_and_path_order_diverge_in_the_standard_vector(self) -> None:
        record = load_record("standard")
        manifest = load_manifest("standard")
        by_id = record["fixtureIds"]
        # The registry's array is the id order, and the digest input is the
        # byte order of the pack-relative paths; in this pack they are opposite.
        self.assertEqual([entry["id"] for entry in manifest["fixtures"]], by_id)
        by_path = [entry["path"] for entry in manifest["fixtures"]]
        digest_paths = [entry["path"] for entry in record["packFiles"]]
        self.assertEqual(digest_paths, sorted(digest_paths, key=byte_key))
        self.assertNotEqual(by_id, by_path)
        self.assertEqual(by_id[0], "p28-rpc-cf02-standard-alpha")
        self.assertEqual(digest_paths[-1], "shallow/p28-rpc-cf02-standard-alpha.toml")
        self.assertEqual(digest_paths[0], "deep/level1/p28-xdr-cf02-standard-beta.toml")

    def test_the_digest_covers_payloads_as_well_as_fixtures(self) -> None:
        record = load_record("standard")
        paths = [entry["path"] for entry in record["packFiles"]]
        self.assertEqual(len(paths), 4)
        self.assertEqual(sum(path.endswith(".toml") for path in paths), 2)
        self.assertTrue(any(path.endswith("beta-input.bin") for path in paths))
        self.assertTrue(any(path.endswith("beta-expected.bin") for path in paths))

    def test_byte_order_disagrees_with_component_order_in_the_complex_vector(self) -> None:
        # The finding this vector exists to catch. `set-b` is a single path
        # component; comparing paths as components compares "set-b" against
        # "set" and puts `set/a` first. Comparing them as bytes stops at `-`
        # (0x2d) against `/` (0x2f), so `set-b` comes first.
        paths = [entry["path"] for entry in load_record("complex")["packFiles"]]
        byte_order = sorted(paths, key=byte_key)
        component_order = sorted(paths, key=lambda path: path.split("/"))
        self.assertEqual(paths, byte_order)
        self.assertNotEqual(byte_order, component_order)
        self.assertLess(
            byte_order.index("deep/level1/set-b/p28-rpc-cf02-complex-bravo.toml"),
            byte_order.index("deep/level1/set/a/p28-soroban-cf02-complex-shared-alpha.toml"),
        )
        self.assertLess(
            component_order.index("deep/level1/set/a/p28-soroban-cf02-complex-shared-alpha.toml"),
            component_order.index("deep/level1/set-b/p28-rpc-cf02-complex-bravo.toml"),
        )

    def test_an_uppercase_path_sorts_first_while_its_id_sorts_last(self) -> None:
        record = load_record("complex")
        upper = "ZZ-upper/p28-xdr-cf02-complex-upper.toml"
        self.assertEqual(record["packFiles"][0]["path"], upper)
        self.assertEqual(record["fixtureIds"][-1], "p28-xdr-cf02-complex-upper")

    def test_the_shared_payload_is_one_file_and_two_registry_entries(self) -> None:
        record = load_record("complex")
        manifest = load_manifest("complex")
        shared = "deep/level1/set/a/payloads/shared.bin"
        self.assertEqual(sum(entry["path"] == shared for entry in record["packFiles"]), 1)
        referencing = [
            entry["id"]
            for entry in manifest["fixtures"]
            if any(payload["path"] == shared for payload in entry["payloads"])
        ]
        self.assertEqual(
            referencing,
            ["p28-soroban-cf02-complex-shared-alpha", "p28-xdr-cf02-complex-shared-zero"],
        )
        self.assertEqual(len(record["packFiles"]), 5)
        self.assertEqual(len(manifest["fixtures"]), 4)

    def test_the_complex_vector_covers_every_surface(self) -> None:
        surfaces = {entry["surface"] for entry in load_manifest("complex")["fixtures"]}
        self.assertEqual(surfaces, {"xdr", "rpc", "soroban"})


class NegativeTests(unittest.TestCase):
    """A digest that ignored these would still match the positives above."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def assert_rejects_edit(self, name: str, mutated: dict[str, bytes], why: str) -> None:
        """``mutated`` must change the digest, and both derivations must agree."""
        pack = write_pack(self.tmp / f"{name}-{len(mutated)}", mutated)
        recorded = load_record(name)["packDigest"]
        fresh = digest_of_pack(pack)
        self.assertNotEqual(fresh, recorded, why)
        self.assertEqual(
            generate.build_registry(generate.scan_pack(pack))["packDigest"],
            fresh,
            f"the generator disagrees with this file's own walk: {why}",
        )
        self.assertNotEqual(
            generate.generate(pack).encode("utf-8"), manifest_bytes(name), why
        )

    def test_altered_whitespace_changes_the_digest(self) -> None:
        # The digest is over raw bytes: an edit a TOML parser cannot even see
        # must change it. The parsed fixture is asserted equal, so the only
        # difference is bytes.
        variants = {
            "an extra space around a key's '='": lambda data: data.replace(b"id = ", b"id  = ", 1),
            "CRLF line endings": lambda data: data.replace(b"\n", b"\r\n"),
            "an extra trailing newline": lambda data: data + b"\n",
            "a leading blank line": lambda data: b"\n" + data,
        }
        for name in VECTOR_NAMES:
            target = fixture_paths(name)[0]
            original = target.read_bytes()
            parsed = tomllib.loads(original.decode("utf-8"))
            for why, mutate in variants.items():
                mutated = dict(walk_pack(VECTORS_DIR / name / "pack"))
                relative = target.relative_to(VECTORS_DIR / name / "pack").as_posix()
                edited = mutate(original)
                with self.subTest(vector=name, variant=why):
                    self.assertNotEqual(edited, original, "the variant changed nothing")
                    self.assertEqual(tomllib.loads(edited.decode("utf-8")), parsed)
                    mutated[relative] = edited
                    self.assert_rejects_edit(name, mutated, why)

    def test_reordering_a_fixtures_keys_changes_the_digest(self) -> None:
        # The other half of "altered whitespace or reordered keys": a TOML file
        # whose keys are in a different order parses to exactly the same value,
        # and still hashes differently. Anything that hashed the parsed fixture
        # rather than its bytes would keep the old digest here.
        for name in VECTOR_NAMES:
            target = fixture_paths(name)[0]
            original = target.read_bytes()
            reordered = swap_lines(original, b"category = ", b"description = ")
            relative = target.relative_to(VECTORS_DIR / name / "pack").as_posix()
            mutated = dict(walk_pack(VECTORS_DIR / name / "pack"))
            mutated[relative] = reordered
            with self.subTest(vector=name):
                self.assertNotEqual(reordered, original)
                self.assertEqual(
                    tomllib.loads(reordered.decode("utf-8")),
                    tomllib.loads(original.decode("utf-8")),
                )
                self.assert_rejects_edit(
                    name, mutated, "a reordered key must change the digest"
                )

    def test_a_single_flipped_byte_in_a_payload_changes_the_digest(self) -> None:
        name = "complex"
        relative = "deep/level1/set/a/payloads/shared.bin"
        mutated = dict(walk_pack(VECTORS_DIR / name / "pack"))
        data = bytearray(mutated[relative])
        data[0] ^= 0x01
        mutated[relative] = bytes(data)
        self.assert_rejects_edit(name, mutated, "a flipped payload bit must change the digest")

    def test_an_extra_pack_file_changes_the_digest(self) -> None:
        # A second fixture, with a unique id so the pack stays valid: the only
        # reason the digest may move is that a fifth file exists.
        name = "minimal"
        mutated = dict(walk_pack(VECTORS_DIR / name / "pack"))
        extra = mutated["p28-xdr-cf02-minimal.toml"].replace(
            b'id = "p28-xdr-cf02-minimal"', b'id = "p28-xdr-cf02-extra"'
        )
        self.assertNotEqual(extra, mutated["p28-xdr-cf02-minimal.toml"])
        mutated["extra/p28-xdr-cf02-extra.toml"] = extra
        self.assert_rejects_edit(name, mutated, "a new file must change the digest")

    def test_a_renamed_pack_file_changes_the_digest(self) -> None:
        name = "minimal"
        original = "p28-xdr-cf02-minimal.toml"
        mutated = {"renamed.toml": walk_pack(VECTORS_DIR / name / "pack")[original]}
        # The edit only renames the file; the bytes are identical.
        self.assertEqual(
            hashlib.sha256(mutated["renamed.toml"]).hexdigest(),
            hashlib.sha256((VECTORS_DIR / name / "pack" / original).read_bytes()).hexdigest(),
        )
        self.assert_rejects_edit(name, mutated, "a renamed file must change the digest")

    def test_files_outside_the_pack_do_not_change_the_digest(self) -> None:
        # CF-02 section 2: everything that is not a *.toml fixture or a
        # referenced payload is outside the pack. A stale registry manifest, a
        # README and a .git directory must all be ignored -- including a
        # *.toml inside .git, which is skipped rather than parsed.
        name = "complex"
        mutated = dict(walk_pack(VECTORS_DIR / name / "pack"))
        mutated["README.md"] = b"not part of the pack\n"
        mutated["notes/registry-v1.json"] = b'{"stale": true}\n'
        mutated["expected.json"] = b"{}\n"
        mutated[".git/ignored.toml"] = b"not = [valid toml\n"
        pack = write_pack(self.tmp / "guarded", mutated)
        self.assertEqual(digest_of_pack(pack), load_record(name)["packDigest"])
        self.assertEqual(
            generate.generate(pack).encode("utf-8"), manifest_bytes(name)
        )

    def test_the_digest_is_independent_of_creation_order_and_location(self) -> None:
        # Section 5's guarantees: no dependence on file system order, root
        # path or timestamps. The pack is rewritten in reverse order under a
        # different parent.
        name = "complex"
        files = dict(reversed(list(walk_pack(VECTORS_DIR / name / "pack").items())))
        pack = write_pack(self.tmp / "a" / "deeper" / "elsewhere", files)
        self.assertEqual(digest_of_pack(pack), load_record(name)["packDigest"])
        self.assertEqual(
            generate.build_registry(generate.scan_pack(pack))["packDigest"],
            load_record(name)["packDigest"],
        )

    def test_reordering_manifest_keys_is_not_the_canonical_encoding(self) -> None:
        # Section 6 rule 5: registry bytes must equal the canonical encoding,
        # so a reordered key -- same data, different bytes -- must be rejected
        # by a validator that re-encodes and compares.
        golden = manifest_bytes("complex")
        manifest = load_manifest("complex")

        def moved_first(source: dict, key: str) -> dict:
            return {key: source[key], **{k: v for k, v in source.items() if k != key}}

        top_level = moved_first(manifest, "packDigest")
        entry = moved_first(manifest["fixtures"][0], "payloads")
        reordered = {**manifest, "fixtures": [entry, *manifest["fixtures"][1:]]}
        for why, candidate in (
            ("top-level keys", top_level),
            ("a fixture's keys", reordered),
        ):
            with self.subTest(reordering=why):
                self.assertEqual(json.loads(json.dumps(candidate)), candidate)
                self.assertNotEqual(
                    canonical_manifest(candidate), golden
                )
                self.assertEqual(json.loads(canonical_manifest(candidate)), manifest)

    def test_reordering_the_fixtures_array_is_not_the_canonical_encoding(self) -> None:
        manifest = load_manifest("complex")
        permuted = dict(manifest)
        permuted["fixtures"] = list(reversed(manifest["fixtures"]))
        self.assertNotEqual(canonical_manifest(permuted), manifest_bytes("complex"))
        # Same entries, only reordered: the ordering rule is what rejects it.
        self.assertEqual(
            sorted(e["id"] for e in permuted["fixtures"]),
            sorted(e["id"] for e in manifest["fixtures"]),
        )
        reordered_ids = [e["id"] for e in permuted["fixtures"]]
        self.assertNotEqual(reordered_ids, sorted(reordered_ids, key=byte_key))

    def test_reordering_payload_entries_is_not_the_canonical_encoding(self) -> None:
        manifest = load_manifest("standard")
        permuted = json.loads(json.dumps(manifest))
        entry = next(e for e in permuted["fixtures"] if len(e["payloads"]) == 2)
        entry["payloads"] = list(reversed(entry["payloads"]))
        self.assertNotEqual(canonical_manifest(permuted), manifest_bytes("standard"))

    def test_an_indented_manifest_is_not_the_canonical_encoding(self) -> None:
        # The manifest's own whitespace rules: two-space indent, LF, exactly one
        # trailing newline.
        golden = manifest_bytes("complex").decode("utf-8")
        for why, candidate in (
            (
                "four-space indent",
                json.dumps(load_manifest("complex"), indent=4, ensure_ascii=False) + "\n",
            ),
            ("CRLF", golden.replace("\n", "\r\n")),
            ("no trailing newline", golden.rstrip("\n")),
            ("an extra trailing newline", golden + "\n"),
            ("ASCII-escaped text", json.dumps(load_manifest("complex"), indent=2) + "\n"),
        ):
            with self.subTest(encoding=why):
                self.assertNotEqual(candidate, golden)


class IsolationTests(unittest.TestCase):
    """The vectors must not leak into the consumable corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.packs = sorted(p for p in REPO_ROOT.glob("protocol-*") if p.is_dir())
        cls.shipped = [
            fixture
            for pack in cls.packs
            for path in validate.find_fixture_files(pack)
            for fixture in [validate.load_fixture(path, validate.Report())]
            if fixture is not None
        ]

    def test_no_vector_id_collides_with_a_shipped_or_staged_fixture(self) -> None:
        vector_ids = {
            tomllib.loads(path.read_text(encoding="utf-8"))["id"]
            for name in VECTOR_NAMES
            for path in fixture_paths(name)
        }
        shipped = {str(fixture.data.get("id")) for fixture in self.shipped}
        staged = {
            tomllib.loads(path.read_text(encoding="utf-8"))["id"]
            for path in (REPO_ROOT / "tests" / "vectors" / "resource_estimation").glob("*.toml")
        }
        self.assertEqual(vector_ids & shipped, set())
        self.assertEqual(vector_ids & staged, set())
        self.assertEqual(len(vector_ids), 7)

    def test_vectors_are_outside_every_scan_root(self) -> None:
        # Both discovery paths glob `protocol-*/`. If either ever widened to the
        # whole repository, these packs would be validated as fixtures and
        # counted in the badge.
        self.assertTrue(VECTORS_DIR.is_dir())
        self.assertFalse(str(VECTORS_DIR.relative_to(REPO_ROOT)).startswith("protocol-"))
        self.assertEqual(badge.pack_roots(REPO_ROOT), self.packs)
        for pack in self.packs:
            self.assertNotIn(pack, VECTORS_DIR.parents)

    def test_the_fixture_count_badge_is_unaffected(self) -> None:
        self.assertEqual(
            badge.count_fixtures(REPO_ROOT),
            sum(len(validate.find_fixture_files(pack)) for pack in self.packs),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(badge.main(["--check"]), 0)

    def test_the_shipped_packs_still_validate(self) -> None:
        for pack in self.packs:
            with self.subTest(pack=pack.name):
                self.assertEqual(validate.validate_directory(pack).errors, [])

    def test_no_vector_fixture_pins_live_varying_state(self) -> None:
        # These vectors exist to pin registry behaviour, never a network
        # observation, so nothing in them may assert a value that changes
        # between runs: XDR fixtures round-trip bytes offline, RPC fixtures only
        # assert that a stable field exists, and Soroban fixtures assert success
        # without pinning a host message.
        checked = 0
        for name in VECTOR_NAMES:
            for path in fixture_paths(name):
                with path.open("rb") as handle:
                    fixture = tomllib.load(handle)
                surface = fixture["surface"]
                with self.subTest(vector=name, fixture=fixture["id"]):
                    if surface == "xdr":
                        self.assertEqual(fixture["kind"], "roundtrip")
                    elif surface == "rpc":
                        self.assertEqual(
                            {assertion["kind"] for assertion in fixture["assert"]},
                            {"field-exists"},
                        )
                    else:
                        self.assertEqual(fixture["expect"]["kind"], "simulation-success")
                        self.assertNotIn("message_contains", fixture["expect"])
                    checked += 1
        self.assertEqual(checked, 7)


class DocumentationTests(unittest.TestCase):
    """The vector set documents itself, and docs/contracts.md knows about it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = (VECTORS_DIR / "README.md").read_text(encoding="utf-8")
        cls.contracts = (REPO_ROOT / "docs" / "contracts.md").read_text(encoding="utf-8")

    def test_the_readme_documents_every_vector_and_its_digest(self) -> None:
        for name in VECTOR_NAMES:
            record = load_record(name)
            with self.subTest(vector=name):
                self.assertIn(f"`{name}`", self.readme)
                self.assertIn(record["packDigest"], self.readme)

    def test_the_readme_links_the_authoritative_contract(self) -> None:
        self.assertIn(CONTRACT_REFERENCE, self.readme)
        self.assertIn("CF-02-A", self.readme)

    def test_the_readme_explains_the_ascii_only_path_decision(self) -> None:
        # A reader who "fixes" the vectors by adding a non-ASCII path would make
        # the digests depend on the checkout's Unicode normalization.
        for needle in ("ASCII-only", "normaliz"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.readme)

    def test_relative_links_in_the_readme_resolve(self) -> None:
        # CI's markdown-links job runs lychee over every *.md, and a broken
        # relative link fails it. Check the ones this directory adds on disk.
        import re

        for match in re.finditer(r"\]\(([^)#][^)]*)\)", self.readme):
            target = match.group(1).split("#")[0]
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            with self.subTest(target=target):
                self.assertTrue(
                    (VECTORS_DIR / target).resolve().exists(),
                    f"README.md links to {target!r}, which does not exist",
                )

    def test_docs_contracts_records_cf_02_a_as_implemented(self) -> None:
        self.assertIn("CF-02-A", self.contracts)
        self.assertIn("tests/vectors/cf-02", self.contracts)
        self.assertIn("tests/test_cf02_vectors.py", self.contracts)


if __name__ == "__main__":
    unittest.main()

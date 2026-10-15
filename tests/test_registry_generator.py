"""Tests for tools/registry/generate.py (F-001, CF-02 sections 2 to 6).

Stdlib only: the CI `validate` job runs `unittest discover` on a bare
interpreter. The golden values below were computed independently of the
generator with `sha256sum`, `wc -c` and `printf` (see the digest-input
construction in `GoldenVectorTests`), so a change to the hashing or the
canonical encoding fails here instead of silently changing every digest.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATE_PATH = REPO_ROOT / "tools" / "registry" / "generate.py"

_spec = importlib.util.spec_from_file_location("registry_generate", GENERATE_PATH)
generate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["registry_generate"] = generate
_spec.loader.exec_module(generate)

# ---------------------------------------------------------------- golden pack
# Exact bytes; LF line endings regardless of the platform running the tests.
A_TOML = (
    b'id = "p28-xdr-a"\nprotocol = 28\nsurface = "xdr"\ncategory = "cap-0083"\n'
    b'description = "golden xdr"\nsource_reference = "CAP-0083"\n'
    b'required_capabilities = ["wasm-artifact"]\ninput_file = "a.bin"\n'
    b'type = "StellarValue"\nkind = "decode-success"\nvalue_base64 = "AAAAAA=="\n'
)
B_TOML = (
    b'id = "p28-rpc-b"\nprotocol = 28\nsurface = "rpc"\ncategory = "network"\n'
    b'description = "golden rpc"\nmethod = "get-network"\n\n[[assert]]\n'
    b'kind = "field-exists"\nfield = "passphrase"\n'
)
PAYLOAD = b"\x00\x01payload\xff\n"

# Computed with coreutils, not with this repository's code.
SHA_A = "42392cf9b1e04f2bfee812a9dd7b9dcf3558b93cab10cd98feefde7bea4632ad"
SHA_B = "329422153cc80416d4896c784ec9eb64b8d3f004c0e0064e700ea17c3d3132b5"
SHA_PAYLOAD = "3cf582903ee15410f68cbf85c20b635b3b5ed8a5ed2d46398c35fba3198f1ada"
GOLDEN_DIGEST = "sha256:8546eb66a764b16345ace35acfbd62e54229572dbe4bc41c42ad2ba65a3808b5"

GOLDEN_REGISTRY = f"""{{
  "registryVersion": 1,
  "protocol": 28,
  "fixtures": [
    {{
      "id": "p28-rpc-b",
      "path": "rpc/b.toml",
      "sha256": "{SHA_B}",
      "surface": "rpc",
      "category": "network",
      "description": "golden rpc",
      "payloads": []
    }},
    {{
      "id": "p28-xdr-a",
      "path": "xdr/a.toml",
      "sha256": "{SHA_A}",
      "surface": "xdr",
      "category": "cap-0083",
      "description": "golden xdr",
      "sourceReference": "CAP-0083",
      "requiredCapabilities": [
        "wasm-artifact"
      ],
      "payloads": [
        {{
          "path": "xdr/a.bin",
          "sha256": "{SHA_PAYLOAD}"
        }}
      ]
    }}
  ],
  "packDigest": "{GOLDEN_DIGEST}"
}}
"""

GOLDEN_FILES = {"xdr/a.toml": A_TOML, "rpc/b.toml": B_TOML, "xdr/a.bin": PAYLOAD}


def fixture_toml(fixture_id: str, protocol: int = 28, extra: str = "") -> bytes:
    return (
        f'id = "{fixture_id}"\nprotocol = {protocol}\nsurface = "xdr"\n'
        f'category = "cap-0083"\ndescription = "d {fixture_id}"\n'
        f'type = "StellarValue"\nkind = "decode-success"\nvalue_base64 = "AAAAAA=="\n'
        f"{extra}"
    ).encode("utf-8")


class PackCase(unittest.TestCase):
    """Builds throwaway packs under a temporary directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def make_pack(self, files: dict[str, bytes], name: str = "pack") -> Path:
        root = self.base / name
        root.mkdir(parents=True, exist_ok=True)
        for relative, data in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return root

    def registry(self, files: dict[str, bytes], name: str = "pack") -> dict:
        return json.loads(generate.generate(self.make_pack(files, name)))

    def assert_invalid(self, files: dict[str, bytes], *fragments: str) -> str:
        pack = self.make_pack(files, "invalid")
        with self.assertRaises(generate.PackError) as caught:
            generate.generate(pack)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message


class GoldenVectorTests(PackCase):
    def test_the_registry_is_exactly_the_documented_canonical_form(self) -> None:
        pack = self.make_pack(GOLDEN_FILES)
        self.assertEqual(generate.generate(pack), GOLDEN_REGISTRY)

    def test_the_pack_digest_matches_the_independently_computed_value(self) -> None:
        # The digest input from CF-02 section 5, written out by hand: the
        # header, then "<sha256> <byte length> <path>" per file, sorted by path.
        text = (
            "canary-pack-v1\n"
            f"{SHA_B} {len(B_TOML)} rpc/b.toml\n"
            f"{SHA_PAYLOAD} {len(PAYLOAD)} xdr/a.bin\n"
            f"{SHA_A} {len(A_TOML)} xdr/a.toml\n"
        )
        expected = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(expected, GOLDEN_DIGEST)
        self.assertEqual(self.registry(GOLDEN_FILES)["packDigest"], GOLDEN_DIGEST)

    def test_file_hashes_are_over_the_raw_bytes(self) -> None:
        self.assertEqual(hashlib.sha256(A_TOML).hexdigest(), SHA_A)
        self.assertEqual(hashlib.sha256(B_TOML).hexdigest(), SHA_B)
        self.assertEqual(hashlib.sha256(PAYLOAD).hexdigest(), SHA_PAYLOAD)

    def test_the_encoding_is_lf_two_space_and_ends_in_one_newline(self) -> None:
        text = generate.generate(self.make_pack(GOLDEN_FILES))
        self.assertNotIn("\r", text)
        self.assertTrue(text.endswith("}\n"))
        self.assertFalse(text.endswith("\n\n"))
        self.assertTrue(text.startswith('{\n  "registryVersion": 1,\n  "protocol": 28,\n'))

    def test_output_is_independent_of_creation_order_and_location(self) -> None:
        first = generate.generate(self.make_pack(GOLDEN_FILES, "one"))
        reversed_files = dict(reversed(list(GOLDEN_FILES.items())))
        second = generate.generate(self.make_pack(reversed_files, "a/deeper/two"))
        self.assertEqual(first, second)

    def test_two_runs_give_identical_bytes(self) -> None:
        pack = self.make_pack(GOLDEN_FILES)
        self.assertEqual(generate.generate(pack), generate.generate(pack))

    def test_files_outside_the_pack_do_not_affect_the_output(self) -> None:
        extras = dict(GOLDEN_FILES)
        extras["README.md"] = b"not part of the pack\n"
        extras["notes/todo.txt"] = b"nor this\n"
        extras["registry-v1.json"] = b'{"stale": true}\n'
        # `.git` is skipped entirely, even for a *.toml that would not parse.
        extras[".git/ignored.toml"] = b"not = [valid toml\n"
        self.assertEqual(generate.generate(self.make_pack(extras)), GOLDEN_REGISTRY)

    def test_regenerating_over_an_existing_registry_is_idempotent(self) -> None:
        pack = self.make_pack(GOLDEN_FILES)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(generate.main([str(pack)]), 0)
            first = (pack / "registry-v1.json").read_bytes()
            self.assertEqual(generate.main([str(pack)]), 0)
        self.assertEqual((pack / "registry-v1.json").read_bytes(), first)
        self.assertEqual(first.decode("utf-8"), GOLDEN_REGISTRY)

    def test_the_digest_changes_when_the_pack_changes(self) -> None:
        counter = iter(range(100))

        def digest(files: dict[str, bytes]) -> str:
            return self.registry(files, f"variant-{next(counter)}")["packDigest"]

        changed_byte = dict(GOLDEN_FILES)
        changed_byte["xdr/a.bin"] = PAYLOAD + b"\x00"
        added_file = dict(GOLDEN_FILES)
        added_file["rpc/c.toml"] = fixture_toml("p28-rpc-c")
        renamed = {
            ("xdr/renamed.bin" if k == "xdr/a.bin" else k): v for k, v in GOLDEN_FILES.items()
        }
        renamed["xdr/a.toml"] = A_TOML.replace(b'"a.bin"', b'"renamed.bin"')
        digests = {
            GOLDEN_DIGEST,
            digest(changed_byte),
            digest(added_file),
            digest(renamed),
        }
        self.assertEqual(len(digests), 4, digests)

    def test_a_crlf_checkout_is_a_different_pack(self) -> None:
        # CF-02 section 5: no newline normalization. A checkout that rewrites
        # line endings changes the digest, and that is correct.
        crlf = {k: v.replace(b"\n", b"\r\n") for k, v in GOLDEN_FILES.items() if k != "xdr/a.bin"}
        crlf["xdr/a.bin"] = PAYLOAD
        self.assertNotEqual(self.registry(crlf, "crlf")["packDigest"], GOLDEN_DIGEST)


class OrderingAndMetadataTests(PackCase):
    def test_fixtures_are_sorted_by_id_in_utf8_byte_order(self) -> None:
        ids = ["b-two", "a-one", "Z-upper", "\u00e9-accent"]
        files = {f"x/{i}.toml": fixture_toml(i) for i in ids}
        got = [f["id"] for f in self.registry(files)["fixtures"]]
        self.assertEqual(got, sorted(ids, key=lambda s: s.encode("utf-8")))
        # Uppercase sorts before lowercase in byte order; the accent last.
        self.assertEqual(got[0], "Z-upper")
        self.assertEqual(got[-1], "\u00e9-accent")

    def test_payloads_are_sorted_by_path_and_listed_once_per_fixture(self) -> None:
        files = {
            "d/one.toml": fixture_toml(
                "p28-one", extra='input_file = "z.bin"\nexpected_file = "a.bin"\n'
            ),
            "d/two.toml": fixture_toml("p28-two", extra='input_file = "a.bin"\n'),
            "d/a.bin": b"A",
            "d/z.bin": b"Z",
        }
        registry = self.registry(files)
        one, two = registry["fixtures"]
        self.assertEqual([p["path"] for p in one["payloads"]], ["d/a.bin", "d/z.bin"])
        self.assertEqual([p["path"] for p in two["payloads"]], ["d/a.bin"])

    def test_a_shared_payload_appears_once_in_the_digest_input(self) -> None:
        shared = {
            "d/one.toml": fixture_toml("p28-one", extra='input_file = "s.bin"\n'),
            "d/two.toml": fixture_toml("p28-two", extra='input_file = "s.bin"\n'),
            "d/s.bin": b"shared",
        }
        only_one = {k: v for k, v in shared.items() if k != "d/two.toml"}
        # Two fixtures plus one payload: 3 files. If the shared payload were
        # hashed twice the digest could not equal this independent value.
        lines = ["canary-pack-v1\n"]
        for path in sorted(shared):
            data = shared[path]
            lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {path}\n")
        expected = "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()
        self.assertEqual(self.registry(shared, "shared")["packDigest"], expected)
        self.assertNotEqual(
            self.registry(only_one, "single")["packDigest"], expected
        )

    def test_a_payload_in_a_subdirectory_resolves_relative_to_the_fixture(self) -> None:
        files = {
            "cap/f.toml": fixture_toml("p28-f", extra='expected_file = "data/e.bin"\n'),
            "cap/data/e.bin": b"E",
        }
        payload = self.registry(files)["fixtures"][0]["payloads"][0]
        self.assertEqual(payload["path"], "cap/data/e.bin")

    def test_optional_metadata_is_mirrored_and_otherwise_absent(self) -> None:
        files = {
            "n/bare.toml": fixture_toml("p28-bare"),
            "n/empty-caps.toml": fixture_toml(
                "p28-empty", extra="required_capabilities = []\n"
            ),
            "n/with.toml": fixture_toml(
                "p28-with",
                extra=(
                    'source_reference = "CAP-0083"\n'
                    'required_capabilities = ["soroban-contract", "rpc-client"]\n'
                ),
            ),
        }
        by_id = {f["id"]: f for f in self.registry(files)["fixtures"]}
        self.assertNotIn("sourceReference", by_id["p28-bare"])
        self.assertNotIn("requiredCapabilities", by_id["p28-bare"])
        self.assertEqual(by_id["p28-empty"]["requiredCapabilities"], [])
        self.assertEqual(by_id["p28-with"]["sourceReference"], "CAP-0083")
        # Mirrors the TOML order exactly; it is not re-sorted.
        self.assertEqual(
            by_id["p28-with"]["requiredCapabilities"], ["soroban-contract", "rpc-client"]
        )

    def test_keys_are_written_in_the_documented_order(self) -> None:
        files = {
            "d/f.toml": fixture_toml(
                "p28-f",
                extra='source_reference = "CAP-0083"\nrequired_capabilities = []\n'
                'input_file = "p.bin"\n',
            ),
            "d/p.bin": b"P",
        }
        text = generate.generate(self.make_pack(files))
        order = [
            '"registryVersion"', '"protocol"', '"fixtures"', '"id"', '"path"', '"sha256"',
            '"surface"', '"category"', '"description"', '"sourceReference"',
            '"requiredCapabilities"', '"payloads"', '"packDigest"',
        ]
        positions = [text.index(key) for key in order]
        self.assertEqual(positions, sorted(positions))

    def test_non_ascii_text_is_written_as_utf8_not_escapes(self) -> None:
        files = {"d/f.toml": fixture_toml("p28-f").replace(b"d p28-f", "d \u00e9\u4e2d".encode())}
        text = generate.generate(self.make_pack(files))
        self.assertIn("d \u00e9\u4e2d", text)
        self.assertNotIn("\\u00e9", text)


class RealPackTests(unittest.TestCase):
    PACK = REPO_ROOT / "protocol-28"

    def test_the_shipped_pack_generates_and_conforms_to_the_schema(self) -> None:
        text = generate.generate(self.PACK)
        registry = json.loads(text)
        schema = json.loads(generate.SCHEMA_PATH.read_text(encoding="utf-8"))
        lite = generate._load_schema_validator()
        self.assertEqual(lite.validate(registry, schema), [])
        self.assertEqual(registry["protocol"], 28)

    def test_the_registry_lists_exactly_the_fixture_files_sorted_by_id(self) -> None:
        registry = json.loads(generate.generate(self.PACK))
        on_disk = {}
        for path in self.PACK.rglob("*.toml"):
            on_disk[tomllib.loads(path.read_text(encoding="utf-8"))["id"]] = path
        self.assertEqual({f["id"] for f in registry["fixtures"]}, set(on_disk))
        ids = [f["id"] for f in registry["fixtures"]]
        self.assertEqual(ids, sorted(ids, key=lambda s: s.encode("utf-8")))

    def test_hashes_and_metadata_match_the_files(self) -> None:
        registry = json.loads(generate.generate(self.PACK))
        for entry in registry["fixtures"]:
            path = self.PACK / entry["path"]
            self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            toml = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(entry["id"], toml["id"])
            self.assertEqual(entry["surface"], toml["surface"])
            self.assertEqual(entry["category"], toml["category"])
            self.assertEqual(entry["description"], toml["description"])
            self.assertEqual(entry.get("sourceReference"), toml.get("source_reference"))

    def test_the_pack_digest_matches_a_fresh_computation(self) -> None:
        registry = json.loads(generate.generate(self.PACK))
        lines = ["canary-pack-v1\n"]
        for entry in sorted(registry["fixtures"], key=lambda e: e["path"].encode()):
            data = (self.PACK / entry["path"]).read_bytes()
            lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {entry['path']}\n")
        expected = "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()
        self.assertEqual(registry["packDigest"], expected)

    def test_generation_is_deterministic(self) -> None:
        self.assertEqual(generate.generate(self.PACK), generate.generate(self.PACK))


class InvalidPackTests(PackCase):
    """An invalid pack is rejected with a message that says what to fix."""

    def test_an_empty_pack(self) -> None:
        self.assert_invalid({"README.md": b"x"}, "no *.toml fixture files")

    def test_a_pack_directory_that_does_not_exist(self) -> None:
        with self.assertRaises(generate.PackError) as caught:
            generate.generate(self.base / "missing")
        self.assertIn("is not a directory", str(caught.exception))

    def test_invalid_toml_names_the_file(self) -> None:
        self.assert_invalid({"d/bad.toml": b"id = [unterminated\n"}, "d/bad.toml", "invalid TOML")

    def test_non_utf8_fixture_content(self) -> None:
        self.assert_invalid({"d/bad.toml": b'id = "\xff\xfe"\n'}, "d/bad.toml", "UTF-8")

    def test_missing_required_fields(self) -> None:
        for field in ("id", "surface", "category", "description"):
            lines = [
                line
                for line in fixture_toml("p28-f").decode().splitlines(keepends=True)
                if not line.startswith(field)
            ]
            self.assert_invalid(
                {"d/f.toml": "".join(lines).encode()}, "d/f.toml", f"'{field}'"
            )

    def test_missing_protocol(self) -> None:
        text = fixture_toml("p28-f").decode().replace("protocol = 28\n", "")
        self.assert_invalid({"d/f.toml": text.encode()}, "d/f.toml", "'protocol'")

    def test_an_unknown_surface(self) -> None:
        text = fixture_toml("p28-f").decode().replace('surface = "xdr"', 'surface = "grpc"')
        self.assert_invalid({"d/f.toml": text.encode()}, "'surface'", "'grpc'")

    def test_duplicate_fixture_ids_name_both_files(self) -> None:
        self.assert_invalid(
            {"a/one.toml": fixture_toml("p28-same"), "b/two.toml": fixture_toml("p28-same")},
            "duplicate fixture id 'p28-same'",
            "a/one.toml",
            "b/two.toml",
        )

    def test_fixtures_for_two_protocols_are_rejected(self) -> None:
        self.assert_invalid(
            {
                "a/one.toml": fixture_toml("p28-one", protocol=28),
                "b/two.toml": fixture_toml("p29-two", protocol=29),
            },
            "exactly one protocol",
            "28",
            "29",
        )

    def test_a_missing_payload(self) -> None:
        self.assert_invalid(
            {"d/f.toml": fixture_toml("p28-f", extra='input_file = "nope.bin"\n')},
            "d/f.toml",
            "input_file",
            "does not exist",
        )

    def test_a_payload_reference_that_escapes_the_pack(self) -> None:
        for reference in ("../secret.bin", "a/../../x", "/etc/passwd", "C:/x.bin", "a\\\\b.bin"):
            self.assert_invalid(
                {
                    "d/f.toml": fixture_toml(
                        "p28-f", extra=f"input_file = {json.dumps(reference)}\n"
                    ),
                    "secret.bin": b"s",
                },
                "d/f.toml",
                "input_file",
            )

    def test_an_empty_or_dot_segment_in_a_payload_reference(self) -> None:
        for reference in ("", "./a.bin", "a//b.bin"):
            self.assert_invalid(
                {"d/f.toml": fixture_toml("p28-f", extra=f"input_file = {json.dumps(reference)}\n")},
                "input_file",
            )

    def test_a_non_string_payload_reference(self) -> None:
        self.assert_invalid(
            {"d/f.toml": fixture_toml("p28-f", extra="input_file = 5\n")}, "must be a string"
        )

    def test_invalid_required_capabilities(self) -> None:
        self.assert_invalid(
            {"d/f.toml": fixture_toml("p28-f", extra='required_capabilities = "x"\n')},
            "required_capabilities",
        )

    def test_a_path_with_a_control_character(self) -> None:
        with self.assertRaises(generate.PackError) as caught:
            generate.check_pack_path("a/b\x07c.toml")
        self.assertIn("control character", str(caught.exception))

    def test_pack_path_rules(self) -> None:
        for bad in ("", "/abs.toml", "a//b.toml", "a/./b.toml", "a/../b.toml", "a\\b.toml"):
            with self.assertRaises(generate.PackError, msg=repr(bad)):
                generate.check_pack_path(bad)
        generate.check_pack_path("cap-0083/\u00e9.toml")  # valid UTF-8 is fine

    def test_names_that_differ_only_by_case(self) -> None:
        root = self.base / "case"
        (root / "d").mkdir(parents=True)
        (root / "d" / "A.toml").write_bytes(fixture_toml("p28-upper"))
        try:
            (root / "d" / "a.toml").write_bytes(fixture_toml("p28-lower"))
        except OSError:
            self.skipTest("file system cannot hold two names differing only by case")
        if len(list((root / "d").iterdir())) != 2:
            self.skipTest("file system is case insensitive")
        with self.assertRaises(generate.PackError) as caught:
            generate.generate(root)
        self.assertIn("differ only by case", str(caught.exception))

    def test_a_symbolic_link_to_a_file(self) -> None:
        pack = self.make_pack(GOLDEN_FILES, "linked")
        try:
            os.symlink(pack / "xdr" / "a.bin", pack / "xdr" / "link.bin")
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        with self.assertRaises(generate.PackError) as caught:
            generate.generate(pack)
        self.assertIn("symbolic link", str(caught.exception))
        self.assertIn("xdr/link.bin", str(caught.exception))

    def test_a_symbolic_link_to_a_directory_is_not_followed(self) -> None:
        outside = self.make_pack({"x.toml": fixture_toml("p28-outside")}, "outside")
        pack = self.make_pack(GOLDEN_FILES, "dirlink")
        try:
            os.symlink(outside, pack / "escape", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        with self.assertRaises(generate.PackError) as caught:
            generate.generate(pack)
        self.assertIn("symbolic link", str(caught.exception))

    def test_a_file_name_that_is_not_utf8(self) -> None:
        if sys.platform == "win32" or sys.platform == "darwin":
            self.skipTest("file names are always valid Unicode on this platform")
        pack = self.make_pack(GOLDEN_FILES, "latin1")
        raw = os.fsencode(str(pack)) + b"/d/caf\xe9.toml"
        os.makedirs(os.path.dirname(raw), exist_ok=True)
        with open(raw, "wb") as handle:
            handle.write(fixture_toml("p28-latin"))
        with self.assertRaises(generate.PackError) as caught:
            generate.generate(pack)
        self.assertIn("not valid UTF-8", str(caught.exception))

    def test_a_file_over_the_size_limit(self) -> None:
        pack = self.make_pack(GOLDEN_FILES, "big")
        (pack / "xdr" / "big.bin").write_bytes(b"x" * 301)
        original = generate.MAX_FILE_BYTES
        generate.MAX_FILE_BYTES = 300  # the golden files are all smaller
        try:
            with self.assertRaises(generate.PackError) as caught:
                generate.generate(pack)
        finally:
            generate.MAX_FILE_BYTES = original
        self.assertIn("xdr/big.bin", str(caught.exception))
        self.assertIn("limit", str(caught.exception))

    def test_too_many_files(self) -> None:
        pack = self.make_pack(GOLDEN_FILES, "many")
        original = generate.MAX_FILES
        generate.MAX_FILES = 2
        try:
            with self.assertRaises(generate.PackError) as caught:
                generate.generate(pack)
        finally:
            generate.MAX_FILES = original
        self.assertIn("more than 2 files", str(caught.exception))

    def test_a_pack_over_the_total_size_limit(self) -> None:
        pack = self.make_pack(GOLDEN_FILES, "total")
        original = generate.MAX_TOTAL_BYTES
        generate.MAX_TOTAL_BYTES = 100
        try:
            with self.assertRaises(generate.PackError) as caught:
                generate.generate(pack)
        finally:
            generate.MAX_TOTAL_BYTES = original
        self.assertIn("in total", str(caught.exception))

    def test_a_path_nested_too_deeply(self) -> None:
        deep = "/".join(["d"] * (generate.MAX_DEPTH + 2)) + "/f.toml"
        self.assert_invalid({deep: fixture_toml("p28-deep")}, "deeper than")


class CommandLineTests(PackCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = generate.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_writes_registry_v1_json_into_the_pack_by_default(self) -> None:
        pack = self.make_pack(GOLDEN_FILES)
        code, out, _ = self.run_main(str(pack))
        self.assertEqual(code, 0)
        self.assertIn("registry-v1.json", out)
        self.assertEqual(
            (pack / "registry-v1.json").read_bytes(), GOLDEN_REGISTRY.encode("utf-8")
        )

    def test_output_flag_writes_elsewhere(self) -> None:
        pack = self.make_pack(GOLDEN_FILES)
        target = self.base / "out" / "reg.json"
        code, _, _ = self.run_main(str(pack), "--output", str(target))
        self.assertEqual(code, 0)
        self.assertEqual(target.read_bytes(), GOLDEN_REGISTRY.encode("utf-8"))
        self.assertFalse((pack / "registry-v1.json").exists())

    def test_an_invalid_pack_exits_nonzero_and_writes_nothing(self) -> None:
        pack = self.make_pack({"d/bad.toml": b"id = [oops\n"})
        code, out, err = self.run_main(str(pack))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error:", err)
        self.assertIn("d/bad.toml", err)
        self.assertIn("no registry written", err)
        self.assertFalse((pack / "registry-v1.json").exists())

    def test_output_and_stdout_are_mutually_exclusive(self) -> None:
        pack = self.make_pack(GOLDEN_FILES)
        with self.assertRaises(SystemExit) as caught:
            self.run_main(str(pack), "--stdout", "--output", str(self.base / "x.json"))
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

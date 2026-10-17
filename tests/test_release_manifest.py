"""Tests for tools/release/manifest.py (F-010, CF-06 section 3).

Stdlib only: the CI `validate` job runs `unittest discover` on a bare
interpreter.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pack_support as support  # noqa: E402

pack = support.pack
manifest = support.manifest
REPO_ROOT = support.REPO_ROOT

_schema_spec = support.load_tool(
    "canary_jsonschema_lite", "tools/validate/jsonschema_lite.py"
)
SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "release-manifest-v1.schema.json").read_text(encoding="utf-8")
)

KEY_ORDER = [
    "manifestVersion",
    "name",
    "version",
    "protocols",
    "revision",
    "packDigest",
    "registryVersion",
    "archive",
    "compatibleEngines",
]


def schema_problems(document: dict) -> list[str]:
    return _schema_spec.validate(document, SCHEMA)


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        support.write_pack(self.root)
        self.info = pack.build_archive(self.root / "pack", self.root / "out", "1.4.2")

    def build(self, **overrides) -> dict:
        kwargs = {"revision": support.REVISION, "engines": ["0.2.0"]}
        kwargs.update(overrides)
        return manifest.build_manifest(self.info.path, **kwargs)

    def test_manifest_records_the_archive_facts(self) -> None:
        result = self.build()
        data = self.info.path.read_bytes()
        self.assertEqual(result["archive"]["file"], "protocolcanary-fixtures-1.4.2.tar.gz")
        self.assertEqual(result["archive"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["archive"]["bytes"], len(data))
        self.assertEqual(result["archive"]["entries"], self.info.entries)
        self.assertEqual(result["archive"]["uncompressedBytes"], self.info.uncompressed_bytes)
        self.assertEqual(result["packDigest"], support.GOLDEN_DIGEST)
        self.assertEqual(result["version"], "1.4.2")
        self.assertEqual(result["protocols"], [28])
        self.assertEqual(result["registryVersion"], 1)
        self.assertEqual(result["revision"], support.REVISION)

    def test_manifest_matches_the_schema_and_key_order(self) -> None:
        result = self.build()
        self.assertEqual(schema_problems(result), [])
        self.assertEqual(list(result), KEY_ORDER)

    def test_rendered_json_is_canonical(self) -> None:
        text = manifest.render_manifest(self.build())
        self.assertTrue(text.endswith("}\n"))
        self.assertNotIn("\r", text)
        self.assertEqual(json.loads(text), self.build())
        self.assertEqual(text, manifest.render_manifest(self.build()))

    def test_engines_are_never_marked_verified(self) -> None:
        result = self.build(engines=["0.2.0", "0.1.1", "0.2.0"])
        self.assertEqual(
            result["compatibleEngines"],
            [
                {"version": "0.1.1", "verified": False},
                {"version": "0.2.0", "verified": False},
            ],
        )

    def test_engine_ranges_are_refused(self) -> None:
        for bad in ["0.1.x", "^0.2.0", ">=0.2", "0.2"]:
            with self.subTest(bad):
                with self.assertRaisesRegex(manifest.ManifestError, "exact"):
                    self.build(engines=[bad])

    def test_no_engines_gives_an_empty_list(self) -> None:
        self.assertEqual(self.build(engines=[])["compatibleEngines"], [])

    def test_nonexistent_archive_is_a_clear_error(self) -> None:
        with self.assertRaisesRegex(manifest.ManifestError, "archive not found"):
            manifest.build_manifest(
                self.root / "missing.tar.gz", revision=support.REVISION, engines=[]
            )

    def test_revision_must_be_a_full_commit(self) -> None:
        for bad in ["abc123", support.REVISION.upper(), support.REVISION[:-1], ""]:
            with self.subTest(bad):
                with self.assertRaisesRegex(manifest.ManifestError, "40-character"):
                    self.build(revision=bad)

    def test_archive_name_must_carry_a_version(self) -> None:
        renamed = self.root / "pack.tar.gz"
        renamed.write_bytes(self.info.path.read_bytes())
        with self.assertRaisesRegex(manifest.ManifestError, "version"):
            manifest.build_manifest(renamed, revision=support.REVISION, engines=[])

    def test_not_a_gzip_archive_is_refused(self) -> None:
        junk = self.root / "protocolcanary-fixtures-1.0.0.tar.gz"
        junk.write_bytes(b"this is not an archive")
        with self.assertRaisesRegex(manifest.ManifestError, "not a readable"):
            manifest.build_manifest(junk, revision=support.REVISION, engines=[])

    def test_archive_without_a_registry_is_refused(self) -> None:
        path = support.tar_gz(
            [support.entry("xdr/a.toml", support.A_TOML)],
            self.root / "protocolcanary-fixtures-1.0.0.tar.gz",
        )
        with self.assertRaisesRegex(manifest.ManifestError, "registry.json"):
            manifest.build_manifest(path, revision=support.REVISION, engines=[])

    def test_a_registry_that_does_not_describe_its_archive_is_refused(self) -> None:
        members = dict(support.valid_members(self.root / "again"))
        # Same registry, different payload bytes.
        entries = []
        for info, data in support.valid_members(self.root / "again2"):
            if info.name == "xdr/a.bin":
                data = b"tampered"
                info = support.file_info(info.name, len(data))
            entries.append((info, data))
        path = support.tar_gz(entries, self.root / "protocolcanary-fixtures-1.0.0.tar.gz")
        self.assertTrue(members)
        with self.assertRaisesRegex(manifest.ManifestError, "packDigest"):
            manifest.build_manifest(path, revision=support.REVISION, engines=[])

    def test_sha256_line_is_in_sha256sum_format(self) -> None:
        result = self.build()
        line = manifest.sha256_line(result)
        self.assertEqual(
            line, f"{result['archive']['sha256']}  protocolcanary-fixtures-1.4.2.tar.gz\n"
        )

    def test_schema_rejects_a_range_and_unknown_fields(self) -> None:
        document = self.build()
        ranged = json.loads(json.dumps(document))
        ranged["compatibleEngines"] = [{"version": "0.1.x", "verified": False}]
        self.assertTrue(schema_problems(ranged))
        extra = json.loads(json.dumps(document))
        extra["builtAt"] = "2026-10-16T00:00:00Z"
        self.assertTrue(schema_problems(extra))
        missing = json.loads(json.dumps(document))
        del missing["packDigest"]
        self.assertTrue(schema_problems(missing))

    def test_cli_writes_the_manifest_next_to_the_archive(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = manifest.main(
                [
                    str(self.info.path),
                    "--revision",
                    support.REVISION,
                    "--engine",
                    "0.2.0",
                    "--sha256-file",
                    str(self.root / "out" / "x.sha256"),
                ]
            )
        self.assertEqual(code, 0)
        written = self.info.path.with_name("protocolcanary-fixtures-1.4.2.manifest.json")
        self.assertEqual(json.loads(written.read_text(encoding="utf-8")), self.build())
        self.assertTrue((self.root / "out" / "x.sha256").read_text().endswith(".tar.gz\n"))

    def test_cli_exit_code_for_a_missing_archive(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = manifest.main(
                [str(self.root / "nope.tar.gz"), "--revision", support.REVISION]
            )
        self.assertEqual(code, 1)
        self.assertIn("archive not found", err.getvalue())
        self.assertFalse(list(self.root.glob("nope*.json")))


if __name__ == "__main__":
    unittest.main()

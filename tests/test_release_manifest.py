"""Tests for tools/release/manifest.py (F-010, CF-06 section 3).

Stdlib only: the CI `validate` job runs `unittest discover` on a bare
interpreter.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import subprocess
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


def _ustar_header_claiming(name: str, size: int) -> bytes:
    """A hand-written ustar header whose size field claims ``size`` bytes.

    The data blocks are never written: ``inspect_archive`` must refuse the
    archive from the claimed size alone, long before the stream ends.
    """
    header = bytearray(512)
    header[0 : len(name)] = name.encode("utf-8")
    header[100:108] = b"0000644\0"
    header[108:116] = b"0000000\0"
    header[116:124] = b"0000000\0"
    header[124:136] = ("%011o" % size).encode("ascii") + b"\0"
    header[136:148] = b"00000000000\0"
    header[156:157] = b"0"  # REGTYPE
    header[257:265] = b"ustar\00000"
    header[148:156] = b"        "
    checksum = sum(header)
    header[148:156] = ("%06o\0 " % checksum).encode("ascii")
    return bytes(header)


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


class HostileRegistryArchiveTests(unittest.TestCase):
    """Archives whose registry.json is present but unusable (F-061 gaps)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def archive_with_registry(self, registry_bytes: bytes) -> Path:
        entries = [
            (info, data)
            for info, data in support.valid_members(self.root / "src")
            if info.name != manifest.REGISTRY_NAME
        ]
        entries.append(support.entry(manifest.REGISTRY_NAME, registry_bytes))
        return support.tar_gz(entries, self.root / "protocolcanary-fixtures-1.0.0.tar.gz")

    def golden_registry(self) -> dict:
        for info, data in support.valid_members(self.root / "golden"):
            if info.name == manifest.REGISTRY_NAME:
                return json.loads(data.decode("utf-8"))
        raise AssertionError("golden pack has no registry")

    def build(self, path: Path, **overrides) -> dict:
        kwargs = {"revision": support.REVISION, "engines": []}
        kwargs.update(overrides)
        return manifest.build_manifest(path, **kwargs)

    def test_a_symbolic_link_member_is_refused(self) -> None:
        path = support.tar_gz(
            [support.link_entry("xdr/evil.toml", "/etc/passwd", hard=False)],
            self.root / "protocolcanary-fixtures-1.0.0.tar.gz",
        )
        with self.assertRaisesRegex(manifest.ManifestError, "not a regular file"):
            self.build(path)

    def test_an_archive_claiming_over_the_read_cap_is_refused(self) -> None:
        header = _ustar_header_claiming("big.bin", manifest._READ_CAP + 1)
        blob = self.root / "protocolcanary-fixtures-9.9.9.tar.gz"
        raw = io.BytesIO()
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=1) as gz:
            gz.write(header + b"\0" * 1024)
        blob.write_bytes(raw.getvalue())
        with self.assertRaisesRegex(manifest.ManifestError, "far larger"):
            self.build(blob)

    def test_an_explicit_version_must_be_exact(self) -> None:
        support.write_pack(self.root)
        info = pack.build_archive(self.root / "pack", self.root / "out", "1.0.0")
        for bad in ["1.0", "v1.0.0", "01.0.0", ""]:
            with self.subTest(bad):
                with self.assertRaisesRegex(manifest.ManifestError, "MAJOR.MINOR.PATCH"):
                    self.build(info.path, version=bad)
        self.assertEqual(self.build(info.path, version="2.3.4")["version"], "2.3.4")

    def test_a_registry_that_is_not_json_is_refused(self) -> None:
        with self.assertRaisesRegex(manifest.ManifestError, "not valid JSON"):
            self.build(self.archive_with_registry(b"{not json"))

    def test_a_registry_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaisesRegex(manifest.ManifestError, "must be a JSON object"):
            self.build(self.archive_with_registry(b"[1, 2, 3]"))

    def test_a_registry_without_an_integer_registry_version_is_refused(self) -> None:
        registry = self.golden_registry()
        registry["registryVersion"] = "1"
        with self.assertRaisesRegex(manifest.ManifestError, "integer registryVersion"):
            self.build(self.archive_with_registry(json.dumps(registry).encode("utf-8")))

    def test_a_registry_without_an_integer_protocol_is_refused(self) -> None:
        registry = self.golden_registry()
        registry["protocol"] = True
        with self.assertRaisesRegex(manifest.ManifestError, "integer protocol"):
            self.build(self.archive_with_registry(json.dumps(registry).encode("utf-8")))

    def test_a_registry_without_a_valid_pack_digest_is_refused(self) -> None:
        registry = self.golden_registry()
        registry["packDigest"] = "sha256:not-a-digest"
        with self.assertRaisesRegex(manifest.ManifestError, "valid packDigest"):
            self.build(self.archive_with_registry(json.dumps(registry).encode("utf-8")))


class ManifestCliTests(unittest.TestCase):
    """The CLI's write paths and the module entry point (F-061 gaps)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        support.write_pack(self.root)
        self.info = pack.build_archive(self.root / "pack", self.root / "out", "1.4.2")

    def test_cli_success_without_a_sha256_file(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = manifest.main([str(self.info.path), "--revision", support.REVISION])
        self.assertEqual(code, 0)
        self.assertIn("OK: wrote", out.getvalue())
        written = self.info.path.with_name("protocolcanary-fixtures-1.4.2.manifest.json")
        self.assertTrue(written.is_file())

    def test_cli_reports_an_unwritable_output_path(self) -> None:
        blocker = self.root / "blocker"
        blocker.write_bytes(b"i am a file, not a directory")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = manifest.main(
                [
                    str(self.info.path),
                    "--revision",
                    support.REVISION,
                    "--output",
                    str(blocker / "nested" / "m.json"),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("could not write the manifest", err.getvalue())

    def test_module_entry_point_exits_zero_on_success(self) -> None:
        run = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "release" / "manifest.py"),
                str(self.info.path),
                "--revision",
                support.REVISION,
            ],
            capture_output=True,
            text=True,
            cwd=self.root,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("OK: wrote", run.stdout)

    def test_module_entry_point_exits_one_on_failure(self) -> None:
        run = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "release" / "manifest.py"),
                str(self.root / "absent.tar.gz"),
                "--revision",
                support.REVISION,
            ],
            capture_output=True,
            text=True,
            cwd=self.root,
        )
        self.assertEqual(run.returncode, 1)
        self.assertIn("FAILED: no manifest written", run.stderr)

    def test_module_main_guard_runs_in_process(self) -> None:
        import runpy

        argv = sys.argv
        sys.argv = ["manifest.py", str(self.info.path), "--revision", support.REVISION]
        self.addCleanup(setattr, sys, "argv", argv)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(
                    str(REPO_ROOT / "tools" / "release" / "manifest.py"),
                    run_name="__main__",
                )
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

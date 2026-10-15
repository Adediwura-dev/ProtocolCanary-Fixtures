#!/usr/bin/env python3
r"""Generate the release manifest for a fixture-pack archive (CF-06 section 3).

Reads a ``protocolcanary-fixtures-<version>.tar.gz`` built by
``tools/pack/pack.py`` and writes ``protocolcanary-fixtures-<version>.manifest.json``
next to it (or to ``--output``). The manifest is described by
``schemas/release-manifest-v1.schema.json``.

What is recorded, and where it comes from:

* ``archive.sha256`` and ``archive.bytes``: the compressed file itself;
* ``archive.entries`` and ``archive.uncompressedBytes``: counted from the tar
  stream (every entry, and the bytes of the regular files);
* ``packDigest``, ``registryVersion`` and ``protocols``: read from the archive's
  ``registry.json`` after the digest is recomputed from the archived files, so
  a registry that does not describe its own archive is refused;
* ``revision``: the full git commit of the pack source, given by the caller.
  It is provenance only (CF-06 section 3);
* ``compatibleEngines``: exact engine versions given by the caller, each marked
  ``verified: false``. ``true`` is only ever correct when that exact engine and
  pack were run together and the run is recorded under CF-04, so this tool does
  not set it.

Nothing is executed or extracted to disk. A missing, truncated or malformed
archive fails with a message and writes nothing.

Usage:
    python3 tools/release/manifest.py ARCHIVE --revision COMMIT [--engine 0.2.0]
        [--output PATH]

Requires Python 3.11+ and nothing outside the standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path

NAME = "protocolcanary-fixtures"
MANIFEST_VERSION = 1
REGISTRY_NAME = "registry.json"
# One 128 MiB cap on what is read while counting, well above the 16 MiB
# archive limit (D-06), so a decompression bomb cannot exhaust memory here.
_READ_CAP = 128 * 1024 * 1024

_ARCHIVE_RE = re.compile(rf"{NAME}-((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\.tar\.gz")
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_ENGINE_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


class ManifestError(Exception):
    """The manifest cannot be produced. The message says why."""


def _utf8_key(text: str) -> bytes:
    return text.encode("utf-8")


def _pack_digest(files: dict[str, bytes]) -> str:
    """CF-02 section 5 over the archived pack files (all but registry.json)."""
    lines = ["canary-pack-v1\n"]
    for path in sorted(files, key=_utf8_key):
        data = files[path]
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {path}\n")
    return "sha256:" + hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def inspect_archive(path: Path) -> dict:
    """Count entries and bytes, and read the registry, without extracting."""
    entries = 0
    uncompressed = 0
    files: dict[str, bytes] = {}
    read_total = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                entries += 1
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ManifestError(
                        f"{member.name!r} is not a regular file or directory; "
                        "this is not a pack archive"
                    )
                read_total += member.size
                if read_total > _READ_CAP:
                    raise ManifestError("the archive is far larger than any valid pack")
                stream = archive.extractfile(member)
                data = stream.read() if stream is not None else b""
                files[member.name] = data
                uncompressed += len(data)
    except (tarfile.TarError, EOFError, OSError, UnicodeError) as error:
        raise ManifestError(f"{path} is not a readable .tar.gz archive: {error}") from None
    return {"entries": entries, "uncompressed": uncompressed, "files": files}


def build_manifest(
    archive: Path,
    *,
    revision: str,
    engines: list[str],
    version: str | None = None,
) -> dict:
    """The manifest mapping, in the key order the contract shows."""
    if not archive.is_file():
        raise ManifestError(f"archive not found: {archive}")
    match = _ARCHIVE_RE.fullmatch(archive.name)
    if version is None:
        if not match:
            raise ManifestError(
                f"cannot read the version from {archive.name!r}; expected "
                f"{NAME}-MAJOR.MINOR.PATCH.tar.gz"
            )
        version = match.group(1)
    elif not re.fullmatch(_ENGINE_RE.pattern, version):
        raise ManifestError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    if not _REVISION_RE.fullmatch(revision):
        raise ManifestError(
            "revision must be a full 40-character lowercase git commit hash"
        )
    for engine in engines:
        if not _ENGINE_RE.fullmatch(engine):
            raise ManifestError(
                f"engine version {engine!r} must be an exact MAJOR.MINOR.PATCH; "
                "a range is never a verified combination"
            )

    data = archive.read_bytes()
    content = inspect_archive(archive)
    files: dict[str, bytes] = content["files"]
    registry_bytes = files.get(REGISTRY_NAME)
    if registry_bytes is None:
        raise ManifestError(f"the archive has no {REGISTRY_NAME} at its root")
    try:
        registry = json.loads(registry_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"{REGISTRY_NAME} is not valid JSON: {error}") from None
    if not isinstance(registry, dict):
        raise ManifestError(f"{REGISTRY_NAME} must be a JSON object")

    registry_version = registry.get("registryVersion")
    protocol = registry.get("protocol")
    declared = registry.get("packDigest")
    if not isinstance(registry_version, int) or isinstance(registry_version, bool):
        raise ManifestError(f"{REGISTRY_NAME} has no integer registryVersion")
    if not isinstance(protocol, int) or isinstance(protocol, bool):
        raise ManifestError(f"{REGISTRY_NAME} has no integer protocol")
    if not isinstance(declared, str) or not _DIGEST_RE.fullmatch(declared):
        raise ManifestError(f"{REGISTRY_NAME} has no valid packDigest")

    pack_files = {p: b for p, b in files.items() if p != REGISTRY_NAME}
    actual = _pack_digest(pack_files)
    if actual != declared:
        raise ManifestError(
            f"{REGISTRY_NAME} declares packDigest {declared} but the archived files "
            f"hash to {actual}; the archive is not the pack its registry describes"
        )

    return {
        "manifestVersion": MANIFEST_VERSION,
        "name": NAME,
        "version": version,
        "protocols": [protocol],
        "revision": revision,
        "packDigest": declared,
        "registryVersion": registry_version,
        "archive": {
            "file": archive.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "entries": content["entries"],
            "uncompressedBytes": content["uncompressed"],
        },
        "compatibleEngines": [
            {"version": engine, "verified": False} for engine in sorted(set(engines))
        ],
    }


def render_manifest(manifest: dict) -> str:
    """UTF-8, LF, two-space indent, one trailing newline."""
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def sha256_line(manifest: dict) -> str:
    """The ``sha256sum``-format line for the archive (CF-06 section 2)."""
    archive = manifest["archive"]
    return f"{archive['sha256']}  {archive['file']}\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manifest.py",
        description="Generate the CF-06 release manifest for a fixture-pack archive.",
    )
    parser.add_argument("archive", type=Path, help="the .tar.gz built by tools/pack/pack.py")
    parser.add_argument(
        "--revision",
        required=True,
        help="full 40-character git commit hash of the pack source",
    )
    parser.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="VERSION",
        help=(
            "an exact engine version to list as a compatible engine, "
            "recorded as unverified; may be given more than once"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help="write the manifest here (default: next to the archive, .manifest.json)",
    )
    parser.add_argument(
        "--sha256-file",
        type=Path,
        metavar="PATH",
        help="also write the sha256sum-format checksum line here",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = build_manifest(args.archive, revision=args.revision, engines=args.engine)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        print("FAILED: no manifest written", file=sys.stderr)
        return 1
    output = args.output or args.archive.with_name(
        args.archive.name.removesuffix(".tar.gz") + ".manifest.json"
    )
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(render_manifest(manifest).encode("utf-8"))
        if args.sha256_file is not None:
            args.sha256_file.parent.mkdir(parents=True, exist_ok=True)
            args.sha256_file.write_bytes(sha256_line(manifest).encode("utf-8"))
    except OSError as error:
        print(f"error: could not write the manifest: {error}", file=sys.stderr)
        return 1
    print(f"OK: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

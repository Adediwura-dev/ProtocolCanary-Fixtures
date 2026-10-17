#!/usr/bin/env python3
r"""Build the deterministic release archive for one protocol pack (CF-06).

Packs the fixtures of a pack directory (for example ``protocol-28/``) and its
generated ``registry.json`` into a ``.tar.gz`` that is byte-for-byte
reproducible: building the same pack twice gives the same SHA-256.

The rules implemented here are the ones in
``Protocol-Canary/docs/contracts/cf-06-fixture-releases.md`` section 2, which
is authoritative:

* content is exactly the CF-02 pack membership (every ``*.toml`` file and every
  file a fixture names in ``input_file`` or ``expected_file``) plus
  ``registry.json`` at the archive root. No README, no ``.git``, no links;
* entries are sorted by path in UTF-8 byte order, directories before the files
  inside them;
* owner uid and gid are 0, owner and group names are empty, mtime is 0, files
  are mode 0644 and directories 0755, with no extended attributes;
* the gzip stream carries no file name and a zero timestamp (``gzip -n``);
* the D-06 limits hold for the archive as a whole: at most 2,000 entries,
  16 MiB uncompressed, 1 MiB per file and depth 8.

An invalid pack is never repaired or skipped: the tool prints what is wrong and
exits non-zero without writing an archive.

Usage:
    python3 tools/pack/pack.py PACK --version 1.0.0 [--output-dir DIR]

Requires Python 3.11+ and nothing outside the standard library.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_PREFIX = "protocolcanary-fixtures"
REGISTRY_NAME = "registry.json"
FILE_MODE = 0o644
DIRECTORY_MODE = 0o755

MAX_ENTRIES = 2000
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_DEPTH = 8

_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _load_generator():
    path = REPO_ROOT / "tools" / "registry" / "generate.py"
    spec = importlib.util.spec_from_file_location("registry_generate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("registry_generate", module)
    spec.loader.exec_module(module)
    return module


generate = _load_generator()


class PackError(Exception):
    """The pack cannot be archived. The message says what to fix."""


@dataclass(frozen=True)
class ArchiveInfo:
    """Facts about a written archive, as recorded in the release manifest."""

    path: Path
    sha256: str
    size: int
    entries: int
    uncompressed_bytes: int
    pack_digest: str
    protocol: int


def archive_name(version: str) -> str:
    return f"{ARCHIVE_PREFIX}-{version}.tar.gz"


def check_version(version: str) -> None:
    if not _VERSION_RE.fullmatch(version):
        raise PackError(
            f"version {version!r} is not MAJOR.MINOR.PATCH (for example 1.0.0)"
        )


def collect_members(pack: Path) -> tuple[dict[str, bytes], str, int]:
    """The files of the archive, ``{path: bytes}``, plus digest and protocol.

    Membership is the CF-02 set; ``registry.json`` is generated from it. Any
    problem the registry generator finds (a link, an unsafe path, a missing
    payload, two protocols) stops the build.
    """
    try:
        scanned = generate.scan_pack(pack)
        registry = generate.build_registry(scanned)
        generate.check_against_schema(registry)
    except generate.PackError as error:
        raise PackError(str(error)) from None
    except OSError as error:
        raise PackError(f"could not read the pack: {error}") from None

    members: dict[str, bytes] = {}
    for fixture in registry["fixtures"]:
        members[fixture["path"]] = scanned[fixture["path"]]
        for payload in fixture["payloads"]:
            members[payload["path"]] = scanned[payload["path"]]
    members[REGISTRY_NAME] = generate.render_registry(registry).encode("utf-8")
    return members, registry["packDigest"], registry["protocol"]


def directories_of(paths) -> list[str]:
    """Every parent directory of ``paths``, sorted in UTF-8 byte order."""
    found: set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for end in range(1, len(parts) + 1):
            found.add("/".join(parts[:end]))
    return sorted(found, key=lambda p: p.encode("utf-8"))


def check_limits(members: dict[str, bytes], directories: list[str]) -> None:
    """The D-06 limits, applied to the archive as it will be written."""
    entries = len(members) + len(directories)
    if entries > MAX_ENTRIES:
        raise PackError(
            f"the archive would have {entries} entries, over the limit of {MAX_ENTRIES}"
        )
    total = 0
    for path, data in members.items():
        if len(data) > MAX_FILE_BYTES:
            raise PackError(
                f"{path} is {len(data)} bytes, over the {MAX_FILE_BYTES}-byte limit for one file"
            )
        if len(path.split("/")) > MAX_DEPTH:
            raise PackError(f"{path} is deeper than {MAX_DEPTH} path segments")
        total += len(data)
    if total > MAX_TOTAL_BYTES:
        raise PackError(
            f"the archive would hold {total} bytes uncompressed, over the limit of {MAX_TOTAL_BYTES}"
        )


def _tar_info(name: str, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = DIRECTORY_MODE if directory else FILE_MODE
    info.size = 0 if directory else size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def render_archive(members: dict[str, bytes]) -> bytes:
    """The archive bytes for ``members``. Deterministic for equal input."""
    directories = directories_of(members)
    check_limits(members, directories)

    names = sorted(set(members) | set(directories), key=lambda p: p.encode("utf-8"))
    raw = io.BytesIO()
    # `filename=""` and `mtime=0`: the gzip header carries neither a name nor
    # a time, so the compressed bytes do not depend on where or when it ran.
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as stream:
        with tarfile.open(
            fileobj=stream, mode="w", format=tarfile.PAX_FORMAT, encoding="utf-8"
        ) as archive:
            for name in names:
                if name in members:
                    data = members[name]
                    archive.addfile(
                        _tar_info(name, directory=False, size=len(data)), io.BytesIO(data)
                    )
                else:
                    archive.addfile(_tar_info(name, directory=True))
    return raw.getvalue()


def build_archive(pack: Path, output_dir: Path, version: str) -> ArchiveInfo:
    """Write ``protocolcanary-fixtures-<version>.tar.gz`` into ``output_dir``."""
    check_version(version)
    members, digest, protocol = collect_members(pack)
    data = render_archive(members)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / archive_name(version)
    path.write_bytes(data)
    return ArchiveInfo(
        path=path,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        entries=len(members) + len(directories_of(members)),
        uncompressed_bytes=sum(len(b) for b in members.values()),
        pack_digest=digest,
        protocol=protocol,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pack.py",
        description=(
            "Build the deterministic fixture-pack archive (CF-06). Reads the pack, "
            "never executes anything, and writes nothing unless the whole pack is valid."
        ),
    )
    parser.add_argument("pack", type=Path, help="pack directory, for example protocol-28")
    parser.add_argument(
        "--version",
        required=True,
        help="release version, MAJOR.MINOR.PATCH; names the archive",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        metavar="DIR",
        help="directory for the archive (default: dist)",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        info = build_archive(args.pack, args.output_dir, args.version)
    except PackError as error:
        print(f"error: {error}", file=sys.stderr)
        print("FAILED: no archive written", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: could not write the archive: {error}", file=sys.stderr)
        return 1
    print(f"OK: wrote {info.path}")
    print(f"sha256   {info.sha256}")
    print(f"bytes    {info.size}")
    print(f"entries  {info.entries}")
    print(f"digest   {info.pack_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Shared helpers for the archive tests (pack, manifest, verify_archive).

Stdlib only. Not a test module itself: ``unittest discover`` ignores it because
its name does not start with ``test``.
"""
from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str, relative: str):
    """Import ``relative`` (a path below the repository root) as ``name``."""
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pack = load_tool("canary_pack", "tools/pack/pack.py")

_LAZY = {
    "verify_archive": ("canary_verify_archive", "tools/pack/verify_archive.py"),
    "manifest": ("canary_release_manifest", "tools/release/manifest.py"),
}


def __getattr__(name: str):
    """Load the other tools on first use, so a test file only needs its own."""
    if name in _LAZY:
        module = load_tool(*_LAZY[name])
        globals()[name] = module
        return module
    raise AttributeError(name)

# Exact bytes of the golden pack (the same one the registry tests use).
A_TOML = (
    b'id = "p28-xdr-a"\nprotocol = 28\nsurface = "xdr"\ncategory = "cap-0083"\n'
    b'description = "golden xdr"\nsource_reference = "CAP-0083"\n'
    b'required_capabilities = ["wasm-artifact"]\ninput_file = "a.bin"\n'
    b'type = "StellarValue"\nkind = "decode-success"\nvalue_base64 = "AAAAAA=="\n'
)
B_TOML = (
    b'id = "p28-rpc-b"\nprotocol = 28\nsurface = "rpc"\ncategory = "network"\n'
    b'description = "golden rpc"\n'
    b'source_reference = "https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getNetwork"\n'
    b'method = "get-network"\n\n[[assert]]\n'
    b'kind = "field-exists"\nfield = "passphrase"\n'
)
PAYLOAD = b"\x00\x01payload\xff\n"


def expected_digest(files: dict[str, bytes]) -> str:
    """CF-02 section 5, built here independently of tools/registry/generate.py."""
    import hashlib

    text = "canary-pack-v1\n"
    for path in sorted(files, key=lambda p: p.encode("utf-8")):
        text += f"{hashlib.sha256(files[path]).hexdigest()} {len(files[path])} {path}\n"
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


GOLDEN_FILES = {"xdr/a.toml": A_TOML, "xdr/a.bin": PAYLOAD, "rpc/b.toml": B_TOML}
GOLDEN_DIGEST = expected_digest(GOLDEN_FILES)
REVISION = "0123456789abcdef0123456789abcdef01234567"


def write_pack(root: Path) -> Path:
    """Write the golden pack under ``root / "pack"`` and return that path."""
    pack_dir = root / "pack"
    (pack_dir / "xdr").mkdir(parents=True)
    (pack_dir / "rpc").mkdir()
    (pack_dir / "xdr" / "a.toml").write_bytes(A_TOML)
    (pack_dir / "xdr" / "a.bin").write_bytes(PAYLOAD)
    (pack_dir / "rpc" / "b.toml").write_bytes(B_TOML)
    return pack_dir


def tar_gz(entries, path: Path, *, compresslevel: int = 6) -> Path:
    """Write a ``.tar.gz`` from ``(TarInfo, bytes | None)`` pairs.

    Unlike ``pack.py`` this writes whatever it is given, so tests can build
    the hostile archives the safe extractor must refuse.
    """
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for info, data in entries:
                archive.addfile(info, io.BytesIO(data) if data is not None else None)
    path.write_bytes(raw.getvalue())
    return path


def file_info(name: str, size: int, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.mtime = 0
    return info


def entry(name: str, data: bytes, mode: int = 0o644):
    return file_info(name, len(data), mode), data


def link_entry(name: str, target: str, *, hard: bool):
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
    info.linkname = target
    info.mtime = 0
    return info, None


def valid_members(root: Path) -> list:
    """The golden pack as archive entries, with its generated registry.

    ``root`` is a scratch directory the pack is written into first.
    """
    members, _, _ = pack.collect_members(write_pack(root))
    return [entry(name, data) for name, data in sorted(members.items())]

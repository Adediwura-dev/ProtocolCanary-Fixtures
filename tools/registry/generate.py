#!/usr/bin/env python3
r"""Generate the fixture registry for one protocol pack (CF-02, section 3).

Scans a pack directory (for example ``protocol-28/``), hashes every fixture
and every payload a fixture references, and writes the ``registry-v1.json``
manifest described by ``schemas/registry-v1.schema.json``.

The rules implemented here are the ones in
``Protocol-Canary/docs/contracts/cf-02-registry-and-digest.md``, which is
authoritative; the Rust engine implements the same contract and both must
agree byte for byte:

* pack membership (section 2): every regular ``*.toml`` file at any depth
  except inside a directory named ``.git``, plus every regular file a fixture
  names in ``input_file`` or ``expected_file``. Anything else (README files,
  schemas, an existing registry) is outside the pack and never affects the
  output;
* pack-relative paths are UTF-8, ``/``-separated, with no leading ``/``, no
  ``.``/``..``/empty segment, no ``\``, NUL or control character, and no two
  paths equal after Unicode case folding;
* a symbolic link anywhere under the pack makes the pack invalid, as does a
  payload reference that leaves the pack;
* fixtures are sorted by ``id`` and payloads by ``path``, comparing UTF-8
  bytes (section 4);
* a file hash is SHA-256 over the raw bytes, with no newline normalization;
  the pack digest is SHA-256 over ``canary-pack-v1\n`` followed by one
  ``<sha256> <byte length> <path>\n`` line per distinct file, sorted by path
  (section 5);
* the JSON is UTF-8, LF, two-space indent, one trailing newline, with keys in
  the documented order (section 3).

An invalid pack is never repaired or skipped: the generator prints what is
wrong and exits non-zero without writing anything. Everything is read-only
until the single final write, and nothing is executed.

Usage:
    python3 tools/registry/generate.py PACK [--output PATH | --stdout]

The default output is ``PACK/registry-v1.json``. Requires Python 3.11+
(``tomllib``) and nothing outside the standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry-v1.schema.json"
DEFAULT_REGISTRY_NAME = "registry-v1.json"

REGISTRY_VERSION = 1
PACK_DIGEST_HEADER = "canary-pack-v1\n"
SURFACES = ("xdr", "rpc", "soroban")
PAYLOAD_FIELDS = ("input_file", "expected_file")

# Extraction limits approved under D-06 (CF-02 section 7, CF-06 section 6).
MAX_FILES = 2000
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_DEPTH = 8


class PackError(Exception):
    """The pack, or a fixture in it, breaks a CF-02 rule. Never repaired."""


def _load_schema_validator():
    """Import tools/validate/jsonschema_lite.py without needing a package."""
    path = REPO_ROOT / "tools" / "validate" / "jsonschema_lite.py"
    spec = importlib.util.spec_from_file_location("jsonschema_lite", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- paths


def check_pack_path(path: str) -> None:
    """Raise PackError unless ``path`` is a valid pack-relative path."""
    if not path:
        raise PackError("empty pack path")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        raise PackError(
            f"pack path {path!r} is not valid UTF-8; rename the file so its name is"
        ) from None
    if "\\" in path:
        raise PackError(f"pack path {path!r} contains a backslash; use '/' separators")
    if path.startswith("/"):
        raise PackError(f"pack path {path!r} is absolute; it must be relative to the pack")
    segments = path.split("/")
    for segment in segments:
        if segment == "":
            raise PackError(f"pack path {path!r} has an empty segment")
        if segment in (".", ".."):
            raise PackError(f"pack path {path!r} contains a {segment!r} segment")
    for char in path:
        if char == "\x00" or unicodedata.category(char) == "Cc":
            raise PackError(f"pack path {path!r} contains a control character")
    if len(segments) - 1 > MAX_DEPTH:
        raise PackError(f"pack path {path!r} is nested deeper than {MAX_DEPTH} directories")


def check_reference(value: str) -> None:
    """A payload reference must be a plain relative path (engine rule RH-04)."""
    if value == "" or value.startswith("/") or "\\" in value or "\x00" in value:
        raise PackError(f"{value!r} is not a plain relative path")
    if re.match(r"^[A-Za-z]:", value):
        raise PackError(f"{value!r} has a drive prefix")
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            raise PackError(f"{value!r} has an empty, '.' or '..' segment")


# ---------------------------------------------------------------- scanning


@dataclass(frozen=True)
class PackFile:
    path: str  # pack-relative, '/'-separated
    sha256: str
    size: int


def _is_link_like(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Windows, Python 3.12+
    return bool(is_junction and is_junction())


def scan_pack(root: Path) -> dict[str, bytes]:
    """Return ``{pack-relative path: raw bytes}`` for every regular file.

    ``.git`` is skipped. Any symbolic link, junction or special file under
    the root makes the pack invalid, so nothing outside the root is ever read.
    """
    if not root.is_dir():
        raise PackError(f"{root} is not a directory")
    files: dict[str, bytes] = {}
    folded: dict[str, str] = {}
    total = 0
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        with os.scandir(directory) as iterator:
            entries = list(iterator)
        for entry in entries:
            if entry.name == ".git":
                continue
            rel = f"{prefix}{entry.name}"
            check_pack_path(rel)
            if _is_link_like(entry):
                raise PackError(
                    f"{rel} is a symbolic link; a pack must contain only regular "
                    "files and directories"
                )
            key = rel.casefold()
            if key in folded and folded[key] != rel:
                raise PackError(
                    f"{folded[key]!r} and {rel!r} differ only by case; a pack must "
                    "not depend on whether the file system is case sensitive"
                )
            folded[key] = rel
            if entry.is_dir(follow_symlinks=False):
                stack.append((Path(entry.path), rel + "/"))
            elif entry.is_file(follow_symlinks=False):
                if len(files) >= MAX_FILES:
                    raise PackError(f"the pack has more than {MAX_FILES} files")
                data = Path(entry.path).read_bytes()
                if len(data) > MAX_FILE_BYTES:
                    raise PackError(
                        f"{rel} is {len(data)} bytes, over the {MAX_FILE_BYTES} byte "
                        "limit for one pack file"
                    )
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise PackError(
                        f"the pack is over {MAX_TOTAL_BYTES} bytes in total"
                    )
                files[rel] = data
            else:
                raise PackError(f"{rel} is not a regular file")
    return files


# ---------------------------------------------------------------- fixtures


def _required_str(data: dict, key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PackError(
            f"{path}: field {key!r} is required and must be a non-empty string "
            "(run tools/validate/validate.py for the full diagnostics)"
        )
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utf8_key(text: str) -> bytes:
    return text.encode("utf-8")


def build_registry(files: dict[str, bytes]) -> dict:
    """Build the registry mapping from the pack's files."""
    fixture_paths = sorted(p for p in files if p.endswith(".toml"))
    if not fixture_paths:
        raise PackError("the pack contains no *.toml fixture files")

    entries: list[dict] = []
    protocols: dict[int, str] = {}
    seen_ids: dict[str, str] = {}
    pack_files: dict[str, PackFile] = {}

    def register(path: str, data: bytes) -> None:
        pack_files.setdefault(path, PackFile(path, _sha256(data), len(data)))

    for path in fixture_paths:
        data = files[path]
        try:
            fixture = tomllib.loads(data.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise PackError(f"{path}: not valid UTF-8: {error}") from None
        except tomllib.TOMLDecodeError as error:
            raise PackError(f"{path}: invalid TOML: {error}") from None
        register(path, data)

        fixture_id = _required_str(fixture, "id", path)
        surface = _required_str(fixture, "surface", path)
        if surface not in SURFACES:
            raise PackError(
                f"{path}: field 'surface' must be one of {list(SURFACES)}, got {surface!r}"
            )
        category = _required_str(fixture, "category", path)
        description = _required_str(fixture, "description", path)
        protocol = fixture.get("protocol")
        if not isinstance(protocol, int) or isinstance(protocol, bool) or protocol < 0:
            raise PackError(f"{path}: field 'protocol' must be a non-negative integer")

        if fixture_id in seen_ids:
            raise PackError(
                f"duplicate fixture id {fixture_id!r} in {seen_ids[fixture_id]} and {path}"
            )
        seen_ids[fixture_id] = path
        protocols.setdefault(protocol, path)

        payloads: dict[str, str] = {}
        directory = path.rpartition("/")[0]
        for field in PAYLOAD_FIELDS:
            if field not in fixture:
                continue
            reference = fixture[field]
            if not isinstance(reference, str):
                raise PackError(f"{path}: field {field!r} must be a string")
            try:
                check_reference(reference)
                payload_path = f"{directory}/{reference}" if directory else reference
                check_pack_path(payload_path)
            except PackError as error:
                raise PackError(f"{path}: {field} {error}") from None
            if payload_path not in files:
                raise PackError(
                    f"{path}: {field} {reference!r} does not exist in the pack "
                    f"(looked for {payload_path})"
                )
            register(payload_path, files[payload_path])
            payloads[payload_path] = pack_files[payload_path].sha256

        entry: dict = {
            "id": fixture_id,
            "path": path,
            "sha256": pack_files[path].sha256,
            "surface": surface,
            "category": category,
            "description": description,
        }
        if "source_reference" in fixture:
            entry["sourceReference"] = _required_str(fixture, "source_reference", path)
        if "required_capabilities" in fixture:
            capabilities = fixture["required_capabilities"]
            if not isinstance(capabilities, list) or not all(
                isinstance(c, str) and c for c in capabilities
            ):
                raise PackError(
                    f"{path}: field 'required_capabilities' must be an array of "
                    "non-empty strings"
                )
            entry["requiredCapabilities"] = list(capabilities)
        entry["payloads"] = [
            {"path": p, "sha256": payloads[p]} for p in sorted(payloads, key=_utf8_key)
        ]
        entries.append(entry)

    if len(protocols) != 1:
        listing = ", ".join(f"{p} (e.g. {where})" for p, where in sorted(protocols.items()))
        raise PackError(
            f"a pack targets exactly one protocol, but its fixtures declare: {listing}"
        )
    (protocol,) = protocols

    entries.sort(key=lambda e: _utf8_key(e["id"]))
    return {
        "registryVersion": REGISTRY_VERSION,
        "protocol": protocol,
        "fixtures": entries,
        "packDigest": pack_digest(pack_files.values()),
    }


def pack_digest(pack_files) -> str:
    """``sha256:`` over the canonical digest input (CF-02 section 5)."""
    lines = [PACK_DIGEST_HEADER]
    for pack_file in sorted(pack_files, key=lambda f: _utf8_key(f.path)):
        lines.append(f"{pack_file.sha256} {pack_file.size} {pack_file.path}\n")
    return "sha256:" + _sha256("".join(lines).encode("utf-8"))


# ---------------------------------------------------------------- output


def render_registry(registry: dict) -> str:
    """The canonical encoding: UTF-8, LF, two-space indent, trailing newline."""
    return json.dumps(registry, indent=2, ensure_ascii=False) + "\n"


def check_against_schema(registry: dict, schema_path: Path = SCHEMA_PATH) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    problems = _load_schema_validator().validate(registry, schema)
    # The schema cannot express ordering (it says so itself); check it here.
    ids = [e["id"] for e in registry["fixtures"]]
    if ids != sorted(ids, key=_utf8_key):
        problems.append("fixtures are not sorted by id")
    if problems:
        raise PackError(
            f"the generated registry does not conform to {schema_path.name}:\n  - "
            + "\n  - ".join(problems)
        )


def generate(pack: Path) -> str:
    """Return the rendered registry for ``pack``, or raise PackError."""
    registry = build_registry(scan_pack(pack))
    check_against_schema(registry)
    return render_registry(registry)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description=(
            "Generate the fixture registry (registry-v1.json) for one protocol pack, "
            "per CF-02. Reads the pack, never executes anything, and writes nothing "
            "unless the whole pack is valid."
        ),
    )
    parser.add_argument("pack", type=Path, help="pack directory, for example protocol-28")
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help=f"write the registry here (default: PACK/{DEFAULT_REGISTRY_NAME})",
    )
    where.add_argument(
        "--stdout", action="store_true", help="print the registry instead of writing a file"
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = generate(args.pack)
    except PackError as error:
        print(f"error: {error}", file=sys.stderr)
        print("FAILED: no registry written", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: could not read the pack: {error}", file=sys.stderr)
        print("FAILED: no registry written", file=sys.stderr)
        return 1

    if args.stdout:
        # Bytes, so Windows' text-mode newline translation cannot turn the
        # registry's LF line endings into CRLF.
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode("utf-8"))
            buffer.flush()
        else:
            sys.stdout.write(text)
        return 0
    output = args.output or (args.pack / DEFAULT_REGISTRY_NAME)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(text.encode("utf-8"))
    except OSError as error:
        print(f"error: could not write {output}: {error}", file=sys.stderr)
        return 1
    print(f"OK: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

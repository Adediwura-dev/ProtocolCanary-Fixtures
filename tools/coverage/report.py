#!/usr/bin/env python3
"""Fixture coverage report: what the validator accepts vs. what fixtures use.

``tools/validate/validate.py`` decides which capabilities a fixture *may*
use (surfaces, XDR types and kinds, RPC methods and assertion kinds,
Soroban expectation kinds, required capabilities). Nothing in the
repository says which of those capabilities a pack actually exercises, so
a gap -- a method no fixture calls, an assertion kind no fixture asserts
-- stays invisible until someone reads every file.

This tool prints that missing half as a deterministic matrix: one section
per capability constant the validator owns, one line per accepted value,
and an explicit ``0`` marked ``no fixture`` where nothing uses it. The
counts are structural only: every fixture present is counted, every
capability the validator accepts is listed, and nothing is inferred,
estimated or assumed. A fixture the validator itself rejects is reported
separately under ``invalid`` rather than silently folded into the counts
or crashing the run.

Everything is read from the validator: the capability constants come from
its module-level sets (so a constant added there shows up here without a
change to this file) and fixtures are discovered and loaded through its
own ``find_fixture_files``/``load_fixture``/``validate_directory``. This
tool never writes to a pack or any file.

Usage:
    python3 tools/coverage/report.py [--format {text,json}] [pack ...]

With no arguments, reports over every ``protocol-*/`` directory next to
this script's repository root (the validator's own default). Exits 0 for
a completed report -- including a report containing invalid fixtures,
which the validator is the tool to fail on -- and 1 for a pack path that
does not exist or is not a directory.

``--format text`` (the default) prints the table; ``--format json``
prints exactly one JSON document on stdout. Both are deterministic:
sorted keys, no timestamps, no absolute paths, so two runs over the same
tree are byte-identical and a diff means the tree changed.

``--validator PATH`` loads a different copy of the validator (used by the
test suite to check that a new capability constant appears here
automatically).

Requires Python 3.11+ (the validator it reuses uses the stdlib
``tomllib``); no third-party dependencies.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]

# The validator owns the capability vocabulary; load it exactly the way
# tools/badge/badge.py does, so this tool counts by the same rules the
# validator validates with instead of duplicating them here.
_VALIDATE_PATH = Path(__file__).resolve().parents[1] / "validate" / "validate.py"
_spec = importlib.util.spec_from_file_location("validate", _VALIDATE_PATH)
assert _spec is not None and _spec.loader is not None
validate = importlib.util.module_from_spec(_spec)
sys.modules["validate"] = validate
_spec.loader.exec_module(validate)


class CoverageError(RuntimeError):
    """Raised for a pack path this tool cannot report on (e.g. missing)."""


# A fixture's ``category`` is a CAP category when it looks like cap-<4-digit
# number> (the same shape tests/test_pack_readme_tables.py matches for pack
# directories). The validator has no constant for this -- it only rejects
# vague categories -- so the report derives the CAP section from the
# fixtures' own category values.
_CAP_CATEGORY_RE = re.compile(r"^cap-\d{4}$")

# Capability constants are module-level sets of strings in the validator.
# These module-level names are also sets of strings but name no capability a
# fixture exercises, so they are excluded from the report by this documented
# rule rather than by a per-name allowlist: VAGUE_CATEGORIES is the
# *disallowed* category vocabulary (the opposite of a capability).
IGNORED_CONSTANTS = {"VAGUE_CATEGORIES"}


def capability_constants(validator: object) -> dict[str, tuple[str, ...]]:
    """Every capability constant ``validator`` declares, name -> sorted values.

    A capability constant is a module-level ``ALL_CAPS`` set/frozenset of
    strings: exactly the shape ``SURFACES``, ``XDR_TYPES``, ``XDR_KINDS``,
    ``RPC_METHODS``, ``RPC_ASSERT_KINDS``, ``RPC_ASSERT_TYPES``,
    ``SOROBAN_EXPECT_KINDS`` and ``CAPABILITIES`` have. Tuples (the
    required-field lists), non-string sets and the ignored names above are
    not capability vocabularies and are skipped. Because discovery is by
    shape, a new set added to the validator is reported here automatically.
    """
    found: dict[str, tuple[str, ...]] = {}
    for name in sorted(dir(validator)):
        if not name.isupper() or name in IGNORED_CONSTANTS or name.startswith("_"):
            continue
        value = getattr(validator, name)
        if isinstance(value, (set, frozenset)) and all(isinstance(v, str) for v in value):
            found[name] = tuple(sorted(value))
    return found


def _single_field(key: str) -> Callable[[dict], list[str]]:
    """Counting rule: the fixture's own top-level ``key`` value, when a string."""

    def extract(data: dict) -> list[str]:
        value = data.get(key)
        return [value] if isinstance(value, str) else []

    return extract


def _table_list_field(container_key: str, key: str) -> Callable[[dict], list[str]]:
    """Counting rule: ``key`` of each table in the fixture's ``container_key`` list."""

    def extract(data: dict) -> list[str]:
        container = data.get(container_key)
        if not isinstance(container, list):
            return []
        values: list[str] = []
        for entry in container:
            if isinstance(entry, dict) and isinstance(entry.get(key), str):
                values.append(entry[key])
        return values

    return extract


def _nested_field(container_key: str, key: str) -> Callable[[dict], list[str]]:
    """Counting rule: ``container_key.key`` when ``container_key`` is a table."""

    def extract(data: dict) -> list[str]:
        container = data.get(container_key)
        if not isinstance(container, dict):
            return []
        value = container.get(key)
        return [value] if isinstance(value, str) else []

    return extract


def _string_list_field(key: str) -> Callable[[dict], list[str]]:
    """Counting rule: each string entry of the fixture's top-level list ``key``."""

    def extract(data: dict) -> list[str]:
        values = data.get(key)
        if not isinstance(values, list):
            return []
        return [v for v in values if isinstance(v, str)]

    return extract


def _soroban_argument_kinds(data: dict) -> list[str]:
    """Every ``[[args]].kind`` in a fixture, including nested vec/map entries.

    A nested argument is still a fixture's use of a kind, so the recursive
    walk counts it; only the shape canary-soroban parses is followed.
    """
    values: list[str] = []

    def walk(entry: object) -> None:
        if not isinstance(entry, dict):
            return
        kind = entry.get("kind")
        if isinstance(kind, str):
            values.append(kind)
        value = entry.get("value")
        if kind == "vec" and isinstance(value, list):
            for item in value:
                walk(item)
        elif kind == "map" and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    walk(item.get("key"))
                    walk(item.get("value"))

    args = data.get("args")
    if isinstance(args, list):
        for entry in args:
            walk(entry)
    return values


# How each known capability constant is counted: which field of a fixture
# uses a value of it. A constant with no rule here is still reported (with
# structural zeros) and named in ``unmapped_capabilities`` -- never guessed
# at, because a fabricated count would be exactly the inference this tool
# must not make.
CAPABILITY_EXTRACTORS: dict[str, Callable[[dict], list[str]]] = {
    "SURFACES": _single_field("surface"),
    "XDR_TYPES": _single_field("type"),
    "XDR_KINDS": _single_field("kind"),
    "RPC_METHODS": _single_field("method"),
    "RPC_ASSERT_KINDS": _table_list_field("assert", "kind"),
    "RPC_ASSERT_TYPES": _table_list_field("assert", "expected_type"),
    "SOROBAN_EXPECT_KINDS": _nested_field("expect", "kind"),
    "SOROBAN_ARG_KINDS": _soroban_argument_kinds,
    "CAPABILITIES": _string_list_field("required_capabilities"),
}


def _pack_label(pack_dir: Path) -> str:
    """The pack's name in the report: never an absolute path.

    A relative path is kept as given (it is already repository-relative in
    normal use); an absolute path -- a temporary directory in a test, or a
    checkout elsewhere on disk -- is reduced to its final component so the
    output stays machine- and run-independent.
    """
    if pack_dir.is_absolute():
        return pack_dir.name
    return pack_dir.as_posix()


def build_report(pack_dirs: list[Path], *, validator: object | None = None) -> dict:
    """Build the coverage document for ``pack_dirs``.

    Pure with respect to the filesystem: every pack is read (discovery,
    load and validation all go through ``validator``) and nothing is
    written. Raises :class:`CoverageError` for a pack that does not exist
    or is not a directory, before touching any other pack.

    The returned dict has only sorted keys and JSON-safe values:

    ``totals``            files / valid / invalid across every pack.
    ``packs``             the same counts per pack (``name`` is a label,
                          never an absolute path).
    ``capabilities``      every capability constant the validator declares
                          -> accepted value -> fixture count, zeros
                          included.
    ``caps``              fixtures per ``cap-<4-digit number>`` category.
    ``invalid``           count and pack-relative paths of the fixtures
                          the validator rejects (counted, not counted *in*).
    ``unmapped_capabilities``  capability constants with no counting rule;
                          their zeros are structural, not measured.
    """
    v = validate if validator is None else validator

    packs: list[dict] = []
    invalid_files: list[str] = []
    # Accumulated counts across all packs, keyed exactly like the constants.
    counts: dict[str, dict[str, int]] = {
        name: {value: 0 for value in values}
        for name, values in capability_constants(v).items()
    }
    cap_counts: dict[str, int] = {}
    total_files = total_valid = total_invalid = 0

    for pack_dir in pack_dirs:
        pack_dir = Path(pack_dir)
        if not pack_dir.is_dir():
            raise CoverageError(f"{pack_dir}: not a directory")
        label = _pack_label(pack_dir)

        # The validator's own validation run decides which fixtures count:
        # a path with any error is invalid, no matter which check fired.
        validation = v.validate_directory(pack_dir)
        rejected = {entry.path for entry in validation.error_entries}

        pack_files = pack_valid = pack_invalid = 0
        for path in v.find_fixture_files(pack_dir):
            pack_files += 1
            total_files += 1
            fixture = v.load_fixture(path, v.Report())
            if fixture is None or path in rejected:
                pack_invalid += 1
                total_invalid += 1
                invalid_files.append(f"{label}/{path.relative_to(pack_dir).as_posix()}")
                continue
            pack_valid += 1
            total_valid += 1

            data = fixture.data
            for name, extract in CAPABILITY_EXTRACTORS.items():
                section = counts.get(name)
                if section is None:
                    continue  # the validator copy no longer declares it
                for value in extract(data):
                    if value in section:
                        section[value] += 1
            category = data.get("category")
            if isinstance(category, str) and _CAP_CATEGORY_RE.match(category):
                key = category.lower()
                cap_counts[key] = cap_counts.get(key, 0) + 1

        packs.append(
            {"name": label, "files": pack_files, "valid": pack_valid, "invalid": pack_invalid}
        )

    declared = capability_constants(v)
    # Built with sorted keys throughout: the document's key order is part
    # of the output format, so two runs over the same tree are identical.
    return {
        "capabilities": {name: dict(sorted(counts[name].items())) for name in sorted(counts)},
        "caps": dict(sorted(cap_counts.items())),
        "invalid": {"count": len(invalid_files), "files": sorted(invalid_files)},
        "packs": sorted(packs, key=lambda pack: pack["name"]),
        "totals": {"files": total_files, "valid": total_valid, "invalid": total_invalid},
        "unmapped_capabilities": sorted(
            name for name in declared if name not in CAPABILITY_EXTRACTORS
        ),
    }


_NO_FIXTURE = "no fixture"


def _render_counts(lines: list[str], title: str, counts: dict[str, int]) -> None:
    """Append one section: a title and aligned ``value  count`` lines."""
    lines.append(title)
    if not counts:
        lines.append(f"  {_NO_FIXTURE}")
        return
    width = max(len(key) for key in counts)
    for key, count in counts.items():
        line = f"  {key:<{width}}  {count:>3}"
        if count == 0:
            line += f"  {_NO_FIXTURE}"
        lines.append(line)


def render_text(report: dict) -> str:
    """The human-readable table (deterministic: no timestamps, no paths)."""
    totals = report["totals"]
    lines = [
        "Fixture coverage report: "
        f"{totals['files']} fixture file(s), {totals['valid']} valid, "
        f"{totals['invalid']} invalid"
    ]

    lines.append("")
    lines.append("Packs")
    if report["packs"]:
        width = max(len(pack["name"]) for pack in report["packs"])
        for pack in report["packs"]:
            lines.append(
                f"  {pack['name']:<{width}}  {pack['files']:>3} file(s), "
                f"{pack['valid']:>3} valid, {pack['invalid']:>3} invalid"
            )
    else:
        lines.append(f"  {_NO_FIXTURE}")

    for name, counts in report["capabilities"].items():
        lines.append("")
        _render_counts(lines, name, counts)

    lines.append("")
    _render_counts(lines, "CAP categories", report["caps"])

    lines.append("")
    lines.append("Invalid fixtures")
    invalid = report["invalid"]
    if invalid["count"]:
        for path in invalid["files"]:
            lines.append(f"  {path}")
    else:
        lines.append("  none")

    if report["unmapped_capabilities"]:
        lines.append("")
        lines.append(
            "Unmapped capability constants (structural zeros, not measured): "
            + ", ".join(report["unmapped_capabilities"])
        )

    return "\n".join(lines) + "\n"


def render_json(report: dict) -> str:
    """The ``--format json`` document: one JSON document, keys sorted."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report.py",
        description=(
            "Fixture coverage report: counts, per capability constant, of the "
            "fixtures present under each pack. Read-only; counts what exists, "
            "never infers coverage."
        ),
    )
    parser.add_argument(
        "packs",
        nargs="*",
        metavar="pack",
        help="directories to report on (default: every protocol-*/ directory next to this script)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="'text' (default) is the human-readable table; 'json' prints one JSON document on stdout",
    )
    parser.add_argument(
        "--validator",
        type=Path,
        default=None,
        metavar="PATH",
        help="load this copy of tools/validate/validate.py instead of the repository's one",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    validator = None
    if args.validator is not None:
        if not args.validator.is_file():
            print(f"error: {args.validator}: no such validator file", file=sys.stderr)
            return 1
        spec = importlib.util.spec_from_file_location("validate_scratch", args.validator)
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        # Register before exec: validate.py defines dataclasses, and
        # dataclasses resolves annotations through ``sys.modules``.
        sys.modules["validate_scratch"] = validator
        spec.loader.exec_module(validator)

    if args.packs:
        pack_dirs = [Path(a) for a in args.packs]
    else:
        pack_dirs = sorted(p for p in REPO_ROOT.glob("protocol-*") if p.is_dir())
        if not pack_dirs:
            pack_dirs = [REPO_ROOT]

    try:
        report = build_report(pack_dirs, validator=validator)
    except CoverageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        sys.stdout.write(render_json(report))
    else:
        sys.stdout.write(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

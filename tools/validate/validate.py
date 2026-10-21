#!/usr/bin/env python3
"""Structural validator for ProtocolCanary-Fixtures.

Validates every ``*.toml`` fixture file under one or more root directories
against the fixture format actually implemented by
``StellarCanary/Protocol-Canary``'s ``canary-fixtures``/``canary-xdr``/
``canary-rpc``/``canary-soroban`` crates (see that repository's
``docs/fixture-contract.md``, which is authoritative; this script mirrors
it, not the other way around).

This tool performs structural validation only. It never executes a
fixture's assertion (no decoding, no network calls, no simulation) and
never treats a fixture field as a command to run.

Usage:
    python3 tools/validate/validate.py [--quiet] [--format {text,json}] [root ...]

With no arguments, validates every protocol-*/ directory found next to
this script's repository root. Exits 0 if every fixture is valid, 1
otherwise.

``--format text`` (the default) prints the human-readable report:
warnings to stdout, errors to stderr, and the final OK/FAILED summary. Pass
``--quiet`` to suppress the warnings.

``--format json`` (F-023) prints one structured JSON document on stdout and
nothing on stderr, so a CI job can ingest it with a single ``json.load`` and
still read the outcome from the exit code. The document has the shape
``{"valid": bool, "errors": [...], "warnings": [...], "summary": {...}}``,
where each diagnostic is ``{"path", "id", "line", "message"}``. ``line`` is
the fixture's 1-based line number when the validator knows it (a TOML syntax
error, or a located top-level field) and ``null`` otherwise -- it is never
guessed, because a wrong line number in a CI log is worse than an absent
one. ``--quiet`` only affects the text format; the JSON document always
carries every warning, since a machine consumer decides what to do with
them. Encoding is deterministic: UTF-8, two-space indent, one trailing
newline, keys in the documented order.

A missing ``source_reference`` is an error, not a warning: every fixture
must cite an authoritative upstream source (see CONTRIBUTING.md).

Two safety rules run before structural validation (F-048/F-049): fixture
text (and evidence JSON read through ``--evidence``) must not contain
bidirectional, invisible or disallowed control characters, and every
pack-relative path segment must be a name Windows can create. Both report
and never rewrite; see CONTRIBUTING.md, "Portable paths and safe text".
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import math
import re
import sys
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

# Mirrors canary-fixtures' supported surfaces; must be kept in sync with
# docs/fixture-contract.md in the upstream canary-fixtures repo. Changing
# this without a corresponding upstream change would make the validator accept
# fixtures the loader would reject (or vice versa).
SURFACES = {"xdr", "rpc", "soroban"}
# Set of XDR type names supported by canary-xdr. Must be kept in sync with the
# types canary-xdr actually supports -- an unsupported type is a Protocol-Canary
# limitation, not something to work around here (see CONTRIBUTING.md, "If you
# need an XDR type this repository does not yet support"): open an issue/PR
# against canary-xdr first, and only add the type here once that support is
# released. ScVal is supported per Protocol-Canary's docs/fixture-contract.md
# (F-016, issue #281); TransactionEnvelope and LedgerEntry were added to the
# engine's XdrTypeName in canary-xdr's decoder.rs and are accepted here by
# F-050 (issue #324).
XDR_TYPES = {
    "StellarValue",
    "ContractExecutable",
    "ScVal",
    "TransactionEnvelope",
    "LedgerEntry",
}
# Set of assertion kinds supported by canary-xdr for a given XDR value.
# Must be kept in sync with canary-xdr's supported assertion kinds.
XDR_KINDS = {"decode-success", "decode-failure", "roundtrip", "encode-equals"}
# Set of RPC methods supported by canary-rpc. Must be kept in sync with the
# methods canary-rpc actually implements: a value added here only names a string;
# the corresponding method must already exist upstream in canary-rpc.
RPC_METHODS = {"get-network", "get-latest-ledger"}
RPC_ASSERT_KINDS = {
    "field-exists",
    "field-absent",
    "field-type",
    "field-equals",
    "field-number-range",
}
# JSON type names accepted for RPC field-type assertions. Keep this set in
# sync with the `expected_type` enum in schemas/fixture-v1.schema.json.
RPC_ASSERT_TYPES = {"string", "number", "bool", "object", "array", "null"}
SOROBAN_EXPECT_KINDS = {"simulation-success", "simulation-error"}
# Keys canary-soroban accepts inside a soroban fixture's [expect] table. It
# rejects anything else outright (see the `unknown_body_keys(expect, ...)` call
# in canary-soroban/src/runner.rs), so this mirrors the loader rather than
# tightening it.
SOROBAN_EXPECT_FIELDS = ("kind", "message_contains")
# Kinds canary-soroban accepts in one [[args]] entry. This is the set of
# `match kind` arms in `parse_arg_at` in
# crates/canary-soroban/src/runner.rs (read at Protocol-Canary commit
# 9b3fefcacde3de03b3cb34bfea4d47124ebc4d82, F-051). Keep it in sync with the
# `args.items.properties.kind` enum in schemas/fixture-v1.schema.json.
SOROBAN_ARG_KINDS = {
    "bool",
    "u32",
    "i32",
    "u64",
    "i64",
    "i128",
    "u128",
    "i256",
    "u256",
    "void",
    "duration",
    "timepoint",
    "symbol",
    "string",
    "bytes",
    "address",
    "vec",
    "map",
}
# Keys an [[args]] table may carry. canary-soroban rejects any other key
# (see parse_arg_at's unknown_body_keys call), so this mirrors the loader.
SOROBAN_ARG_FIELDS = ("kind", "value")
# Keys a map entry (one element of a `map` argument's value array) may carry.
SOROBAN_MAP_ENTRY_FIELDS = ("key", "value")
# Maximum number of nested vec/map containers in one argument. Mirrors
# MAX_ARG_DEPTH in crates/canary-soroban/src/builder.rs (F-051); the engine
# checks it in parse_arg_at's check_depth and again when converting to XDR.
MAX_ARG_DEPTH = 8
# ScSymbol is limited to 32 bytes of [A-Za-z0-9_] by Stellar-contract.x.
_SYMBOL_RE = re.compile(r"[A-Za-z0-9_]{0,32}")
# A G... account or C... contract strkey is 56 base32 characters. Checksums
# are deliberately not verified here: the validator has no strkey helper and
# canary-soroban is the authority on checksum validity.
_STRKEY_RE = re.compile(r"[GC][A-Z2-7]{55}")
_HEX_RE = re.compile(r"[0-9a-fA-F]*")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]*")
# Set of kebab-case capability strings a fixture may list in
# `required_capabilities`. Must be kept in sync with
# canary_core::Capability in StellarCanary/Protocol-Canary, whose
# kebab-case names these mirror, AND with the required_capabilities
# enum in schemas/fixture-v1.schema.json (the same cross-reference is
# documented there). The two lists are maintained independently (a
# Python set and a JSON Schema enum), so adding a value to one without
# the other silently creates a validator/schema mismatch. The canonical
# vocabulary is published in tests/fixtures/capabilities.json (F-032),
# and tests/test_validate.py pins this set, the schema's enum and that
# file to each other, so a divergence in any of the three fails the suite.
CAPABILITIES = {
    "soroban-contract",
    "rpc-client",
    "stellar-sdk-dependency",
    "wasm-artifact",
    "raw-ledger-access",
}
VAGUE_CATEGORIES = {"misc", "other", "test", "general"}

# F-049: the Windows-portable-name rule lives in one helper shared with the
# registry generator and the archive verifier, so the three tools cannot
# drift. Loaded by path because these tools are run as scripts and are not a
# Python package.
def _load_portable_names():
    name = "canary_portable_names"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve().parent / "portable_names.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


portable_names = _load_portable_names()

# F-048: characters that change how fixture text looks in review or hide
# entirely. Cf covers zero-width and other format characters (including a BOM
# and the bidirectional controls below); Cc covers C0 and C1 controls. Tab,
# line feed and carriage return are the only allowed controls, and a single
# BOM is allowed only as the first character of a file.
ALLOWED_CONTROL_CHARACTERS = ("\t", "\n", "\r")
BIDI_CONTROL_CHARACTERS = tuple(
    chr(codepoint)
    for codepoint in tuple(range(0x202A, 0x202F)) + tuple(range(0x2066, 0x206A))
)
BYTE_ORDER_MARK = "\ufeff"

# What `source_reference` may be (F-025). Two forms are accepted:
#
# * a CAP identifier such as ``CAP-0083``, the form CONTRIBUTING.md documents;
# * an absolute ``https://`` URL with a host and a path, no credentials, and
#   no whitespace.
#
# This pattern is the *syntactic* rule and is mirrored, character for
# character, as ``source_reference.pattern`` in schemas/fixture-v1.schema.json
# (tools/validate/schema_sync.py fails when the two differ). It is written
# without lookarounds so it means the same thing as a Python regex and as an
# ECMA-262 regex. The rule a regex cannot express, that a GitHub link must be
# pinned to a commit or a tag, is `check_source_reference` below and is
# enforced only here, like the other loader-side invariants.
SOURCE_REFERENCE_PATTERN = r"CAP-[0-9]{4}|https://[^\s/?#@]+/[^\s]+"
_SOURCE_REFERENCE_RE = re.compile(SOURCE_REFERENCE_PATTERN)
MAX_SOURCE_REFERENCE_LENGTH = 2048

# A GitHub ref is "pinned" when it names a commit or a release, not a branch:
# a full 40 or 64 digit object name, or a version-like tag (``v28.0.1``,
# ``28.0``, ``protocol-28``, ``core-v21.0.0``). A branch such as ``master``
# moves, so a link that follows it can silently stop describing the behavior
# the fixture was written against.
_COMMIT_REF_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_VERSION_SUFFIX = r"[0-9]+(?:\.[0-9]+)*(?:[-+._][0-9A-Za-z.+_-]*)?"
_TAG_REF_RE = re.compile(rf"v?{_VERSION_SUFFIX}|[A-Za-z][A-Za-z0-9._]*-v?{_VERSION_SUFFIX}")
# URL path kinds on github.com whose next segment is a git ref.
_GITHUB_REF_KINDS = {"blob", "tree", "raw", "blame", "edit", "commits"}
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_GITHUB_RAW_HOST = "raw.githubusercontent.com"

# Required fields, mirrored by schemas/fixture-v1.schema.json and kept in
# sync by tools/validate/schema_sync.py (see CONTRIBUTING.md#fixture-schema).
# The schema's top-level ``required`` array must list exactly
# ``COMMON_REQUIRED_FIELDS``, and each surface's per-surface ``required``
# array must list exactly that surface's tuple below. These live here as
# constants rather than inline in the checks so the sync tool has a single
# machine-readable source to compare the schema against.
COMMON_REQUIRED_FIELDS = (
    "id",
    "protocol",
    "surface",
    "category",
    "description",
    "source_reference",
)
XDR_REQUIRED_FIELDS = ("type", "kind", "value_base64")
RPC_REQUIRED_FIELDS = ("method", "assert")
SOROBAN_REQUIRED_FIELDS = (
    "source_account",
    "contract_id",
    "function",
    "sequence_number",
    "expect",
)
# Fields required only for one XDR ``kind`` value; mirrors the nested if/then
# in the schema's xdr block.
XDR_CONDITIONAL_REQUIRED_FIELDS = {"encode-equals": ("expected_base64",)}

# Keys accepted at the top level of a fixture beyond the required common
# fields (F-031), and the extra keys each surface's body contributes. A
# fixture's top level accepts COMMON_REQUIRED_FIELDS + these optional keys
# + exactly its own surface's keys; anything else -- a typo such as
# ``descriptoin``, or a field that belongs to another surface -- is an
# error rather than silently ignored, mirroring ``additionalProperties:
# false`` in schemas/fixture-v1.schema.json. The tuples must stay in sync
# with that schema's per-surface ``properties`` blocks.
COMMON_OPTIONAL_FIELDS = ("required_capabilities", "input_file", "expected_file")
SURFACE_FIELDS = {
    "xdr": ("type", "kind", "value_base64", "expected_base64"),
    "rpc": ("method", "assert"),
    "soroban": (
        "source_account",
        "contract_id",
        "function",
        "sequence_number",
        "args",
        "expect",
    ),
}
# Keys a single [[assert]] table may carry (the schema's assert.items).
RPC_ASSERT_FIELDS = ("kind", "field", "value", "expected_type")


@dataclass
class Fixture:
    path: Path
    data: dict


@dataclass
class Diagnostic:
    """One error or warning, with the location a machine consumer needs.

    ``line`` is the 1-based line number the validator can attribute to the
    message, or ``None`` when it cannot. It is never guessed: a reported line
    that does not hold the offending text is worse for a CI reader than an
    honest ``null``. See ``_field_line`` and ``_toml_error_line`` for the two
    places a line is genuinely known.
    """
    path: Path
    message: str
    fixture_id: str | None = None
    line: int | None = None

    def format(self) -> str:
        """The text-format line for this diagnostic: ``path [id]: message``."""
        if self.fixture_id:
            return f"{self.path} [{self.fixture_id}]: {self.message}"
        return f"{self.path}: {self.message}"

    def as_json(self) -> dict:
        """The JSON object for this diagnostic (key order is part of the format)."""
        return {
            "path": str(self.path),
            "id": self.fixture_id,
            "line": self.line,
            "message": self.message,
        }


@dataclass
class Report:
    # Diagnostics are stored structured and rendered to text on demand, so the
    # human-readable report (`errors`/`warnings`) and the JSON report
    # (`build_json_report`) are two views of exactly the same records and can
    # never disagree about a message's path, id or line.
    error_entries: list[Diagnostic] = field(default_factory=list)
    warning_entries: list[Diagnostic] = field(default_factory=list)
    # Fixture ids keyed by the fixture file's path, for fixtures whose ``id``
    # has already been parsed and validated (see ``known_fixture_id``). Lets
    # error/warning messages name the fixture without each call site having to
    # restate its id explicitly.
    fixture_ids: dict[Path, str] = field(default_factory=dict)

    def register_fixture_id(self, path: Path, fixture_id: str | None) -> None:
        """Record ``fixture_id`` as ``path``'s id when it is known to be valid.

        A falsy/``None`` id leaves ``path`` unregistered so its messages fall
        back to the path-only format.
        """
        if fixture_id:
            self.fixture_ids[path] = fixture_id

    def _diagnostic(
        self,
        path: Path,
        message: str,
        fixture_id: str | None,
        line: int | None,
    ) -> Diagnostic:
        fid = fixture_id if fixture_id is not None else self.fixture_ids.get(path)
        return Diagnostic(path=path, message=message, fixture_id=fid or None, line=line)

    def error(
        self,
        path: Path,
        message: str,
        fixture_id: str | None = None,
        line: int | None = None,
    ) -> None:
        self.error_entries.append(self._diagnostic(path, message, fixture_id, line))

    def warning(
        self,
        path: Path,
        message: str,
        fixture_id: str | None = None,
        line: int | None = None,
    ) -> None:
        self.warning_entries.append(self._diagnostic(path, message, fixture_id, line))

    def merge(self, other: "Report") -> None:
        """Absorb another report's diagnostics (used to combine per-root reports)."""
        self.error_entries.extend(other.error_entries)
        self.warning_entries.extend(other.warning_entries)
        self.fixture_ids.update(other.fixture_ids)

    @property
    def errors(self) -> list[str]:
        """The error messages in text form, in the order they were reported."""
        return [entry.format() for entry in self.error_entries]

    @property
    def warnings(self) -> list[str]:
        """The warning messages in text form, in the order they were reported."""
        return [entry.format() for entry in self.warning_entries]

    @property
    def ok(self) -> bool:
        return not self.error_entries


@dataclass(frozen=True)
class UnsafeCharacter:
    """One forbidden code point and its 1-based source location (F-048)."""

    codepoint: int
    line: int
    column: int


def _unsafe_character_problem(character: str) -> str | None:
    """Why ``character`` is forbidden in fixture/evidence text, or ``None``."""
    if character in BIDI_CONTROL_CHARACTERS:
        return (
            "is a bidirectional control character, which can make the file "
            "display differently from its bytes"
        )
    if character == BYTE_ORDER_MARK:
        return "is a byte order mark (BOM) that is not at the start of the file"
    category = unicodedata.category(character)
    if category == "Cf":
        return "is an invisible format character"
    if category == "Cc" and character not in ALLOWED_CONTROL_CHARACTERS:
        return "is a control character other than tab, line feed or carriage return"
    return None


def find_unsafe_characters(text: str) -> list[UnsafeCharacter]:
    """Every forbidden code point in ``text`` with its 1-based line and column.

    The scan is deliberately over decoded text, not TOML fields, so the same
    rule covers comments, keys, string values and evidence JSON. A BOM is
    allowed only at offset zero; every other occurrence is reported. The
    function reports and never rewrites: the caller decides what to do.
    """
    findings: list[UnsafeCharacter] = []
    line = 1
    column = 1
    for index, character in enumerate(text):
        if character == "\n":
            line += 1
            column = 1
            continue
        if index == 0 and character == BYTE_ORDER_MARK:
            column += 1
            continue
        if _unsafe_character_problem(character) is not None:
            findings.append(
                UnsafeCharacter(codepoint=ord(character), line=line, column=column)
            )
        column += 1
    return findings


def report_unsafe_characters(path: Path, text: str, report: "Report") -> None:
    """Report every forbidden code point in ``text`` (F-048)."""
    for finding in find_unsafe_characters(text):
        character = chr(finding.codepoint)
        problem = _unsafe_character_problem(character)
        name = unicodedata.name(character, "unnamed character")
        report.error(
            path,
            f"line {finding.line}, column {finding.column}: forbidden code point "
            f"U+{finding.codepoint:04X} ({name}) {problem}; fixture and evidence "
            "text must not contain bidirectional, invisible or disallowed control "
            "characters",
            line=finding.line,
        )


def _is_pinned_ref(ref: str) -> bool:
    return bool(_COMMIT_REF_RE.fullmatch(ref) or _TAG_REF_RE.fullmatch(ref))


def check_source_reference(value: str) -> str | None:
    """Return a message describing why ``value`` is not an acceptable
    ``source_reference``, or ``None`` when it is.

    The messages say what to change, because the usual cause is a link that
    works today and is merely not pinned.
    """
    if len(value) > MAX_SOURCE_REFERENCE_LENGTH:
        return (
            f"is {len(value)} characters; a reference longer than "
            f"{MAX_SOURCE_REFERENCE_LENGTH} is not a link a reader can follow"
        )
    if value != value.strip() or any(ch.isspace() for ch in value):
        return "must not contain whitespace; use a CAP identifier or a single URL"
    if not _SOURCE_REFERENCE_RE.fullmatch(value):
        if value.startswith("http://"):
            return "must use https://, not http://"
        if value.startswith("https://") and "@" in value[len("https://"):].split("/")[0]:
            return "must not embed credentials (user@host) in the URL"
        return (
            "must be a CAP identifier such as 'CAP-0083' or an absolute https:// "
            "URL with a path (for example a CAP, RFC or documentation page); "
            "free text and relative links are not accepted"
        )
    if value.startswith("CAP-"):
        return None

    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if not host or "." not in host:
        return f"has no valid host name in {value!r}"
    if parts.username is not None or parts.password is not None:
        return "must not embed credentials (user@host) in the URL"

    if host in _GITHUB_HOSTS:
        if len(segments) < 3:
            return (
                "points at a GitHub repository root, which follows the default "
                "branch; link to a file or page at a commit or tag instead"
            )
        kind = segments[2]
        if kind in _GITHUB_REF_KINDS:
            if len(segments) < 4:
                return (
                    f"has no ref after '/{kind}/'; link to a specific file at a "
                    "commit or tag"
                )
            ref = segments[3]
            if not _is_pinned_ref(ref):
                return (
                    f"links to GitHub ref {ref!r}, which is a branch name and "
                    "moves; pin the link to a full commit SHA or a release tag "
                    f"(for example .../{kind}/<40-hex commit>/...)"
                )
    elif host == _GITHUB_RAW_HOST:
        # /<owner>/<repo>/<ref>/<path...>, or /<owner>/<repo>/refs/(heads|tags)/<name>/...
        if len(segments) < 4:
            return "has no ref and file path; link to a file at a commit or tag"
        if segments[2] == "refs":
            if len(segments) < 5 or segments[3] != "tags":
                return (
                    "links to a branch (refs/heads/...); pin the link to a full "
                    "commit SHA or a release tag"
                )
            ref = segments[4]
        else:
            ref = segments[2]
        if not _is_pinned_ref(ref):
            return (
                f"links to ref {ref!r}, which is a branch name and moves; pin "
                "the link to a full commit SHA or a release tag"
            )
    return None


def find_fixture_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.toml") if p.is_file())


def _toml_error_line(exc: tomllib.TOMLDecodeError) -> int | None:
    """The 1-based line a TOML syntax error was raised at, when known.

    Python 3.14+ exposes it as ``TOMLDecodeError.lineno``; older (3.11/3.12)
    interpreters only name it in the message text, so fall back to that rather
    than reporting no line for a diagnostic whose exact position the parser
    already computed.
    """
    lineno = getattr(exc, "lineno", None)
    if isinstance(lineno, int) and lineno > 0:
        return lineno
    match = re.search(r"\(at line (\d+), column \d+\)", str(exc))
    return int(match.group(1)) if match else None


def _field_line(path: Path, key: str) -> int | None:
    """The 1-based line defining top-level ``key`` in ``path``, else ``None``.

    Only used for fields the validator has already found must exist (so the
    key is a top-level fixture field, not an arbitrary needle). ``None`` is
    returned when the file cannot be read or the key is not defined exactly
    once -- for example it also appears in a comment -- because pointing at the
    wrong line would mislead more than omitting the line does.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=")
    matches = [n for n, line in enumerate(text.splitlines(), 1) if pattern.match(line)]
    return matches[0] if len(matches) == 1 else None


def load_fixture(path: Path, report: Report) -> Fixture | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        report.error(path, f"not valid UTF-8: {exc}")
        return None
    except OSError as exc:
        report.error(path, f"failed to read file: {exc}")
        return None
    report_unsafe_characters(path, raw, report)
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        report.error(path, f"invalid TOML: {exc}", line=_toml_error_line(exc))
        return None
    return Fixture(path=path, data=data)


def known_fixture_id(fx: Fixture) -> str | None:
    """Return ``fx``'s id when it is present and valid, else ``None``.

    Only a non-empty, lowercase string counts: those are exactly the
    properties ``validate_common_fields`` requires of ``id``. An id that is
    missing, non-string, empty, or not lowercase is not yet known to be
    valid -- the failure is itself reported by ``validate_common_fields`` --
    so messages about that fixture fall back to a path-only format.
    """
    fid = fx.data.get("id")
    if isinstance(fid, str) and fid and fid == fid.lower():
        return fid
    return None


def _require(data: dict, key: str, expected_type: type, path: Path, report: Report) -> bool:
    if key not in data:
        report.error(path, f"missing required field {key!r}")
        return False
    val = data[key]
    if (expected_type is int and isinstance(val, bool)) or not isinstance(val, expected_type):
        report.error(
            path,
            f"field {key!r} must be of type {expected_type.__name__}, "
            f"got {type(val).__name__}",
            line=_field_line(path, key),
        )
        return False
    return True


def validate_common_fields(fx: Fixture, report: Report) -> None:
    data, path = fx.data, fx.path

    ok_id = _require(data, "id", str, path, report)
    if ok_id and not data["id"]:
        report.error(path, "field 'id' must not be empty", line=_field_line(path, "id"))
    if ok_id and data["id"] != data["id"].lower():
        report.error(path, "field 'id' must be lowercase", line=_field_line(path, "id"))

    # `protocol` has no upper bound on purpose: this validator deliberately
    # does not maintain a list of supported Stellar protocol versions, so
    # shipping a new protocol never requires a validator change here. The
    # cost is that a stray or typo'd value (e.g. 82 where 28 was meant)
    # passes structural validation silently. Catching that is a pack-level
    # responsibility, not this validator's: each pack's own test suite
    # asserts every fixture under protocol-NN/ targets NN (see
    # tests/test_pack_protocol_28.py's test_every_fixture_targets_protocol_28).
    # See CONTRIBUTING.md's "Fixture schema" section for the same note.
    ok_protocol = _require(data, "protocol", int, path, report)
    if ok_protocol and data["protocol"] < 1:
        report.error(
            path,
            "field 'protocol' must be a positive integer",
            line=_field_line(path, "protocol"),
        )

    ok_surface = _require(data, "surface", str, path, report)
    if ok_surface and data["surface"] not in SURFACES:
        report.error(
            path,
            f"field 'surface' must be one of {sorted(SURFACES)}, got {data['surface']!r}",
            line=_field_line(path, "surface"),
        )

    ok_category = _require(data, "category", str, path, report)
    if ok_category and not data["category"]:
        report.error(path, "field 'category' must not be empty", line=_field_line(path, "category"))
    if ok_category and data["category"].lower() in VAGUE_CATEGORIES:
        report.error(
            path,
            f"field 'category' is too vague ({data['category']!r}); "
            "use a specific CAP/topic slug",
            line=_field_line(path, "category"),
        )

    ok_description = _require(data, "description", str, path, report)
    if ok_description and not data["description"]:
        report.error(
            path,
            "field 'description' must not be empty",
            line=_field_line(path, "description"),
        )

    if "source_reference" in data:
        if not isinstance(data["source_reference"], str) or not data["source_reference"]:
            report.error(
                path,
                "field 'source_reference', if present, must be a non-empty string",
                line=_field_line(path, "source_reference"),
            )
        else:
            problem = check_source_reference(data["source_reference"])
            if problem is not None:
                report.error(
                    path,
                    f"field 'source_reference' {problem} (got {data['source_reference']!r})",
                    line=_field_line(path, "source_reference"),
                )
    else:
        report.error(
            path,
            "no 'source_reference' set; protocol-specific fixtures must cite an "
            "authoritative upstream source (e.g. CAP-0083 or "
            "https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getNetwork)",
        )

    if "required_capabilities" in data:
        caps = data["required_capabilities"]
        if not isinstance(caps, list):
            report.error(
                path,
                "field 'required_capabilities' must be an array",
                line=_field_line(path, "required_capabilities"),
            )
        else:
            seen_caps: set[str] = set()
            for cap in caps:
                if cap in seen_caps:
                    report.error(
                        path,
                        f"duplicate entry {cap!r} in 'required_capabilities'",
                        line=_field_line(path, "required_capabilities"),
                    )
                else:
                    seen_caps.add(cap)
                if cap not in CAPABILITIES:
                    report.error(
                        path,
                        f"unknown capability {cap!r}; expected one of {sorted(CAPABILITIES)}",
                        line=_field_line(path, "required_capabilities"),
                    )

    for file_field in ("input_file", "expected_file"):
        if file_field in data:
            if not isinstance(data[file_field], str) or not data[file_field]:
                report.error(
                    path,
                    f"field {file_field!r}, if present, must be a non-empty string",
                    line=_field_line(path, file_field),
                )
                continue
            referenced = (path.parent / data[file_field]).resolve()
            if not referenced.is_file():
                report.error(
                    path,
                    f"{file_field} {data[file_field]!r} does not resolve to an existing file "
                    f"(resolved: {referenced})",
                    line=_field_line(path, file_field),
                )


def validate_top_level_keys(fx: Fixture, report: Report) -> None:
    """Reject top-level keys the fixture contract does not declare (F-031).

    The top level accepts the common fields plus exactly the keys its own
    surface consumes, so a typo'd field (``descriptoin``) or one belonging
    to a different surface fails here instead of passing silently while the
    intended field is reported missing. The check runs only for a
    recognized ``surface``: with an unknown surface (already reported by
    ``validate_common_fields``) every surface key would look undeclared and
    bury the real error in noise.
    """
    surface = fx.data.get("surface")
    if not isinstance(surface, str) or surface not in SURFACE_FIELDS:
        return
    allowed = (
        set(COMMON_REQUIRED_FIELDS)
        | set(COMMON_OPTIONAL_FIELDS)
        | set(SURFACE_FIELDS[surface])
    )
    for key in sorted(set(fx.data) - allowed):
        report.error(
            fx.path,
            f"unexpected top-level key {key!r}; a {surface!r} fixture accepts only "
            f"{sorted(allowed)}",
            line=_field_line(fx.path, key),
        )


def _validate_base64(value: str, field_name: str, path: Path, report: Report) -> None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        report.error(
            path,
            f"field {field_name!r} is not valid base64: {exc}",
            line=_field_line(path, field_name),
        )
        return
    if base64.b64encode(decoded).decode("ascii") != value:
        report.error(
            path,
            f"field {field_name!r} is not canonical base64",
            line=_field_line(path, field_name),
        )


def validate_xdr_body(fx: Fixture, report: Report) -> None:
    data, path = fx.data, fx.path

    ok_type = _require(data, "type", str, path, report)
    if ok_type and data["type"] not in XDR_TYPES:
        report.error(
            path,
            f"field 'type' must be one of {sorted(XDR_TYPES)} (what canary-xdr currently "
            f"supports), got {data['type']!r}",
            line=_field_line(path, "type"),
        )

    ok_kind = _require(data, "kind", str, path, report)
    if ok_kind and data["kind"] not in XDR_KINDS:
        report.error(
            path,
            f"field 'kind' must be one of {sorted(XDR_KINDS)}, got {data['kind']!r}",
            line=_field_line(path, "kind"),
        )

    if _require(data, "value_base64", str, path, report):
        if not data["value_base64"]:
            report.error(
                path,
                "field 'value_base64' must not be empty",
                line=_field_line(path, "value_base64"),
            )
        _validate_base64(data["value_base64"], "value_base64", path, report)

    if (ok_kind and data["kind"] == "encode-equals") or "expected_base64" in data:
        if _require(data, "expected_base64", str, path, report):
            if not data["expected_base64"]:
                report.error(
                    path,
                    "field 'expected_base64' must not be empty",
                    line=_field_line(path, "expected_base64"),
                )
            _validate_base64(data["expected_base64"], "expected_base64", path, report)


def validate_rpc_body(fx: Fixture, report: Report) -> None:
    data, path = fx.data, fx.path

    ok_method = _require(data, "method", str, path, report)
    if ok_method and data["method"] not in RPC_METHODS:
        report.error(
            path,
            f"field 'method' must be one of {sorted(RPC_METHODS)}, got {data['method']!r}",
            line=_field_line(path, "method"),
        )

    if "assert" not in data:
        report.error(path, "rpc fixture must define at least one [[assert]] table")
        return
    asserts = data["assert"]
    if not isinstance(asserts, list) or not asserts:
        report.error(path, "'assert' must be a non-empty array of tables")
        return
    for i, a in enumerate(asserts):
        if not isinstance(a, dict):
            report.error(path, f"assert[{i}] must be a table")
            continue
        kind = a.get("kind")
        if not isinstance(kind, str) or kind not in RPC_ASSERT_KINDS:
            actual = type(kind).__name__ if not isinstance(kind, str) else repr(kind)
            report.error(
                path,
                f"assert[{i}].kind must be one of {sorted(RPC_ASSERT_KINDS)}, got {actual}",
            )
        if kind == "field-number-range":
            allowed_keys = {"kind", "field", "min", "max"}
        else:
            allowed_keys = set(RPC_ASSERT_FIELDS)
        unknown_assert_keys = sorted(set(a) - allowed_keys)
        if unknown_assert_keys:
            report.error(
                path,
                f"unexpected key(s) {', '.join(repr(k) for k in unknown_assert_keys)} in "
                f"assert[{i}]; accepted keys are {sorted(allowed_keys)}",
            )
        field_value = a.get("field")
        if field_value is None or field_value == "":
            report.error(path, f"assert[{i}] missing required non-empty 'field'")
        elif not isinstance(field_value, str):
            report.error(
                path,
                f"assert[{i}].field must be a non-empty string, "
                f"got {type(field_value).__name__}",
            )
        if kind == "field-equals" and "value" not in a:
            report.error(path, f"assert[{i}] with kind=field-equals requires 'value'")
        if kind == "field-type":
            expected_type = a.get("expected_type")
            if not isinstance(expected_type, str) or expected_type not in RPC_ASSERT_TYPES:
                actual = (
                    type(expected_type).__name__
                    if not isinstance(expected_type, str)
                    else repr(expected_type)
                )
                report.error(
                    path,
                    f"assert[{i}].expected_type must be one of {sorted(RPC_ASSERT_TYPES)}, "
                    f"got {actual}",
                )
        if kind == "field-number-range":
            has_min = "min" in a
            has_max = "max" in a
            if not has_min and not has_max:
                report.error(
                    path,
                    f"assert[{i}] with kind=field-number-range requires 'min' or 'max'",
                )
            min_val = None
            max_val = None
            if has_min:
                val = a["min"]
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    report.error(
                        path,
                        f"assert[{i}].min must be a number, got {type(val).__name__}",
                    )
                elif math.isnan(val) or math.isinf(val):
                    report.error(
                        path,
                        f"assert[{i}].min must be a finite number, got {val}",
                    )
                else:
                    min_val = val
            if has_max:
                val = a["max"]
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    report.error(
                        path,
                        f"assert[{i}].max must be a number, got {type(val).__name__}",
                    )
                elif math.isnan(val) or math.isinf(val):
                    report.error(
                        path,
                        f"assert[{i}].max must be a finite number, got {val}",
                    )
                else:
                    max_val = val
            if min_val is not None and max_val is not None and min_val > max_val:
                report.error(
                    path,
                    f"assert[{i}] min {min_val} greater than max {max_val}",
                )


# ------------------------------------------------------- Soroban arguments
#
# F-051 (issue #325). canary-soroban parses every [[args]] entry as one
# `{ kind, value }` table, converts it to an ScVal, and rejects unknown keys,
# bad values and vec/map nesting deeper than MAX_ARG_DEPTH. The helpers below
# mirror parse_arg_at in crates/canary-soroban/src/runner.rs so a malformed
# argument is caught by the Python validator -- which contributors run first
# -- instead of only when the engine loads the fixture.

_WIDE_INTEGER_BOUNDS = {
    "u128": (0, 2**128 - 1),
    "i128": (-(2**127), 2**127 - 1),
    "u256": (0, 2**256 - 1),
    "i256": (-(2**255), 2**255 - 1),
}


def _wide_integer_problem(kind: str, value: object) -> str | None:
    """Why ``value`` is not a valid ``kind`` literal, or ``None`` when it is.

    Mirrors the engine's split_integer_literal/parse_{u,i}{128,256}_literal:
    an optional sign, an optional 0x prefix, then decimal or hex digits (no
    underscores, floats, or whitespace), followed by a range check.
    """
    if not isinstance(value, str):
        return (
            f"kind {kind!r} requires a decimal or 0x-prefixed hex string, "
            f"got {type(value).__name__}"
        )
    text = value
    negative = False
    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    radix = 10
    if text.startswith("0x") or text.startswith("0X"):
        radix = 16
        text = text[2:]
    digits = _HEX_RE.fullmatch(text) if radix == 16 else re.fullmatch(r"[0-9]+", text)
    if not text or digits is None:
        return (
            f"kind {kind!r} requires a decimal or 0x-prefixed hex string, "
            f"got {value!r}"
        )
    magnitude = int(text, radix)
    if kind.startswith("u") and negative:
        return f"kind {kind!r} must not be negative, got {value!r}"
    parsed = -magnitude if negative else magnitude
    lower, upper = _WIDE_INTEGER_BOUNDS[kind]
    if parsed < lower:
        return f"kind {kind!r} value {value!r} is below the minimum {lower}"
    if parsed > upper:
        return f"kind {kind!r} value {value!r} is above the maximum {upper}"
    return None


def _bytes_problem(value: str) -> str | None:
    """Why ``value`` is not the bytes literal the engine accepts, or ``None``.

    A 0x/0X prefix selects hex (even number of digits); anything else is
    standard padded base64, which must have a multiple-of-four length, at
    most two '=' characters and zero unused trailing bits (the engine's
    decode_base64 enforces all three).
    """
    if value.startswith("0x") or value.startswith("0X"):
        digits = value[2:]
        if len(digits) % 2:
            return f"hex string has an odd number of digits ({value!r})"
        if _HEX_RE.fullmatch(digits) is None:
            return f"hex string contains a character outside 0-9a-fA-F ({value!r})"
        return None

    if len(value) % 4:
        return (
            "base64 string length must be a multiple of 4 (use '=' padding), "
            f"got {value!r}"
        )
    padding = len(value) - len(value.rstrip("="))
    if padding > 2:
        return f"base64 string has too much padding ({value!r})"
    body = value[: len(value) - padding] if padding else value
    if _BASE64_RE.fullmatch(body) is None:
        return f"base64 string contains a character outside the standard alphabet ({value!r})"
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        return f"is not valid base64: {exc}"
    if base64.b64encode(decoded).decode("ascii") != value:
        return (
            "is not canonical base64: unused trailing bits must be zero "
            f"({value!r})"
        )
    return None


def _address_problem(value: str) -> str | None:
    """Shape-check a G.../C... strkey without verifying its checksum.

    Matches the engine's two accepted Strkey variants. A secret seed (S...)
    is rejected explicitly, exactly as parse_address does, and is never
    echoed back in the message.
    """
    if value.startswith("S"):
        return (
            "must not be a secret seed; expected a G... account or C... contract "
            "address (value not shown)"
        )
    if _STRKEY_RE.fullmatch(value) is None:
        return f"must be a 56-character G... account or C... contract strkey, got {value!r}"
    return None


def _validate_soroban_arg(
    entry: object, path: Path, report: Report, label: str, depth: int
) -> None:
    """Validate one argument table, recursing into vec/map values.

    ``depth`` is how many vec/map containers already enclose this entry, the
    same counter the engine's parse_arg_at carries.
    """
    if not isinstance(entry, dict):
        report.error(
            path,
            f"{label} must be a table with 'kind' and 'value' keys, "
            f"got {type(entry).__name__}",
        )
        return

    unknown = sorted(set(entry) - set(SOROBAN_ARG_FIELDS))
    if unknown:
        report.error(
            path,
            f"unexpected key(s) {', '.join(repr(k) for k in unknown)} in {label}; "
            f"canary-soroban accepts only {list(SOROBAN_ARG_FIELDS)}",
        )

    kind = entry.get("kind")
    if not isinstance(kind, str):
        report.error(
            path,
            f"{label} must define a string 'kind'; expected one of "
            f"{sorted(SOROBAN_ARG_KINDS)}, got {kind!r}",
        )
        return
    if kind not in SOROBAN_ARG_KINDS:
        report.error(
            path,
            f"{label}.kind must be one of {sorted(SOROBAN_ARG_KINDS)}, got {kind!r}",
        )
        return

    # void carries no payload; any value present is ignored by the engine.
    if kind == "void":
        return
    if "value" not in entry:
        report.error(path, f"{label} with kind={kind!r} is missing its 'value'")
        return

    _validate_soroban_arg_value(kind, entry["value"], path, report, f"{label}.value", depth)


def _validate_soroban_arg_value(
    kind: str, value: object, path: Path, report: Report, label: str, depth: int
) -> None:
    if kind == "bool":
        if not isinstance(value, bool):
            report.error(
                path,
                f"{label} for kind 'bool' must be a boolean, got {type(value).__name__}",
            )
        return

    if kind in {"u32", "i32", "u64", "i64"}:
        bounds = {
            "u32": (0, 2**32 - 1),
            "i32": (-(2**31), 2**31 - 1),
            "u64": (0, 2**64 - 1),
            "i64": (-(2**63), 2**63 - 1),
        }[kind]
        if not _is_int(value):
            report.error(
                path,
                f"{label} for kind {kind!r} must be an integer, "
                f"got {type(value).__name__}",
            )
        elif not bounds[0] <= value <= bounds[1]:
            report.error(
                path,
                f"{label} for kind {kind!r} is outside the allowed range "
                f"{bounds[0]}..{bounds[1]}, got {value}",
            )
        return

    if kind in _WIDE_INTEGER_BOUNDS:
        problem = _wide_integer_problem(kind, value)
        if problem is not None:
            report.error(path, f"{label}: {problem}")
        return

    if kind in {"duration", "timepoint"}:
        if not _is_int(value):
            report.error(
                path,
                f"{label} for kind {kind!r} must be a non-negative integer, "
                f"got {type(value).__name__}",
            )
        elif not 0 <= value <= 2**64 - 1:
            report.error(
                path,
                f"{label} for kind {kind!r} must be a non-negative 64-bit "
                f"integer, got {value}",
            )
        return

    if kind == "symbol":
        if not isinstance(value, str):
            report.error(
                path,
                f"{label} for kind 'symbol' must be a string, got {type(value).__name__}",
            )
        elif _SYMBOL_RE.fullmatch(value) is None:
            report.error(
                path,
                f"{label} for kind 'symbol' must be at most 32 characters "
                f"from [A-Za-z0-9_], got {value!r}",
            )
        return

    if kind == "string":
        if not isinstance(value, str):
            report.error(
                path,
                f"{label} for kind 'string' must be a string, got {type(value).__name__}",
            )
        return

    if kind == "bytes":
        if not isinstance(value, str):
            report.error(
                path,
                f"{label} for kind 'bytes' must be a string, got {type(value).__name__}",
            )
            return
        problem = _bytes_problem(value)
        if problem is not None:
            report.error(path, f"{label} {problem}")
        return

    if kind == "address":
        if not isinstance(value, str):
            report.error(
                path,
                f"{label} for kind 'address' must be a string, got {type(value).__name__}",
            )
            return
        problem = _address_problem(value)
        if problem is not None:
            report.error(path, f"{label} {problem}")
        return

    if kind == "vec":
        if not isinstance(value, list):
            report.error(
                path,
                f"{label} for kind 'vec' must be an array of {{ kind, value }} "
                f"tables, got {type(value).__name__}",
            )
            return
        if depth + 1 > MAX_ARG_DEPTH:
            report.error(
                path,
                f"{label}: vec/map arguments are nested deeper than the limit "
                f"of {MAX_ARG_DEPTH} levels",
            )
            return
        for i, item in enumerate(value):
            _validate_soroban_arg(item, path, report, f"{label}[{i}]", depth + 1)
        return

    if kind == "map":
        if not isinstance(value, list):
            report.error(
                path,
                f"{label} for kind 'map' must be an array of {{ key, value }} "
                f"tables, got {type(value).__name__}",
            )
            return
        if depth + 1 > MAX_ARG_DEPTH:
            report.error(
                path,
                f"{label}: vec/map arguments are nested deeper than the limit "
                f"of {MAX_ARG_DEPTH} levels",
            )
            return
        for i, item in enumerate(value):
            entry_label = f"{label}[{i}]"
            if not isinstance(item, dict):
                report.error(
                    path,
                    f"{entry_label} must be a table with 'key' and 'value' "
                    f"entries, got {type(item).__name__}",
                )
                continue
            unknown = sorted(set(item) - set(SOROBAN_MAP_ENTRY_FIELDS))
            if unknown:
                report.error(
                    path,
                    f"unexpected key(s) {', '.join(repr(k) for k in unknown)} in "
                    f"{entry_label}; canary-soroban accepts only "
                    f"{list(SOROBAN_MAP_ENTRY_FIELDS)}",
                )
            if "key" not in item:
                report.error(path, f"{entry_label} map entry is missing its 'key'")
            else:
                _validate_soroban_arg(
                    item["key"], path, report, f"{entry_label}.key", depth + 1
                )
            if "value" not in item:
                report.error(path, f"{entry_label} map entry is missing its 'value'")
            else:
                _validate_soroban_arg(
                    item["value"], path, report, f"{entry_label}.value", depth + 1
                )


_BASE32_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def crc16_xmodem(data: bytes) -> int:
    """CRC16-XModem (polynomial 0x1021, initial value 0)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def decode_strkey(
    encoded: str, expected_version: int | None = None
) -> tuple[bytes | None, str | None]:
    """Decode a Stellar strkey into its 32-byte payload or return a failure reason.

    Uses only the standard library: base32 (RFC 4648, no padding), 35 decoded bytes,
    version byte (6 << 3 for G... accounts, 2 << 3 for C... contracts), 32-byte payload,
    and CRC16-XModem checksum (polynomial 0x1021, init 0, little-endian) of the first
    33 bytes. Returns (payload, None) on success or (None, reason) on failure.
    """
    if len(encoded) != 56:
        return None, f"wrong length: expected 56 characters, got {len(encoded)}"
    if any(c.islower() for c in encoded):
        return None, "strkey must not contain lower-case characters"
    if not all(c in _BASE32_ALPHABET for c in encoded):
        return None, "strkey contains non-base32 character(s)"
    if encoded.startswith("S"):
        return None, "must not be a secret seed (value not shown)"
    if encoded.startswith("M"):
        return None, "wrong prefix: muxed account (M...) not supported"
    if expected_version == (6 << 3) and encoded.startswith("C"):
        return None, "wrong prefix: expected G... account strkey, got C... contract"
    if expected_version == (2 << 3) and encoded.startswith("G"):
        return None, "wrong prefix: expected C... contract strkey, got G... account"
    if expected_version == (6 << 3) and not encoded.startswith("G"):
        return None, f"wrong prefix: expected G... account strkey, got prefix {encoded[0]!r}"
    if expected_version == (2 << 3) and not encoded.startswith("C"):
        return None, f"wrong prefix: expected C... contract strkey, got prefix {encoded[0]!r}"

    try:
        raw = base64.b32decode(encoded)
    except Exception as e:
        return None, f"invalid base32 encoding: {e}"

    if len(raw) != 35:
        return None, f"wrong decoded length: expected 35 bytes, got {len(raw)}"

    if expected_version is not None and raw[0] != expected_version:
        return None, f"wrong prefix / version byte: expected {expected_version}, got {raw[0]}"

    crc = crc16_xmodem(raw[:33])
    expected_crc = int.from_bytes(raw[33:35], "little")
    if crc != expected_crc:
        return None, f"bad checksum (expected {expected_crc:04x}, got {crc:04x})"

    return raw[1:33], None


def validate_soroban_body(fx: Fixture, report: Report) -> None:
    data, path = fx.data, fx.path

    if _require(data, "source_account", str, path, report):
        if not data["source_account"]:
            report.error(
                path, "field 'source_account' must not be empty", line=_field_line(path, "source_account")
            )
        else:
            payload, reason = decode_strkey(data["source_account"], expected_version=6 << 3)
            if reason is not None:
                report.error(
                    path,
                    f"field 'source_account' is not a valid G... strkey: {reason}",
                    line=_field_line(path, "source_account"),
                )

    if _require(data, "contract_id", str, path, report):
        if not data["contract_id"]:
            report.error(
                path, "field 'contract_id' must not be empty", line=_field_line(path, "contract_id")
            )
        else:
            payload, reason = decode_strkey(data["contract_id"], expected_version=2 << 3)
            if reason is not None:
                report.error(
                    path,
                    f"field 'contract_id' is not a valid C... strkey: {reason}",
                    line=_field_line(path, "contract_id"),
                )

    if _require(data, "function", str, path, report):
        if not data["function"]:
            report.error(
                path, "field 'function' must not be empty", line=_field_line(path, "function")
            )
        elif len(data["function"].encode("utf-8")) > 32:
            report.error(
                path,
                f"field 'function' exceeds 32 bytes ({len(data['function'].encode('utf-8'))} bytes in UTF-8)",
                line=_field_line(path, "function"),
            )

    _require(data, "sequence_number", int, path, report)

    # 'args' is optional but, when present, must match the schema's
    # "args": { "type": "array" } declaration (see
    # schemas/fixture-v1.schema.json's soroban conditional). Each entry is
    # then validated as the { kind, value } table canary-soroban parses
    # (F-051, issue #325), with the fixture id and argument index in every
    # message so a contributor can find the offending entry.
    if "args" in data:
        args = data["args"]
        if not isinstance(args, list):
            report.error(
                path,
                f"field 'args', if present, must be an array, "
                f"got {type(args).__name__}",
            )
        else:
            for i, entry in enumerate(args):
                _validate_soroban_arg(entry, path, report, f"args[{i}]", 0)

    if "expect" not in data:
        report.error(path, "missing required field 'expect'")
        return
    expect = data["expect"]
    if not isinstance(expect, dict):
        report.error(path, "'expect' must be a table")
        return

    # canary-soroban rejects an unrecognized key inside [expect], so a
    # misspelled `message_contain` is a loader error rather than a silently
    # weaker assertion. Mirroring that here means `make validate` catches the
    # typo instead of passing a fixture that only fails when someone runs it.
    unknown_expect_keys = sorted(set(expect) - set(SOROBAN_EXPECT_FIELDS))
    if unknown_expect_keys:
        report.error(
            path,
            f"unexpected key(s) {', '.join(repr(k) for k in unknown_expect_keys)} in "
            f"'expect'; canary-soroban accepts only {list(SOROBAN_EXPECT_FIELDS)}",
        )

    kind = expect.get("kind")
    if not isinstance(kind, str) or kind not in SOROBAN_EXPECT_KINDS:
        actual = type(kind).__name__ if not isinstance(kind, str) else repr(kind)
        report.error(
            path,
            f"expect.kind must be one of {sorted(SOROBAN_EXPECT_KINDS)}, got {actual}",
            line=_field_line(path, "kind"),
        )

    # `message_contains` is genuinely optional: canary-soroban models it as
    # Option<String>, so a simulation-error fixture may assert only that the
    # simulation failed. But when it *is* present it has to be a usable needle.
    # The loader reads it with `.and_then(toml::Value::as_str)`, which turns a
    # non-string into None, and `str::contains("")` is always true -- either
    # way the fixture silently asserts less than it appears to. That is the trap
    # this closes; schemas/fixture-v1.schema.json already declares the field a
    # string, so this brings the validator in line with the schema.
    if "message_contains" in expect:
        needle = expect["message_contains"]
        if not isinstance(needle, str):
            report.error(
                path,
                f"expect.message_contains must be a string, got {type(needle).__name__}; "
                "canary-soroban ignores a non-string value, which would silently weaken "
                "the assertion to 'the simulation failed'",
                line=_field_line(path, "message_contains"),
            )
        elif not needle:
            report.error(
                path,
                "expect.message_contains must not be empty; an empty needle matches every "
                "error message, so the assertion would be no stronger than omitting it",
                line=_field_line(path, "message_contains"),
            )


def validate_body(fx: Fixture, report: Report) -> None:
    surface = fx.data.get("surface")
    if surface == "xdr":
        validate_xdr_body(fx, report)
    elif surface == "rpc":
        validate_rpc_body(fx, report)
    elif surface == "soroban":
        validate_soroban_body(fx, report)
    # An invalid/missing surface was already reported by validate_common_fields.


def validate_unique_ids(
    fixtures: list[Fixture],
    report: Report,
    seen: dict[str, Path] | None = None,
) -> dict[str, Path]:
    if seen is None:
        seen = {}
    for fx in fixtures:
        fid = fx.data.get("id")
        if not isinstance(fid, str):
            continue
        if fid in seen:
            report.error(
                fx.path,
                f"duplicate fixture id {fid!r}: already defined in {seen[fid]}",
                line=_field_line(fx.path, "id"),
            )
        else:
            seen[fid] = fx.path
    return seen


def check_portable_paths(root: Path, report: Report) -> None:
    """Report every path under ``root`` that Windows cannot create (F-049).

    Every entry is checked, not only the ``*.toml`` fixtures: a payload named
    ``con.bin`` or a directory named ``bad.`` breaks extraction on Windows just
    as a fixture name does, so the validator must not let it through merely
    because ``find_fixture_files`` would never load it.
    """
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - rglob only yields descendants
            continue
        try:
            portable_names.check_path(relative)
        except portable_names.PortableNameError as error:
            report.error(path, f"non-portable file name: {error}")


def validate_directory(root: Path, seen_ids: dict[str, Path] | None = None) -> Report:
    report = Report()
    fixtures: list[Fixture] = []
    check_portable_paths(root, report)
    for path in find_fixture_files(root):
        fx = load_fixture(path, report)
        if fx is not None:
            fixtures.append(fx)

    for fx in fixtures:
        report.register_fixture_id(fx.path, known_fixture_id(fx))

    for fx in fixtures:
        validate_common_fields(fx, report)
        validate_body(fx, report)
        validate_top_level_keys(fx, report)

    validate_unique_ids(fixtures, report, seen=seen_ids)
    return report


# --------------------------------------------------- verification evidence
#
# CF-04 section 3 (``verificationVersion`` 1). The rules below mirror
# ``schemas/verification-v1.schema.json`` and add the two that a schema cannot
# express: a live record must carry the evidence it was made from, and a
# record must point at a fixture that exists with the hash that was checked.

EVIDENCE_VERSION = 1
EVIDENCE_METHODS = ("structural", "source-checked", "live-read-only", "live-simulation")
EVIDENCE_LIVE_METHODS = ("live-read-only", "live-simulation")
EVIDENCE_OUTCOMES = ("matched", "mismatched")
EVIDENCE_REQUIRED_FIELDS = (
    "verificationVersion",
    "fixtureId",
    "fixtureSha256",
    "method",
    "checkedAt",
    "verifier",
    "outcome",
)
EVIDENCE_OPTIONAL_FIELDS = ("network", "endpointHost", "evidenceRef")
_EVIDENCE_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EVIDENCE_HOST_RE = re.compile(r"[^/:?#@\s\\]+")
_EVIDENCE_INSTANT_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z"
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_instant(value: object) -> str | None:
    """Problem with ``checkedAt``, or None. RFC 3339, UTC, with a ``Z``."""
    if not isinstance(value, str) or not _EVIDENCE_INSTANT_RE.fullmatch(value):
        return "checkedAt must be an RFC 3339 UTC instant such as 2026-09-28T12:00:00Z"
    try:
        datetime.strptime(value.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return f"checkedAt {value!r} is not a real date and time"
    return None


def _check_evidence_ref(value: str) -> str | None:
    if value.startswith("/") or "\\" in value or "\0" in value:
        return "evidenceRef must be a relative path inside the repository"
    if any(part in ("", ".", "..") for part in value.split("/")):
        return "evidenceRef must not contain empty, '.' or '..' path segments"
    return None


def validate_evidence(record: object) -> list[str]:
    """Return every problem found in one verification record (CF-04 section 3).

    An empty list means the record is structurally valid. This checks the
    record on its own; ``check_evidence_against_fixtures`` checks that the
    fixture it names exists with the hash that was verified. Nothing here
    decides whether a record is *fresh* (CF-04 section 4); freshness is derived
    elsewhere and never stored.
    """
    if not isinstance(record, dict):
        return ["a verification record must be a JSON object"]

    problems: list[str] = []
    allowed = set(EVIDENCE_REQUIRED_FIELDS) | set(EVIDENCE_OPTIONAL_FIELDS)
    for name in EVIDENCE_REQUIRED_FIELDS:
        if name not in record:
            problems.append(f"missing required field {name!r}")
    for name in sorted(set(record) - allowed):
        problems.append(
            f"unknown field {name!r}; allowed fields are "
            + ", ".join(sorted(allowed))
        )

    version = record.get("verificationVersion")
    if "verificationVersion" in record and (not _is_int(version) or version != EVIDENCE_VERSION):
        problems.append(f"verificationVersion must be {EVIDENCE_VERSION}")

    if "fixtureId" in record and not _non_empty_str(record["fixtureId"]):
        problems.append("fixtureId must be a non-empty string")

    sha = record.get("fixtureSha256")
    if "fixtureSha256" in record and not (
        isinstance(sha, str) and _EVIDENCE_SHA256_RE.fullmatch(sha)
    ):
        problems.append("fixtureSha256 must be 64 lowercase hex characters")

    method = record.get("method")
    if "method" in record and method not in EVIDENCE_METHODS:
        problems.append(f"method must be one of {list(EVIDENCE_METHODS)}")

    if "checkedAt" in record:
        instant_problem = _check_instant(record["checkedAt"])
        if instant_problem:
            problems.append(instant_problem)

    if "outcome" in record and record["outcome"] not in EVIDENCE_OUTCOMES:
        problems.append(f"outcome must be one of {list(EVIDENCE_OUTCOMES)}")

    if "verifier" in record:
        verifier = record["verifier"]
        if not isinstance(verifier, dict):
            problems.append("verifier must be an object with name and version")
        else:
            for name in ("name", "version"):
                if not _non_empty_str(verifier.get(name)):
                    problems.append(f"verifier.{name} must be a non-empty string")
            for name in sorted(set(verifier) - {"name", "version"}):
                problems.append(f"unknown field {name!r} in verifier")

    network = record.get("network")
    if network is not None:
        if not isinstance(network, dict):
            problems.append("network must be an object or null")
        else:
            if not _non_empty_str(network.get("name")):
                problems.append("network.name must be a non-empty string")
            if not _non_empty_str(network.get("passphrase")):
                problems.append("network.passphrase must be a non-empty string")
            observed = network.get("observedProtocol")
            if not _is_int(observed) or observed < 0:
                problems.append("network.observedProtocol must be a non-negative integer")
            for name in sorted(set(network) - {"name", "passphrase", "observedProtocol"}):
                problems.append(f"unknown field {name!r} in network")

    host = record.get("endpointHost")
    if host is not None and not (isinstance(host, str) and _EVIDENCE_HOST_RE.fullmatch(host)):
        problems.append(
            "endpointHost must be a host name only: no scheme, port, path, query, "
            "credentials or whitespace"
        )

    reference = record.get("evidenceRef")
    if reference is not None:
        if not _non_empty_str(reference):
            problems.append("evidenceRef must be a non-empty string or null")
        else:
            reference_problem = _check_evidence_ref(reference)
            if reference_problem:
                problems.append(reference_problem)

    if method in EVIDENCE_LIVE_METHODS:
        if not isinstance(network, dict):
            problems.append(f"method {method!r} requires a network object (name, passphrase, observedProtocol)")
        if host is None:
            problems.append(f"method {method!r} requires endpointHost")
        if reference is None:
            problems.append(
                f"method {method!r} requires evidenceRef: a live record must point at "
                "the captured request and response it was made from"
            )
    return problems


def validate_evidence_file(path: Path, report: Report) -> dict | None:
    """Parse one evidence file and report its problems. Returns the record."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        report.error(path, f"cannot read the evidence file: {error}")
        return None
    report_unsafe_characters(path, text, report)
    try:
        record = json.loads(text)
    except json.JSONDecodeError as error:
        report.error(path, f"not valid JSON: {error}")
        return None
    problems = validate_evidence(record)
    for problem in problems:
        report.error(path, problem)
    return record if not problems else None


def find_evidence_files(target: Path) -> list[Path]:
    """``*.json`` files under a directory, or the file itself, sorted."""
    if target.is_dir():
        return sorted(p for p in target.rglob("*.json") if p.is_file())
    return [target]


def fixture_hashes(roots: list[Path]) -> dict[str, str]:
    """``{fixture id: sha256 of the fixture file}`` for every loadable fixture."""
    hashes: dict[str, str] = {}
    scratch = Report()
    for root in roots:
        for path in find_fixture_files(root):
            fx = load_fixture(path, scratch)
            fixture_id = known_fixture_id(fx) if fx is not None else None
            if fixture_id:
                hashes.setdefault(fixture_id, hashlib.sha256(path.read_bytes()).hexdigest())
    return hashes


def check_evidence_against_fixtures(
    path: Path, record: dict, hashes: dict[str, str], report: Report
) -> None:
    """The record's fixture must exist, and its hash must be the current one.

    A record whose ``fixtureSha256`` differs from the current file describes an
    earlier version of the fixture (CF-04 section 4, ``historical``): it is
    reported so it is never read as evidence for the file that is there now.
    """
    fixture_id = record.get("fixtureId")
    if fixture_id not in hashes:
        report.error(path, f"fixtureId {fixture_id!r} does not match any fixture")
    elif hashes[fixture_id] != record.get("fixtureSha256"):
        report.error(
            path,
            f"fixtureSha256 does not match the current {fixture_id!r} fixture file "
            f"(current {hashes[fixture_id]}); the record describes an earlier version",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description=(
            "Structural validator for ProtocolCanary-Fixtures. Performs structural "
            "validation only; never executes a fixture's assertion."
        ),
    )
    parser.add_argument(
        "roots",
        nargs="*",
        metavar="root",
        help=(
            "directories to scan for *.toml fixtures (default: every protocol-*/ "
            "directory next to this script)"
        ),
    )
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help=(
            "a verification record file, or a directory of *.json records (CF-04); "
            "may be given more than once. Each record is validated and must name a "
            "fixture in the scanned roots with the hash that was verified"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "suppress warning output in the text format; errors and the final "
            "OK/FAILED summary are still printed (JSON output is unaffected)"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help=(
            "output format: 'text' (default) is the human-readable report; "
            "'json' prints one structured JSON document on stdout and nothing "
            "on stderr"
        ),
    )
    return parser


def build_json_report(report: Report, roots: list[Path], total_files: int) -> dict:
    """The ``--format json`` document (F-023).

    ``valid`` mirrors the exit code: ``True`` when the run would exit 0. Every
    diagnostic is rendered from the same ``Diagnostic`` record the text report
    uses, so the two formats cannot disagree. The summary's counts are derived,
    not maintained separately: ``fixtures`` is the number of ``*.toml`` files
    discovered across ``roots``, and the error/warning counts are the lengths
    of the arrays above them.
    """
    return {
        "valid": report.ok,
        "errors": [entry.as_json() for entry in report.error_entries],
        "warnings": [entry.as_json() for entry in report.warning_entries],
        "summary": {
            "roots": [str(root) for root in roots],
            "fixtures": total_files,
            "errors": len(report.error_entries),
            "warnings": len(report.warning_entries),
        },
    }


def render_json(document: dict) -> str:
    """The canonical JSON encoding: two-space indent, one trailing newline.

    ``ensure_ascii=False`` keeps non-ASCII fixture paths and messages readable
    while ``json.dumps`` still escapes the characters JSON requires (quotes,
    backslashes, newlines), so the result always round-trips through
    ``json.loads``.
    """
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    roots = [Path(a) for a in args.roots] if args.roots else None
    if roots is None:
        repo_root = Path(__file__).resolve().parents[2]
        roots = sorted(p for p in repo_root.glob("protocol-*") if p.is_dir())
        if not roots:
            roots = [repo_root]
    else:
        seen_roots: set[Path] = set()
        deduped_roots: list[Path] = []
        for r in roots:
            resolved = r.resolve()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                deduped_roots.append(r)
        roots = deduped_roots

    combined = Report()
    total_files = 0
    seen_ids: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            combined.error(root, "not a directory")
            continue
        total_files += len(find_fixture_files(root))
        combined.merge(validate_directory(root, seen_ids=seen_ids))

    evidence_files: list[Path] = []
    for target in args.evidence:
        if not target.exists():
            combined.error(target, "evidence path does not exist")
            continue
        evidence_files.extend(find_evidence_files(target))
    if evidence_files:
        hashes = fixture_hashes([r for r in roots if r.is_dir()])
        for path in evidence_files:
            record = validate_evidence_file(path, combined)
            if record is not None:
                check_evidence_against_fixtures(path, record, hashes, combined)

    if args.format == "json":
        # One complete document on stdout and nothing else, so `| jq` and
        # `json.load` both see valid JSON regardless of whether the run passed.
        sys.stdout.write(render_json(build_json_report(combined, roots, total_files)))
        return 0 if combined.ok else 1

    if not args.quiet:
        for warning in combined.warnings:
            print(f"warning: {warning}")
    for error in combined.errors:
        print(f"error: {error}", file=sys.stderr)

    if combined.ok:
        summary = f"OK: {total_files} fixture file(s) valid across {len(roots)} root(s)"
        if evidence_files:
            summary += f"; {len(evidence_files)} evidence record(s) valid"
        print(summary)
        return 0
    print(f"FAILED: {len(combined.errors)} error(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

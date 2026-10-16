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
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
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
# released.
XDR_TYPES = {"StellarValue", "ContractExecutable"}
# Set of assertion kinds supported by canary-xdr for a given XDR value.
# Must be kept in sync with canary-xdr's supported assertion kinds.
XDR_KINDS = {"decode-success", "decode-failure", "roundtrip", "encode-equals"}
# Set of RPC methods supported by canary-rpc. Must be kept in sync with the
# methods canary-rpc actually implements: a value added here only names a string;
# the corresponding method must already exist upstream in canary-rpc.
RPC_METHODS = {"get-network", "get-latest-ledger"}
RPC_ASSERT_KINDS = {"field-exists", "field-absent", "field-type", "field-equals"}
# JSON type names accepted for RPC field-type assertions. Keep this set in
# sync with the `expected_type` enum in schemas/fixture-v1.schema.json.
RPC_ASSERT_TYPES = {"string", "number", "bool", "object", "array", "null"}
SOROBAN_EXPECT_KINDS = {"simulation-success", "simulation-error"}
# Keys canary-soroban accepts inside a soroban fixture's [expect] table. It
# rejects anything else outright (see the `unknown_body_keys(expect, ...)` call
# in canary-soroban/src/runner.rs), so this mirrors the loader rather than
# tightening it.
SOROBAN_EXPECT_FIELDS = ("kind", "message_contains")
# Set of kebab-case capability strings a fixture may list in
# `required_capabilities`. Must be kept in sync with
# canary_core::Capability in StellarCanary/Protocol-Canary, whose
# kebab-case names these mirror, AND with the required_capabilities
# enum in schemas/fixture-v1.schema.json (the same cross-reference is
# documented there). The two lists are maintained independently (a
# Python set and a JSON Schema enum), so adding a value to one without
# the other silently creates a validator/schema mismatch.
CAPABILITIES = {
    "soroban-contract",
    "rpc-client",
    "stellar-sdk-dependency",
    "wasm-artifact",
    "raw-ledger-access",
}
VAGUE_CATEGORIES = {"misc", "other", "test", "general"}

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
    except OSError as exc:
        report.error(path, f"failed to read file: {exc}")
        return None
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
    if not isinstance(data[key], expected_type):
        report.error(
            path,
            f"field {key!r} must be of type {expected_type.__name__}, "
            f"got {type(data[key]).__name__}",
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


def _validate_base64(value: str, field_name: str, path: Path, report: Report) -> None:
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        report.error(
            path,
            f"field {field_name!r} is not valid base64: {exc}",
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
        if kind not in RPC_ASSERT_KINDS:
            report.error(
                path,
                f"assert[{i}].kind must be one of {sorted(RPC_ASSERT_KINDS)}, got {kind!r}",
            )
        if not a.get("field"):
            report.error(path, f"assert[{i}] missing required non-empty 'field'")
        if kind == "field-equals" and "value" not in a:
            report.error(path, f"assert[{i}] with kind=field-equals requires 'value'")
        if kind == "field-type":
            expected_type = a.get("expected_type")
            if expected_type not in RPC_ASSERT_TYPES:
                report.error(
                    path,
                    f"assert[{i}].expected_type must be one of {sorted(RPC_ASSERT_TYPES)}, "
                    f"got {expected_type!r}",
                )


def validate_soroban_body(fx: Fixture, report: Report) -> None:
    data, path = fx.data, fx.path

    if _require(data, "source_account", str, path, report) and not data["source_account"]:
        report.error(
            path, "field 'source_account' must not be empty", line=_field_line(path, "source_account")
        )
    if _require(data, "contract_id", str, path, report) and not data["contract_id"]:
        report.error(
            path, "field 'contract_id' must not be empty", line=_field_line(path, "contract_id")
        )
    if _require(data, "function", str, path, report) and not data["function"]:
        report.error(
            path, "field 'function' must not be empty", line=_field_line(path, "function")
        )
    _require(data, "sequence_number", int, path, report)

    # 'args' is optional but, when present, must match the schema's
    # "args": { "type": "array" } declaration (see
    # schemas/fixture-v1.schema.json's soroban conditional).
    if "args" in data and not isinstance(data["args"], list):
        report.error(
            path,
            f"field 'args', if present, must be an array, "
            f"got {type(data['args']).__name__}",
        )

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
    if kind not in SOROBAN_EXPECT_KINDS:
        report.error(
            path,
            f"expect.kind must be one of {sorted(SOROBAN_EXPECT_KINDS)}, got {kind!r}",
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


def validate_unique_ids(fixtures: list[Fixture], report: Report) -> None:
    seen: dict[str, Path] = {}
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


def validate_directory(root: Path) -> Report:
    report = Report()
    fixtures: list[Fixture] = []
    for path in find_fixture_files(root):
        fx = load_fixture(path, report)
        if fx is not None:
            fixtures.append(fx)

    for fx in fixtures:
        report.register_fixture_id(fx.path, known_fixture_id(fx))

    for fx in fixtures:
        validate_common_fields(fx, report)
        validate_body(fx, report)

    validate_unique_ids(fixtures, report)
    return report


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

    combined = Report()
    total_files = 0
    for root in roots:
        if not root.is_dir():
            combined.error(root, "not a directory")
            continue
        total_files += len(find_fixture_files(root))
        combined.merge(validate_directory(root))

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
        print(f"OK: {total_files} fixture file(s) valid across {len(roots)} root(s)")
        return 0
    print(f"FAILED: {len(combined.errors)} error(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

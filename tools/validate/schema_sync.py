#!/usr/bin/env python3
"""Checks that schemas/fixture-v1.schema.json stays in sync with validate.py.

``schemas/fixture-v1.schema.json`` is this repository's editor-facing mirror
of the fixture contract described in ``CONTRIBUTING.md``. Nothing consumes it
at run time, so without an explicit check a rule change in
``tools/validate/validate.py`` -- for example adding a new XDR type or RPC
method -- could land without a matching schema update, silently leaving the
schema stale for anyone relying on editor-side validation.

This tool closes that gap using only the Python standard library: there is no
third-party JSON Schema engine in this repository's dependency set. It loads
the schema and compares its declared enums and required-field lists against
the corresponding constants in ``tools/validate/validate.py``. Any
disagreement is reported as an error and exits non-zero, so drift fails CI
(via ``tests/test_validate.py``, which runs this check against the shipped
schema).

It does not execute fixtures, contact the network, or validate fixture files
end to end -- that remains ``tools/validate/validate.py``'s job.

Usage:
    python3 tools/validate/schema_sync.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"
SCHEMA_PATH = REPO_ROOT / "schemas" / "fixture-v1.schema.json"


def load_validator(path: Path = VALIDATE_PATH):
    """Import tools/validate/validate.py as a standalone module."""
    spec = importlib.util.spec_from_file_location("validate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["validate"] = module
    spec.loader.exec_module(module)
    return module


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    """Load the fixture JSON Schema as a plain dict."""
    return json.loads(path.read_text(encoding="utf-8"))


def surface_block(schema: dict, surface: str) -> dict | None:
    """Return the ``then`` block of the schema's ``allOf`` entry for ``surface``.

    ``schemas/fixture-v1.schema.json`` selects each surface's body rules with
    an ``if``/``then`` block keyed on the ``surface`` constant. Returns
    ``None`` when no block declares ``surface``.
    """
    for block in schema.get("allOf", []):
        condition = block.get("if", {}).get("properties", {}).get("surface", {})
        if condition.get("const") == surface:
            return block.get("then", {})
    return None


def _get(node, *keys):
    """Walk nested dict ``keys``, returning ``None`` at the first miss."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _enum_values(node) -> list:
    value = node.get("enum") if isinstance(node, dict) else None
    return value if isinstance(value, list) else []


def _required_fields(node) -> list:
    value = node.get("required") if isinstance(node, dict) else None
    return value if isinstance(value, list) else []


def _compare(label: str, expected, actual, errors: list[str]) -> None:
    """Record an error when the schema's ``actual`` set differs from ``expected``."""
    expected_set, actual_set = set(expected), set(actual)
    if expected_set == actual_set:
        return
    details = []
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing:
        details.append(f"schema is missing {missing}")
    if extra:
        details.append(f"schema lists unsupported {extra}")
    errors.append(
        f"{label}: tools/validate/validate.py and schemas/fixture-v1.schema.json "
        f"disagree ({'; '.join(details)})"
    )


def check_sync(schema: dict, validator) -> list[str]:
    """Return one message per schema/validator disagreement (empty when in sync)."""
    errors: list[str] = []
    properties = schema.get("properties", {})

    _compare(
        "top-level required fields",
        validator.COMMON_REQUIRED_FIELDS,
        _required_fields(schema),
        errors,
    )
    _compare(
        "surface enum",
        validator.SURFACES,
        _enum_values(_get(properties, "surface")),
        errors,
    )
    _compare(
        "required_capabilities enum",
        validator.CAPABILITIES,
        _enum_values(_get(properties, "required_capabilities", "items")),
        errors,
    )

    expected_pattern = f"^({validator.SOURCE_REFERENCE_PATTERN})$"
    actual_pattern = _get(properties, "source_reference", "pattern")
    if actual_pattern != expected_pattern:
        errors.append(
            "source_reference pattern: tools/validate/validate.py and "
            "schemas/fixture-v1.schema.json disagree "
            f"(validator expects {expected_pattern!r}, schema has {actual_pattern!r})"
        )

    xdr = surface_block(schema, "xdr")
    if xdr is None:
        errors.append("xdr surface: schema has no allOf/if block for surface 'xdr'")
    else:
        _compare(
            "xdr required fields",
            validator.XDR_REQUIRED_FIELDS,
            _required_fields(xdr),
            errors,
        )
        _compare(
            "xdr type enum",
            validator.XDR_TYPES,
            _enum_values(_get(xdr, "properties", "type")),
            errors,
        )
        _compare(
            "xdr kind enum",
            validator.XDR_KINDS,
            _enum_values(_get(xdr, "properties", "kind")),
            errors,
        )
        for kind, fields in validator.XDR_CONDITIONAL_REQUIRED_FIELDS.items():
            inner_if = xdr.get("if", {})
            inner_then = xdr.get("then", {})
            if _get(inner_if, "properties", "kind", "const") == kind:
                _compare(
                    f"xdr kind={kind} required fields",
                    fields,
                    _required_fields(inner_then),
                    errors,
                )

    rpc = surface_block(schema, "rpc")
    if rpc is None:
        errors.append("rpc surface: schema has no allOf/if block for surface 'rpc'")
    else:
        _compare(
            "rpc required fields",
            validator.RPC_REQUIRED_FIELDS,
            _required_fields(rpc),
            errors,
        )
        _compare(
            "rpc method enum",
            validator.RPC_METHODS,
            _enum_values(_get(rpc, "properties", "method")),
            errors,
        )
        _compare(
            "rpc assert kind enum",
            validator.RPC_ASSERT_KINDS,
            _enum_values(_get(rpc, "properties", "assert", "items", "properties", "kind")),
            errors,
        )
        _compare(
            "rpc assert expected_type enum",
            validator.RPC_ASSERT_TYPES,
            _enum_values(
                _get(rpc, "properties", "assert", "items", "properties", "expected_type")
            ),
            errors,
        )

    soroban = surface_block(schema, "soroban")
    if soroban is None:
        errors.append("soroban surface: schema has no allOf/if block for surface 'soroban'")
    else:
        _compare(
            "soroban required fields",
            validator.SOROBAN_REQUIRED_FIELDS,
            _required_fields(soroban),
            errors,
        )
        _compare(
            "soroban expect kind enum",
            validator.SOROBAN_EXPECT_KINDS,
            _enum_values(_get(soroban, "properties", "expect", "properties", "kind")),
            errors,
        )

    return errors


def main(argv: list[str]) -> int:
    if argv:
        print(f"usage: {Path(__file__).name}", file=sys.stderr)
        return 2
    schema = load_schema()
    validator = load_validator()
    errors = check_sync(schema, validator)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"FAILED: {len(errors)} schema/validator drift error(s)", file=sys.stderr)
        return 1
    print("OK: schemas/fixture-v1.schema.json matches tools/validate/validate.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

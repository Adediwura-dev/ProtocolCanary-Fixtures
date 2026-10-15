#!/usr/bin/env python3
"""A small JSON Schema validator using only the standard library.

This repository's CI runs on a bare interpreter with no third-party installs,
so the schemas under ``schemas/`` cannot be checked with the ``jsonschema``
package. This module implements the subset of JSON Schema those schemas use,
which is enough to validate a document against them for real instead of
mirroring them with hand-written predicates.

It is deliberately strict about what it does not know. A schema that uses a
keyword outside ``SUPPORTED_KEYWORDS`` raises :class:`SchemaError` instead of
being skipped, because silently ignoring a keyword would let an invalid
document pass. To use a new keyword, implement it here and add a test.

Supported: ``type``, ``enum``, ``const``, ``required``, ``properties``,
``patternProperties``, ``additionalProperties``, ``items``, ``minItems``,
``maxItems``, ``uniqueItems``, ``minLength``, ``maxLength``, ``pattern``,
``minimum``, ``maximum``, ``minProperties`` and local ``$ref`` pointers.
Annotation keywords (``$schema``, ``$id``, ``$comment``, ``title``,
``description``, ``examples``, ``default``) are ignored.

Usage:
    from jsonschema_lite import validate
    errors = validate(document, schema)   # [] when the document conforms
"""
from __future__ import annotations

import re
from typing import Any

# ``$defs``/``definitions`` only hold reusable sub-schemas; they are validated
# when something ``$ref``s them, so they are not applied on their own.
ANNOTATION_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "examples",
        "default",
        "$defs",
        "definitions",
    }
)
SUPPORTED_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "patternProperties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minProperties",
        "$ref",
    }
)
_JSON_TYPES = ("string", "integer", "number", "boolean", "object", "array", "null")


class SchemaError(ValueError):
    """The schema itself uses something this validator does not implement."""


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "null":
        return value is None
    raise SchemaError(f"unknown JSON type {name!r} in schema")


def _type_name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__


def _resolve(ref: str, root: dict) -> Any:
    if not ref.startswith("#"):
        raise SchemaError(f"only local $ref pointers are supported, got {ref!r}")
    node: Any = root
    for part in [p for p in ref[1:].split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise SchemaError(f"$ref {ref!r} does not resolve")
        node = node[part]
    return node


def _where(path: str) -> str:
    return path or "$"


def _check(value: Any, schema: Any, root: dict, path: str, errors: list[str]) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{_where(path)}: no value is allowed here")
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"a schema must be an object or a boolean, got {schema!r}")

    unknown = set(schema) - SUPPORTED_KEYWORDS - ANNOTATION_KEYWORDS
    if unknown:
        raise SchemaError(
            f"unsupported schema keyword(s) {sorted(unknown)} at {_where(path)}; "
            "implement them in tools/validate/jsonschema_lite.py before using them"
        )

    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, path, errors)

    if "type" in schema:
        wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, name) for name in wanted):
            errors.append(
                f"{_where(path)}: expected {' or '.join(wanted)}, got {_type_name(value)}"
            )
            return  # keywords below assume the right type

    if "const" in schema and not (
        value == schema["const"] and _type_name(value) == _type_name(schema["const"])
    ):
        errors.append(f"{_where(path)}: must equal {schema['const']!r}, got {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{_where(path)}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(
                f"{_where(path)}: {value!r} is shorter than {schema['minLength']} character(s)"
            )
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(
                f"{_where(path)}: is {len(value)} characters, more than {schema['maxLength']}"
            )
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(
                f"{_where(path)}: {value!r} does not match pattern {schema['pattern']!r}"
            )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{_where(path)}: {value!r} is less than {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{_where(path)}: {value!r} is greater than {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(
                f"{_where(path)}: has {len(value)} item(s), fewer than {schema['minItems']}"
            )
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(
                f"{_where(path)}: has {len(value)} item(s), more than {schema['maxItems']}"
            )
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in value:
                if item in seen:
                    errors.append(f"{_where(path)}: items must be unique, {item!r} repeats")
                    break
                seen.append(item)
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{index}]", errors)

    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(
                f"{_where(path)}: has {len(value)} propert(ies), fewer than "
                f"{schema['minProperties']}"
            )
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{_where(path)}: missing required property {name!r}")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        for name, member in value.items():
            child = f"{path}.{name}" if path else f"$.{name}"
            matched = False
            if name in properties:
                matched = True
                _check(member, properties[name], root, child, errors)
            for pattern, sub in patterns.items():
                if re.search(pattern, name):
                    matched = True
                    _check(member, sub, root, child, errors)
            if not matched and "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    errors.append(f"{_where(path)}: unexpected property {name!r}")
                elif extra is not True:
                    _check(member, extra, root, child, errors)


def validate(document: Any, schema: dict) -> list[str]:
    """Return one message per violation; an empty list means ``document`` conforms."""
    errors: list[str] = []
    _check(document, schema, schema, "", errors)
    return errors

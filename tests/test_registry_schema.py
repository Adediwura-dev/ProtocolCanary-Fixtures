"""Tests for schemas/registry-v1.schema.json (F-002, CF-02 section 3).

Stdlib only: the CI `validate` job runs `unittest discover` on a bare
interpreter with no third-party installs, so this file mirrors the schema
with plain predicates instead of importing a validator.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO_ROOT / "schemas" / "registry-v1.schema.json").read_text())

_SHA = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SURFACES = ("xdr", "rpc", "soroban")
_TOP_FIELDS = set(SCHEMA["properties"])
_ENTRY_FIELDS = set(SCHEMA["properties"]["fixtures"]["items"]["properties"])
_PAYLOAD_FIELDS = {"path", "sha256"}


def check_manifest(record: dict) -> None:
    """Mirror of registry-v1.schema.json; raises AssertionError on violation."""
    assert set(record) <= _TOP_FIELDS, f"undeclared: {set(record) - _TOP_FIELDS}"
    assert record["registryVersion"] == 1
    assert isinstance(record["protocol"], int) and record["protocol"] >= 0
    assert isinstance(record["fixtures"], list) and record["fixtures"]
    for entry in record["fixtures"]:
        assert set(entry) <= _ENTRY_FIELDS, f"undeclared: {set(entry) - _ENTRY_FIELDS}"
        assert isinstance(entry.get("id"), str) and entry["id"]
        assert isinstance(entry.get("path"), str) and entry["path"].endswith(".toml")
        assert _SHA.match(entry.get("sha256", "")), "sha256 must be 64 lowercase hex"
        assert entry.get("surface") in _SURFACES
        assert isinstance(entry.get("category"), str) and entry["category"]
        assert isinstance(entry.get("description"), str) and entry["description"]
        assert isinstance(entry.get("payloads"), list)
        for payload in entry["payloads"]:
            assert set(payload) <= _PAYLOAD_FIELDS
            assert isinstance(payload.get("path"), str) and payload["path"]
            assert _SHA.match(payload.get("sha256", ""))
        if "sourceReference" in entry:
            assert isinstance(entry["sourceReference"], str) and entry["sourceReference"]
        if "requiredCapabilities" in entry:
            assert isinstance(entry["requiredCapabilities"], list)
            assert all(
                isinstance(c, str) and c for c in entry["requiredCapabilities"]
            )
    assert _DIGEST.match(record.get("packDigest", "")), "packDigest must be sha256:<64hex>"


SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

MANIFEST = {
    "registryVersion": 1,
    "protocol": 28,
    "fixtures": [
        {
            "id": "p28-xdr-cap85-external-ref-roundtrip",
            "path": "protocol-28/xdr/cap-0085/p28-xdr-cap85-external-ref-roundtrip.toml",
            "sha256": SHA,
            "surface": "xdr",
            "category": "cap-0085",
            "description": "CAP-0085 round-trip fixture",
            "payloads": [{"path": "payloads/a.bin", "sha256": SHA}],
            "sourceReference": "https://github.com/stellar/stellar-protocol/blob/master/core/cap-0085.md",
            "requiredCapabilities": ["stellar-sdk-dependency"],
        }
    ],
    "packDigest": f"sha256:{SHA}",
}


class RegistrySchemaTest(unittest.TestCase):
    def test_conforming_manifest_validates(self) -> None:
        check_manifest(MANIFEST)

    def test_optional_fields_may_be_omitted(self) -> None:
        record = copy.deepcopy(MANIFEST)
        del record["fixtures"][0]["sourceReference"]
        del record["fixtures"][0]["requiredCapabilities"]
        check_manifest(record)

    def test_bad_digest_rejected(self) -> None:
        record = copy.deepcopy(MANIFEST)
        record["packDigest"] = SHA
        with self.assertRaises(Exception):
            check_manifest(record)

    def test_missing_required_field_rejected(self) -> None:
        record = copy.deepcopy(MANIFEST)
        del record["fixtures"]
        with self.assertRaises(Exception):
            check_manifest(record)

    def test_unsupported_version_rejected(self) -> None:
        record = copy.deepcopy(MANIFEST)
        record["registryVersion"] = 2
        with self.assertRaises(Exception):
            check_manifest(record)

    def test_undeclared_property_rejected(self) -> None:
        record = copy.deepcopy(MANIFEST)
        record["fixtures"][0]["bogus"] = 1
        with self.assertRaises(Exception):
            check_manifest(record)


if __name__ == "__main__":
    unittest.main()

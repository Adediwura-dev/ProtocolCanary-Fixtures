"""Tests for schemas/verification-v1.schema.json (F-015, CF-04 section 3).

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
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO_ROOT / "schemas" / "verification-v1.schema.json").read_text())

_SHA = re.compile(r"^[0-9a-f]{64}$")
_HOST = re.compile(r"^[^/:?#@]+$")
_METHODS = ("structural", "source-checked", "live-read-only", "live-simulation")
_LIVE = ("live-read-only", "live-simulation")
_OUTCOMES = ("matched", "mismatched")
_TOP_FIELDS = set(SCHEMA["properties"])


def check_record(record: dict) -> None:
    """Mirror of verification-v1.schema.json; raises AssertionError on violation."""
    assert set(record) <= _TOP_FIELDS, f"undeclared: {set(record) - _TOP_FIELDS}"
    assert record["verificationVersion"] == 1
    assert isinstance(record["fixtureId"], str) and record["fixtureId"]
    assert _SHA.match(record["fixtureSha256"]), "fixtureSha256 must be 64 lowercase hex"
    assert record["method"] in _METHODS
    datetime.strptime(record["checkedAt"], "%Y-%m-%dT%H:%M:%SZ")  # RFC 3339 UTC
    verifier = record["verifier"]
    assert isinstance(verifier.get("name"), str) and verifier["name"]
    assert isinstance(verifier.get("version"), str) and verifier["version"]
    assert set(verifier) <= {"name", "version"}
    assert record["outcome"] in _OUTCOMES
    network = record.get("network")
    if network is not None:
        assert set(network) <= {"name", "passphrase", "observedProtocol"}
        assert isinstance(network.get("name"), str) and network["name"]
        assert isinstance(network.get("passphrase"), str) and network["passphrase"]
        assert isinstance(network.get("observedProtocol"), int)
        assert network["observedProtocol"] >= 0
    host = record.get("endpointHost")
    if host is not None:
        assert isinstance(host, str) and _HOST.match(host), "bare hostname only"
    ref = record.get("evidenceRef")
    if ref is not None:
        assert isinstance(ref, str) and ref
    if record["method"] in _LIVE:
        assert isinstance(network, dict), "live methods require network"
        assert isinstance(host, str), "live methods require endpointHost"


LIVE_RECORD = {
    "verificationVersion": 1,
    "fixtureId": "p29-rpc-obs-latest-ledger",
    "fixtureSha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "method": "live-read-only",
    "checkedAt": "2026-10-10T04:00:00Z",
    "verifier": {"name": "protocol-canary", "version": "0.3.0"},
    "outcome": "matched",
    "network": {
        "name": "testnet",
        "passphrase": "Test SDF Network ; September 2015",
        "observedProtocol": 29,
    },
    "endpointHost": "horizon-testnet.stellar.org",
    "evidenceRef": "protocol-29/rpc/p29-rpc-latest-ledger.toml",
}

OFFLINE_RECORD = {
    "verificationVersion": 1,
    "fixtureId": "p28-xdr-cap85-external-ref-roundtrip",
    "fixtureSha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "method": "structural",
    "checkedAt": "2026-10-10T04:00:00Z",
    "verifier": {"name": "protocol-canary", "version": "0.3.0"},
    "outcome": "matched",
    "network": None,
}


class VerificationSchemaTest(unittest.TestCase):
    def test_live_record_validates(self) -> None:
        check_record(LIVE_RECORD)

    def test_offline_record_with_null_network_validates(self) -> None:
        check_record(OFFLINE_RECORD)

    def test_offline_record_with_omitted_network_validates(self) -> None:
        record = copy.deepcopy(OFFLINE_RECORD)
        del record["network"]
        check_record(record)

    def test_live_missing_network_fails(self) -> None:
        record = copy.deepcopy(LIVE_RECORD)
        del record["network"]
        with self.assertRaises(Exception):
            check_record(record)

    def test_live_missing_endpoint_host_fails(self) -> None:
        record = copy.deepcopy(LIVE_RECORD)
        del record["endpointHost"]
        with self.assertRaises(Exception):
            check_record(record)

    def test_undeclared_property_rejected(self) -> None:
        record = copy.deepcopy(LIVE_RECORD)
        record["surprise"] = 1
        with self.assertRaises(Exception):
            check_record(record)

    def test_bad_sha_and_version_rejected(self) -> None:
        record = copy.deepcopy(LIVE_RECORD)
        record["fixtureSha256"] = "XYZ"
        with self.assertRaises(Exception):
            check_record(record)
        record = copy.deepcopy(LIVE_RECORD)
        record["verificationVersion"] = 2
        with self.assertRaises(Exception):
            check_record(record)


if __name__ == "__main__":
    unittest.main()

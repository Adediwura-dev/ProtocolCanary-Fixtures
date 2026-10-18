"""Tests for the Protocol 29 Testnet read-only evidence corpus (F-034, #291).

``protocol-29/evidence/testnet/`` holds raw captures of the three read-only
RPC methods CF-04 section 8 names, taken from the public Testnet endpoint.
Per CF-04 section 7 an evidence file is data: the JSON-RPC request and
response from a public endpoint and nothing else — no tokens, no private
endpoint URLs, no secret headers, never a write call. The corpus must exist
with its references before any Protocol 29 fixture is authored
(CF-04-H, D-09 option C).

The capture envelope is this repository's (the issue specifies captures,
timestamps and host validation but no envelope): ``capturedAt``,
``endpointHost``, ``request``, ``response``, closed to anything else. The
instant and host rules are the ones ``tools/validate/validate.py`` already
uses for CF-04 records, so evidence cannot drift from the record rules.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "protocol-29" / "evidence" / "testnet"

VALIDATE_PATH = REPO_ROOT / "tools" / "validate" / "validate.py"
_spec = importlib.util.spec_from_file_location("validate_for_protocol29_evidence", VALIDATE_PATH)
validate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate_for_protocol29_evidence"] = validate
_spec.loader.exec_module(validate)

# The capture envelope: closed, so a stray field (a token, a URL with a
# query string, an copied header) cannot ride along unnoticed.
CAPTURE_FIELDS = ("capturedAt", "endpointHost", "request", "response")
# CF-04 section 8: the read-only methods this corpus records. No write call
# (submitTransaction and friends) may ever appear in an evidence file.
READ_ONLY_METHODS = ("getNetwork", "getLatestLedger", "getVersionInfo")
# The only endpoint these recordings may come from: public Testnet, no
# credentials, no private host (CF-04 section 7).
ALLOWED_HOSTS = ("soroban-testnet.stellar.org",)


def capture_problems(capture: object) -> list[str]:
    """Problems in one evidence capture (CF-04 sections 3, 7 and 8).

    Empty list means the capture conforms. The timestamp and host syntax
    checks reuse ``validate``'s own CF-04 helpers so a capture and a
    verification record can never disagree about what an RFC 3339 UTC
    instant or a bare host name is.
    """
    if not isinstance(capture, dict):
        return ["an evidence capture must be a JSON object"]

    problems: list[str] = []
    for name in sorted(set(capture) - set(CAPTURE_FIELDS)):
        problems.append(
            f"unknown field {name!r}; an evidence capture carries only "
            + ", ".join(CAPTURE_FIELDS)
        )
    for name in CAPTURE_FIELDS:
        if name not in capture:
            problems.append(f"missing required field {name!r}")

    if "capturedAt" in capture:
        # validate's rule is written for a record's checkedAt; the capture
        # instant obeys the same rule under its own field name.
        instant_problem = validate._check_instant(capture["capturedAt"])
        if instant_problem:
            problems.append(instant_problem.replace("checkedAt", "capturedAt", 1))

    if "endpointHost" in capture:
        host = capture["endpointHost"]
        if not isinstance(host, str) or not validate._EVIDENCE_HOST_RE.fullmatch(host):
            problems.append(
                "endpointHost must be a host name only: no scheme, port, path, "
                "query, credentials or whitespace"
            )
        elif host not in ALLOWED_HOSTS:
            problems.append(
                f"endpointHost {host!r} is not an approved public read-only endpoint; "
                f"captures may only come from {list(ALLOWED_HOSTS)}"
            )

    request = capture.get("request")
    if "request" in capture:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            problems.append("request must be a JSON-RPC 2.0 object")
        else:
            method = request.get("method")
            if method not in READ_ONLY_METHODS:
                problems.append(
                    f"request.method {method!r} is not one of the read-only methods "
                    f"{list(READ_ONLY_METHODS)}; an evidence capture must never "
                    "contain a write call"
                )

    response = capture.get("response")
    if "response" in capture:
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
            problems.append("response must be a JSON-RPC 2.0 object")
        elif "result" not in response:
            problems.append("response carries no result; a failed call is not evidence")
        elif isinstance(request, dict) and response.get("id") != request.get("id"):
            problems.append("response id does not match the request id")
    return problems


class Protocol29EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sorted(EVIDENCE_DIR.glob("*.json"))
        cls.captures = {
            path.name: json.loads(path.read_text(encoding="utf-8")) for path in cls.paths
        }

    def test_corpus_contains_the_three_read_only_captures(self) -> None:
        self.assertEqual(
            sorted(self.captures),
            ["get-latest-ledger.json", "get-network.json", "get-version-info.json"],
        )
        methods = {c["request"]["method"] for c in self.captures.values()}
        self.assertEqual(methods, set(READ_ONLY_METHODS))

    def test_every_capture_conforms_to_the_evidence_rules(self) -> None:
        for name, capture in self.captures.items():
            with self.subTest(capture=name):
                self.assertEqual(capture_problems(capture), [])

    def test_every_capture_observes_protocol_29(self) -> None:
        # Why the corpus exists: the endpoint reported protocol 29 at the
        # captured instant (copied from the real response, never assumed).
        for name, capture in self.captures.items():
            with self.subTest(capture=name):
                self.assertEqual(capture["response"]["result"].get("protocolVersion"), 29)

    def test_capture_files_carry_no_credentials(self) -> None:
        # CF-04 section 7: request and response data only — no tokens, no
        # credential-bearing URLs, no secret headers.
        forbidden = ("authorization", "bearer", "api_key", "apikey", "secret", "password", "seed", "@")
        for path in self.paths:
            raw = path.read_text(encoding="utf-8").lower()
            with self.subTest(capture=path.name):
                for token in forbidden:
                    self.assertNotIn(token, raw, f"{path.name} contains {token!r}")

    def test_malformed_timestamps_are_rejected(self) -> None:
        good = self.captures["get-network.json"]
        for bad in (
            "2026-10-18 02:59:14Z",
            "2026-10-18T02:59:14+02:00",
            "2026-99-99T99:99:99Z",
            1792292352,
        ):
            with self.subTest(capturedAt=bad):
                problems = capture_problems(dict(good, capturedAt=bad))
                self.assertTrue(problems, "a malformed capturedAt was accepted")
                self.assertTrue(
                    any("capturedAt" in p for p in problems), problems
                )

    def test_disallowed_hosts_are_rejected(self) -> None:
        good = self.captures["get-network.json"]
        for bad in (
            "mainnet.sorobanrpc.com",
            "https://soroban-testnet.stellar.org",
            "soroban-testnet.stellar.org/rpc",
            "rpc-user@soroban-testnet.stellar.org",
        ):
            with self.subTest(endpointHost=bad):
                problems = capture_problems(dict(good, endpointHost=bad))
                self.assertTrue(problems, f"endpointHost {bad!r} was accepted")

    def test_write_calls_are_rejected(self) -> None:
        good = self.captures["get-network.json"]
        mutated = dict(good, request={"jsonrpc": "2.0", "id": 1, "method": "submitTransaction"})
        problems = capture_problems(mutated)
        self.assertTrue(problems)
        self.assertTrue(any("submitTransaction" in p for p in problems), problems)

    def test_unknown_envelope_fields_are_rejected(self) -> None:
        good = self.captures["get-network.json"]
        problems = capture_problems(dict(good, authorization="Bearer nope"))
        self.assertTrue(any("unknown field 'authorization'" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
"""Tests for tools/validate/check_source_links.py.

All tests are completely offline: tests that verify HTTP request handling,
status codes, timeouts, redirect loops, and credential stripping spin up a local
HTTP server on 127.0.0.1 and shut it down cleanly.
"""
from __future__ import annotations

import io
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tools.validate.check_source_links import (
    CheckResult,
    clean_comment_url,
    check_link,
    extract_links_from_file,
    generate_markdown_table,
    main,
    strip_credentials,
)


class MockServerHandler(BaseHTTPRequestHandler):
    """Local HTTP handler for testing link checking scenarios."""

    def do_GET(self) -> None:
        path = self.path

        if path == "/200":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        elif path == "/redirect-to-200":
            self.send_response(302)
            self.send_header("Location", "/200")
            self.end_headers()
        elif path == "/redirect-chain-1":
            self.send_response(301)
            self.send_header("Location", "/redirect-chain-2")
            self.end_headers()
        elif path == "/redirect-chain-2":
            self.send_response(301)
            self.send_header("Location", "/redirect-chain-3")
            self.end_headers()
        elif path == "/redirect-chain-3":
            self.send_response(301)
            self.send_header("Location", "/200")
            self.end_headers()
        elif path == "/loop-a":
            self.send_response(302)
            self.send_header("Location", "/loop-b")
            self.end_headers()
        elif path == "/loop-b":
            self.send_response(302)
            self.send_header("Location", "/loop-a")
            self.end_headers()
        elif path == "/redirect-to-ftp":
            self.send_response(302)
            self.send_header("Location", "ftp://example.com/file")
            self.end_headers()
        elif path == "/429":
            self.send_response(429)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Too Many Requests")
        elif path == "/404":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
        elif path == "/500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Server Error")
        elif path == "/hang":
            time.sleep(0.4)
            self.send_response(200)
            self.end_headers()
        elif path == "/auth-check":
            # Verify no Authorization header or credentials arrived
            auth = self.headers.get("Authorization")
            if auth:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Credentials leaked in header")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"No credentials sent")
        elif path == "/rpc-endpoint":
            # Rejects GET with 405 Method Not Allowed
            self.send_response(405)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/rpc-endpoint":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"result": "ok"}')
        else:
            self.send_response(405)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Silence HTTP server logs during test execution
        pass


class TestCheckSourceLinks(unittest.TestCase):
    server: ThreadingHTTPServer
    server_thread: threading.Thread
    base_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockServerHandler)
        port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def test_strip_credentials(self) -> None:
        self.assertEqual(
            strip_credentials("https://user:pass@example.com/path"),
            "https://example.com/path",
        )
        self.assertEqual(
            strip_credentials("http://alice:secret@127.0.0.1:8080/test?q=1#frag"),
            "http://127.0.0.1:8080/test?q=1#frag",
        )
        self.assertEqual(
            strip_credentials("https://user@example.com/foo"),
            "https://example.com/foo",
        )
        self.assertEqual(
            strip_credentials("ftp://admin:secret@ftp.example.com/file"),
            "ftp://ftp.example.com/file",
        )
        self.assertEqual(
            strip_credentials("https://example.com/clean"),
            "https://example.com/clean",
        )

    def test_clean_comment_url(self) -> None:
        self.assertEqual(clean_comment_url("https://example.com/foo."), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo:"), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo,"), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo;"), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo!"), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo?"), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo'"), "https://example.com/foo")
        self.assertEqual(clean_comment_url('https://example.com/foo"'), "https://example.com/foo")
        self.assertEqual(clean_comment_url("https://example.com/foo)"), "https://example.com/foo")
        # Retain closing paren when opening paren is part of the URL
        self.assertEqual(
            clean_comment_url("https://example.com/wiki/Foo_(bar)"),
            "https://example.com/wiki/Foo_(bar)",
        )

    def test_extract_links_from_file(self) -> None:
        fixture_content = """# Header comment 1 with https://example.com/comment1.
# Header comment 2 with https://user:pass@example.com/comment2:
# (Another comment with https://example.com/comment3)
# Duplicate mention of https://example.com/comment1 in header.

id = "p28-test-fixture"
protocol = 28
surface = "xdr"
source_reference = "https://example.com/source-ref"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fp:
            fp.write(fixture_content)
            temp_path = Path(fp.name)

        try:
            links = extract_links_from_file(temp_path)
            self.assertEqual(
                links,
                [
                    "https://example.com/comment1",
                    "https://example.com/comment2",
                    "https://example.com/comment3",
                    "https://example.com/source-ref",
                ],
            )
        finally:
            temp_path.unlink()

    def test_extract_links_skips_cap_identifiers(self) -> None:
        fixture_content = """# Header with no URLs.
id = "p28-test-cap"
protocol = 28
surface = "xdr"
source_reference = "CAP-0083"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fp:
            fp.write(fixture_content)
            temp_path = Path(fp.name)

        try:
            links = extract_links_from_file(temp_path)
            self.assertEqual(links, [])
        finally:
            temp_path.unlink()

    def test_extract_links_includes_non_http_source_reference(self) -> None:
        fixture_content = """# Header with no URLs.
id = "p28-test-ftp"
source_reference = "ftp://example.com/spec.txt"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fp:
            fp.write(fixture_content)
            temp_path = Path(fp.name)

        try:
            links = extract_links_from_file(temp_path)
            self.assertEqual(links, ["ftp://example.com/spec.txt"])
        finally:
            temp_path.unlink()

    def test_check_link_200_ok(self) -> None:
        res = check_link(f"{self.base_url}/200")
        self.assertEqual(res.status, "OK")
        self.assertEqual(res.formatted(), "OK")

    def test_check_link_redirect_to_200(self) -> None:
        res = check_link(f"{self.base_url}/redirect-to-200")
        self.assertEqual(res.status, "OK")

    def test_check_link_429_inconclusive(self) -> None:
        res = check_link(f"{self.base_url}/429")
        self.assertEqual(res.status, "Inconclusive")
        self.assertIn("429", res.formatted())

    def test_check_link_404_failed(self) -> None:
        res = check_link(f"{self.base_url}/404")
        self.assertEqual(res.status, "Failed")
        self.assertIn("404", res.formatted())

    def test_check_link_500_failed(self) -> None:
        res = check_link(f"{self.base_url}/500")
        self.assertEqual(res.status, "Failed")
        self.assertIn("500", res.formatted())

    def test_check_link_redirect_limit(self) -> None:
        # Chain has 3 redirects; limit of 2 should fail
        res = check_link(f"{self.base_url}/redirect-chain-1", max_redirects=2)
        self.assertEqual(res.status, "Failed")
        self.assertIn("too many redirects", res.formatted())

        # Limit of 4 should succeed
        res_ok = check_link(f"{self.base_url}/redirect-chain-1", max_redirects=4)
        self.assertEqual(res_ok.status, "OK")

    def test_check_link_redirect_loop_detection(self) -> None:
        res = check_link(f"{self.base_url}/loop-a")
        self.assertEqual(res.status, "Failed")
        self.assertIn("redirect loop", res.formatted())

    def test_check_link_non_http_scheme(self) -> None:
        res_ftp = check_link("ftp://ftp.example.com/file")
        self.assertEqual(res_ftp.status, "Failed")
        self.assertIn("non-HTTP scheme", res_ftp.formatted())

        res_file = check_link("file:///etc/passwd")
        self.assertEqual(res_file.status, "Failed")
        self.assertIn("non-HTTP scheme", res_file.formatted())

    def test_check_link_redirect_to_non_http_scheme(self) -> None:
        res = check_link(f"{self.base_url}/redirect-to-ftp")
        self.assertEqual(res.status, "Failed")
        self.assertIn("redirected to non-HTTP scheme", res.formatted())

    def test_check_link_credential_stripping_and_no_leak(self) -> None:
        url_with_creds = f"http://secret_user:secret_pass@127.0.0.1:{self.server.server_address[1]}/auth-check"
        res = check_link(url_with_creds)
        # Server verifies no credentials arrived and returns 200
        self.assertEqual(res.status, "OK")
        # Ensure credentials do not appear in formatted result
        self.assertNotIn("secret_user", res.formatted())
        self.assertNotIn("secret_pass", res.formatted())

    def test_check_link_timeout(self) -> None:
        res = check_link(f"{self.base_url}/hang", timeout=0.1)
        self.assertEqual(res.status, "Failed")
        self.assertIn("timed out", res.formatted().lower())

    def test_check_link_rpc_post_fallback(self) -> None:
        res = check_link(f"{self.base_url}/rpc-endpoint")
        self.assertEqual(res.status, "OK")

    def test_generate_markdown_table(self) -> None:
        rows = [
            ("https://example.com/a", "protocol-28/a.toml", CheckResult("OK")),
            ("https://example.com/b", "protocol-28/b.toml", CheckResult("Inconclusive", "HTTP 429")),
            ("https://example.com/c", "protocol-28/c.toml", CheckResult("Failed", "HTTP 404")),
        ]
        table = generate_markdown_table(rows)
        self.assertIn("| URL | Fixture File | Result |", table)
        self.assertIn("| https://example.com/a | protocol-28/a.toml | OK |", table)
        self.assertIn("| https://example.com/b | protocol-28/b.toml | Inconclusive (HTTP 429) |", table)
        self.assertIn("| https://example.com/c | protocol-28/c.toml | Failed (HTTP 404) |", table)

    def test_main_cli_passes_with_ok_and_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            f1 = Path(tmp_dir) / "f1.toml"
            f1.write_text(f'source_reference = "{self.base_url}/200"\n')

            f2 = Path(tmp_dir) / "f2.toml"
            f2.write_text(f'source_reference = "{self.base_url}/429"\n')

            summary_file = Path(tmp_dir) / "summary.md"

            stdout_buf = io.StringIO()
            with patch("sys.stdout", stdout_buf):
                exit_code = main([tmp_dir, "--summary-file", str(summary_file)])

            # 429 is inconclusive, does not fail the job!
            self.assertEqual(exit_code, 0)
            output = stdout_buf.getvalue()
            self.assertIn("| URL | Fixture File | Result |", output)
            self.assertIn("OK", output)
            self.assertIn("Inconclusive", output)

            # Summary file was written
            self.assertTrue(summary_file.exists())
            summary_content = summary_file.read_text()
            self.assertIn("| URL | Fixture File | Result |", summary_content)

    def test_main_cli_fails_with_broken_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            f1 = Path(tmp_dir) / "f1.toml"
            f1.write_text(f'source_reference = "{self.base_url}/404"\n')

            stdout_buf = io.StringIO()
            with patch("sys.stdout", stdout_buf):
                exit_code = main([tmp_dir])

            self.assertEqual(exit_code, 1)
            output = stdout_buf.getvalue()
            self.assertIn("Failed (HTTP 404)", output)


if __name__ == "__main__":
    unittest.main()

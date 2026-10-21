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
import urllib.error
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_extract_links_from_unreadable_file(self) -> None:
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            self.assertEqual(extract_links_from_file(Path("unreadable.toml")), [])

    def test_extract_links_keeps_header_urls_when_toml_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "broken.toml"
            path.write_text(
                "# https://example.com/header\nnot valid [[[ toml\n",
                encoding="utf-8",
            )
            self.assertEqual(
                extract_links_from_file(path), ["https://example.com/header"]
            )

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

    def test_check_link_post_fallback_failures(self) -> None:
        url = f"{self.base_url}/rpc-endpoint"
        cases = (
            (
                urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None),
                "Inconclusive",
                "429",
            ),
            (urllib.error.HTTPError(url, 503, "Unavailable", {}, None), "Failed", "503"),
            (RuntimeError("connection reset"), "Failed", "POST fallback failed"),
        )
        for post_error, expected_status, expected_detail in cases:
            with self.subTest(error=type(post_error).__name__):
                opener = Mock()
                opener.open.side_effect = [
                    urllib.error.HTTPError(url, 405, "Method Not Allowed", {}, None),
                    post_error,
                ]
                result = check_link(url, opener=opener)
                self.assertEqual(result.status, expected_status)
                self.assertIn(expected_detail, result.formatted())
                self.assertEqual(opener.open.call_count, 2)

    def test_check_link_network_and_unexpected_errors(self) -> None:
        cases = (
            (urllib.error.URLError("connection refused"), "network error"),
            (RuntimeError("unexpected"), "RuntimeError"),
        )
        for error, expected_detail in cases:
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                result = check_link("https://example.com/", opener=opener)
                self.assertEqual(result.status, "Failed")
                self.assertIn(expected_detail, result.formatted())

    def test_check_link_rejects_invalid_and_hostless_urls(self) -> None:
        invalid = check_link("http://[invalid")
        self.assertEqual(invalid.status, "Failed")
        self.assertIn("invalid URL", invalid.formatted())

        hostless = check_link("http:///path")
        self.assertEqual(hostless.status, "Failed")
        self.assertIn("missing host", hostless.formatted())

    def test_check_link_handles_final_status_without_http_error(self) -> None:
        for status, expected_status in ((429, "Inconclusive"), (503, "Failed")):
            with self.subTest(status=status):
                response = Mock(status=status, headers={})
                opener = Mock()
                opener.open.return_value = response
                result = check_link("https://example.com/", opener=opener)
                self.assertEqual(result.status, expected_status)
                self.assertIn(str(status), result.formatted())
                response.close.assert_called_once_with()

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

    def test_main_rejects_invalid_explicit_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            non_toml = root / "not-a-fixture.txt"
            non_toml.write_text("content", encoding="utf-8")
            missing = root / "missing"

            for path in (missing, non_toml):
                with self.subTest(path=path):
                    stderr_buf = io.StringIO()
                    with patch("sys.stderr", stderr_buf):
                        exit_code = main([str(path)])
                    self.assertEqual(exit_code, 2)
                    self.assertIn(str(path), stderr_buf.getvalue())

    def test_main_rejects_explicit_roots_without_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            empty = Path(tmp_dir) / "empty"
            empty.mkdir()
            stderr_buf = io.StringIO()
            with patch("sys.stderr", stderr_buf):
                exit_code = main([str(empty)])
            self.assertEqual(exit_code, 2)
            self.assertIn(str(empty), stderr_buf.getvalue())

    def test_main_accepts_a_toml_file_as_an_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            fixture = Path(tmp_dir) / "fixture.toml"
            fixture.write_text('source_reference = "CAP-0083"\n', encoding="utf-8")
            stdout_buf = io.StringIO()
            with patch("sys.stdout", stdout_buf):
                exit_code = main([str(fixture)])
            self.assertEqual(exit_code, 0)
            self.assertIn("0 checked across 1 fixture files", stdout_buf.getvalue())

    def test_main_uses_default_roots_without_rejecting_empty_packs(self) -> None:
        default_root = Path("protocol-27")
        with patch.object(Path, "glob", return_value=[default_root]), patch(
            "tools.validate.check_source_links.find_fixture_files", return_value=[]
        ) as find_files:
            stdout_buf = io.StringIO()
            with patch("sys.stdout", stdout_buf):
                exit_code = main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("0 checked across 0 fixture files", stdout_buf.getvalue())
        find_files.assert_called_once_with(default_root)

    def test_main_warns_when_summary_file_cannot_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "fixture.toml").write_text(
                'source_reference = "CAP-0083"\n', encoding="utf-8"
            )
            stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main([tmp_dir, "--summary-file", tmp_dir])
            self.assertEqual(exit_code, 0)
            self.assertIn("Warning: could not write to summary file", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()

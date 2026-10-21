#!/usr/bin/env python3
"""Check fixture source references and header comment links on a schedule.

Scans fixture TOML files for:
  - `source_reference` field values (excluding CAP-XXXX identifiers)
  - `https://` URLs in header comments

Checks each URL with bounded redirects, timeout, 429 rate-limit handling
(treated as inconclusive), non-HTTP scheme protection, and credential stripping.
Outputs a formatted markdown table suitable for the GitHub Actions job summary.

Usage:
    python3 tools/validate/check_source_links.py [roots ...]
        [--timeout TIMEOUT] [--max-redirects N] [--summary-file FILE]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

# Regex for extracting https:// URLs from text / comments.
_HTTPS_URL_RE = re.compile(r"https://[^\s\"'<>`]+", re.IGNORECASE)

# CAP identifier pattern (these are specification identifiers, not links).
_CAP_IDENTIFIER_RE = re.compile(r"^CAP-[0-9]{4}$")

# Default request timeout in seconds.
DEFAULT_TIMEOUT = 10.0

# Default maximum number of redirects to follow.
DEFAULT_MAX_REDIRECTS = 5

# Realistic User-Agent to prevent unwarranted blocks by CDNs/hosts.
USER_AGENT = "Mozilla/5.0 (compatible; ProtocolCanary-LinkCheck/1.0; +https://github.com/StellarCanary/ProtocolCanary-Fixtures)"


@dataclass(frozen=True)
class CheckResult:
    status: str  # "OK", "Inconclusive", "Failed"
    detail: str = ""

    def formatted(self) -> str:
        if self.detail and self.status != "OK":
            return f"{self.status} ({self.detail})"
        return self.status


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP redirect handler that intercepts redirects so the caller can
    enforce redirect bounds, detect cycles, and strip credentials at each hop.
    """

    def http_error_301(self, req, fp, code, msg, headers):
        return fp

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    def http_error_303(self, req, fp, code, msg, headers):
        return fp

    def http_error_307(self, req, fp, code, msg, headers):
        return fp

    def http_error_308(self, req, fp, code, msg, headers):
        return fp


def strip_credentials(url: str) -> str:
    """Strip userinfo (username and password) from a URL.

    Ensures that credentials are never transmitted over the wire or leaked
    into logs, tables, or step summaries.
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return url

    if parts.username is not None or parts.password is not None or (parts.netloc and "@" in parts.netloc):
        netloc = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def clean_comment_url(url: str) -> str:
    """Trim surrounding punctuation commonly found after URLs in comments."""
    while url and url[-1] in ".,;:!?'\")":
        if url[-1] == ")" and "(" in url:
            break
        url = url[:-1]
    return url


def extract_links_from_file(path: Path) -> list[str]:
    """Extract source_reference values and header comment https:// URLs from a fixture file.

    Returns a sorted list of unique sanitized URLs.
    """
    try:
        with open(path, "rb") as fp:
            content = fp.read()
    except OSError:
        return []

    text = content.decode("utf-8", errors="replace")
    found_urls: set[str] = set()

    # 1. Header comments: comments at the top of the file before the first TOML body key.
    header_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            header_lines.append(stripped)
        else:
            break

    for comment in header_lines:
        for match in _HTTPS_URL_RE.findall(comment):
            cleaned = clean_comment_url(match)
            if cleaned:
                found_urls.add(strip_credentials(cleaned))

    # 2. Parse TOML to read `source_reference`
    try:
        data = tomllib.loads(text)
        src = data.get("source_reference")
        if isinstance(src, str) and src.strip():
            src_str = src.strip()
            # If it's a CAP identifier (e.g. CAP-0083), it's not a link.
            if not _CAP_IDENTIFIER_RE.match(src_str):
                found_urls.add(strip_credentials(src_str))
    except Exception:
        # If TOML decoding fails, any header URLs found above are still checked.
        pass

    return sorted(found_urls)


def check_link(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    opener: urllib.request.OpenerDirector | None = None,
) -> CheckResult:
    """Check a URL with bounded redirects, timeout, 429 handling, and non-HTTP scheme guards."""
    sanitized_url = strip_credentials(url)
    try:
        parts = urlsplit(sanitized_url)
    except Exception as exc:
        return CheckResult("Failed", f"invalid URL: {exc}")

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return CheckResult("Failed", f"non-HTTP scheme: {scheme or 'none'}")

    if not parts.hostname:
        return CheckResult("Failed", "missing host")

    if opener is None:
        opener = urllib.request.build_opener(NoRedirectHandler)

    current_url = sanitized_url
    visited: set[str] = set()
    redirects_followed = 0

    while True:
        visited.add(current_url)
        req = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
            },
        )

        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return CheckResult("Inconclusive", "HTTP 429 Too Many Requests")
            if exc.code == 405:
                # Some RPC endpoints (e.g., soroban-testnet) reject GET with 405 Method Not Allowed.
                # Try POST with empty payload as a fallback check.
                try:
                    post_req = urllib.request.Request(
                        current_url,
                        data=b"",
                        headers={
                            "User-Agent": USER_AGENT,
                            "Accept": "*/*",
                        },
                    )
                    resp = opener.open(post_req, timeout=timeout)
                except urllib.error.HTTPError as post_exc:
                    if post_exc.code == 429:
                        return CheckResult("Inconclusive", "HTTP 429 Too Many Requests")
                    return CheckResult("Failed", f"HTTP {post_exc.code}")
                except Exception as post_err:
                    return CheckResult("Failed", f"POST fallback failed: {post_err}")
            else:
                return CheckResult("Failed", f"HTTP {exc.code}")
        except (TimeoutError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", str(exc))
            if "timed out" in str(reason).lower() or isinstance(exc, TimeoutError):
                return CheckResult("Failed", f"timed out after {timeout}s")
            return CheckResult("Failed", f"network error: {reason}")
        except Exception as exc:
            return CheckResult("Failed", f"{type(exc).__name__}: {exc}")

        status = resp.status
        try:
            if status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return CheckResult("Failed", f"HTTP {status} missing Location header")

                next_url = strip_credentials(urljoin(current_url, location))
                next_parts = urlsplit(next_url)
                next_scheme = (next_parts.scheme or "").lower()
                if next_scheme not in ("http", "https"):
                    return CheckResult("Failed", f"redirected to non-HTTP scheme: {next_scheme or 'none'}")

                if next_url in visited:
                    return CheckResult("Failed", "redirect loop detected")

                redirects_followed += 1
                if redirects_followed > max_redirects:
                    return CheckResult("Failed", f"too many redirects (exceeded limit of {max_redirects})")

                current_url = next_url
                continue
            elif status == 200:
                return CheckResult("OK")
            elif status == 429:
                return CheckResult("Inconclusive", "HTTP 429 Too Many Requests")
            else:
                return CheckResult("Failed", f"HTTP {status}")
        finally:
            resp.close()


def find_fixture_files(root: Path) -> list[Path]:
    """Find all .toml fixture files under root."""
    if root.is_file():
        return [root] if root.suffix == ".toml" else []
    return sorted(p for p in root.rglob("*.toml") if p.is_file())


def generate_markdown_table(rows: list[tuple[str, str, CheckResult]]) -> str:
    """Generate a Markdown table from a list of (url, fixture_file, result) tuples."""
    lines = [
        "| URL | Fixture File | Result |",
        "|---|---|---|",
    ]
    for url, fixture_file, result in rows:
        lines.append(f"| {url} | {fixture_file} | {result.formatted()} |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check links in fixture source_reference fields and header comments on a schedule."
    )
    parser.add_argument(
        "roots",
        nargs="*",
        help="Directories or .toml files to scan. Defaults to protocol-* directories in the repo root.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT}s)",
    )
    parser.add_argument(
        "--max-redirects",
        type=int,
        default=DEFAULT_MAX_REDIRECTS,
        help=f"Maximum number of redirects to follow (default: {DEFAULT_MAX_REDIRECTS})",
    )
    parser.add_argument(
        "--summary-file",
        help="Optional path to append the markdown table to (e.g. $GITHUB_STEP_SUMMARY)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    if args.roots:
        scan_paths = [Path(r) for r in args.roots]
        for scan_path in scan_paths:
            if not scan_path.exists():
                sys.stderr.write(f"Error: root does not exist: {scan_path}\n")
                return 2
            if not scan_path.is_dir() and not (
                scan_path.is_file() and scan_path.suffix == ".toml"
            ):
                sys.stderr.write(
                    f"Error: root must be a directory or .toml file: {scan_path}\n"
                )
                return 2
    else:
        scan_paths = sorted(p for p in repo_root.glob("protocol-*") if p.is_dir())
        if not scan_paths:
            scan_paths = [repo_root]

    fixture_files: list[Path] = []
    for sp in scan_paths:
        fixture_files.extend(find_fixture_files(sp))

    if args.roots and not fixture_files:
        roots = ", ".join(str(path) for path in scan_paths)
        sys.stderr.write(f"Error: no .toml fixture files found in explicit roots: {roots}\n")
        return 2

    # Remove duplicates while preserving order
    unique_files: list[Path] = []
    seen_files: set[Path] = set()
    for f in fixture_files:
        resolved = f.resolve()
        if resolved not in seen_files:
            seen_files.add(resolved)
            unique_files.append(f)

    # Collect pairs of (url, file_display_path)
    links_to_check: list[tuple[str, str]] = []
    for file_path in unique_files:
        try:
            rel_path = file_path.relative_to(repo_root)
        except ValueError:
            try:
                rel_path = file_path.relative_to(Path.cwd())
            except ValueError:
                rel_path = file_path
        urls = extract_links_from_file(file_path)
        for u in urls:
            links_to_check.append((u, str(rel_path)))

    # Sort deterministically
    links_to_check.sort(key=lambda item: (item[1], item[0]))

    # Check URLs with caching
    url_cache: dict[str, CheckResult] = {}
    rows: list[tuple[str, str, CheckResult]] = []
    ok_count = 0
    inconclusive_count = 0
    failed_count = 0

    opener = urllib.request.build_opener(NoRedirectHandler)

    for url, file_path in links_to_check:
        if url not in url_cache:
            url_cache[url] = check_link(
                url,
                timeout=args.timeout,
                max_redirects=args.max_redirects,
                opener=opener,
            )
        result = url_cache[url]
        rows.append((url, file_path, result))

        if result.status == "OK":
            ok_count += 1
        elif result.status == "Inconclusive":
            inconclusive_count += 1
        else:
            failed_count += 1

    table = generate_markdown_table(rows)
    summary_text = (
        f"\n\n**Source Reference Links Check Summary**: "
        f"{len(rows)} checked across {len(unique_files)} fixture files "
        f"({ok_count} OK, {inconclusive_count} Inconclusive, {failed_count} Failed).\n"
    )

    full_output = table + summary_text
    print(full_output)

    # Write to summary file if specified or present in environment
    summary_file = args.summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as fp:
                fp.write(full_output)
        except OSError as exc:
            sys.stderr.write(f"Warning: could not write to summary file {summary_file}: {exc}\n")

    # Inconclusive (429) does not fail the job; only Failed fails the job.
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

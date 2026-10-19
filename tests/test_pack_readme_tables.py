"""Consistency checks between each protocol pack and its README fixture table.

A pack README that carries a Markdown fixture table (header columns
``Fixture`` and ``Surface``, e.g. ``| Fixture | Surface | CAP | What it
proves |``) is checked against the pack itself:

1. every ``*.toml`` in the pack appears exactly once in the table, and
   every table row links to an existing fixture file;
2. the Surface column equals the fixture's ``surface`` field and the
   fixture's surface directory inside the pack;
3. a CAP column value other than the dash (``—``) equals the fixture's
   ``cap-<4-digit number>`` directory name, and fixtures under such a
   directory carry that value.

A pack whose README has no fixture table (or no README at all) is
reported as skipped with a stated reason — never as a failure.  This
file only reads READMEs; it never edits them.

Run with: python3 -m unittest discover tests
"""
from __future__ import annotations

import re
import tempfile
import tomllib
import unittest
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]

# The dash a pack README uses in the CAP column when a fixture asserts
# general protocol behaviour rather than one CAP.
CAP_DASHES = frozenset({"—", "–", "-"})

# A CAP directory is named cap-<4-digit number>, e.g. cap-0083.
CAP_DIR_RE = re.compile(r"^cap-\d{4}$", re.IGNORECASE)

# The first Markdown link inside a cell, e.g. [`p28.toml`](p28.toml).
LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*<?([^()<>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")

# A table separator row cell, e.g. `---` or `:---:`.
SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")


# --------------------------------------------------------------------------
# Markdown table parsing (small, purpose-built; GFM pipe tables only).
# --------------------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    """Split one pipe-table row into stripped cell strings.

    Tolerates escaped pipes (``\\|``) and pipes inside backtick code
    spans, plus optional leading/trailing delimiters.
    """
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            current.append("\\|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
            current.append(ch)
            i += 1
            continue
        if ch == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _is_separator_row(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(SEPARATOR_CELL_RE.match(c.replace(" ", "")) for c in cells)


def _link_target(cell: str) -> str | None:
    """Return the target of the first Markdown link in *cell*, if any."""
    match = LINK_RE.search(cell)
    if match is None:
        return None
    return match.group(2)


def _cell_value(cell: str) -> str:
    """Reduce a cell to its plain value: link text, no backticks/space."""
    match = LINK_RE.search(cell)
    if match is not None:
        cell = match.group(1)
    return cell.replace("`", "").strip()


def _header_names(header: list[str]) -> list[str]:
    return [_cell_value(c).casefold() for c in header]


@dataclass
class TableRow:
    number: int  # 1-based position among the table's body rows
    line_number: int  # 1-based line in the README text
    cells: list[str]


@dataclass
class FixtureTable:
    header: list[str]
    rows: list[TableRow]
    header_line: int

    def column(self, name: str) -> int | None:
        """Index of the column named *name*, or None if absent."""
        wanted = name.casefold()
        for index, title in enumerate(_header_names(self.header)):
            if title == wanted:
                return index
        return None


def parse_fixture_tables(text: str) -> list[FixtureTable]:
    """Return every table in *text* whose header has Fixture and Surface columns."""
    lines = text.replace("\r\n", "\n").splitlines()
    tables: list[FixtureTable] = []
    i = 0
    while i < len(lines) - 1:
        header = _split_row(lines[i]) if "|" in lines[i] else []
        if (
            header
            and _is_separator_row(lines[i + 1])
            and len(_split_row(lines[i + 1])) == len(header)
        ):
            names = _header_names(header)
            rows: list[TableRow] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                cells = _split_row(lines[j])
                if len(cells) < len(header):
                    cells = cells + [""] * (len(header) - len(cells))
                rows.append(TableRow(number=len(rows) + 1, line_number=j + 1, cells=cells))
                j += 1
            if "fixture" in names and "surface" in names:
                tables.append(FixtureTable(header=header, rows=rows, header_line=i + 1))
            i = j
        else:
            i += 1
    return tables


# --------------------------------------------------------------------------
# Pack-vs-table consistency checking.
# --------------------------------------------------------------------------


@dataclass
class PackReport:
    name: str
    status: str  # "checked" or "skipped"
    reason: str = ""  # why the pack was skipped
    errors: list[str] = field(default_factory=list)
    row_count: int = 0
    fixture_count: int = 0


def _normalize_target(target: str) -> str:
    """Normalize a link target to a pack-relative POSIX path."""
    target = unquote(target).strip()
    while target.startswith("./"):
        target = target[2:]
    return str(PurePosixPath(target))


def check_pack(pack_dir: Path) -> PackReport:
    """Check one protocol-* pack against its README fixture table."""
    report = PackReport(name=pack_dir.name, status="checked")
    readme = pack_dir / "README.md"
    if not readme.is_file():
        report.status = "skipped"
        report.reason = "pack has no README.md"
        return report

    tables = parse_fixture_tables(readme.read_text(encoding="utf-8"))
    if not tables:
        report.status = "skipped"
        report.reason = (
            "README.md has no fixture table with Fixture and Surface columns"
        )
        return report
    table = tables[0]

    fixtures = sorted(
        p.relative_to(pack_dir).as_posix()
        for p in pack_dir.rglob("*.toml")
        if p.is_file()
    )
    report.fixture_count = len(fixtures)
    report.row_count = len(table.rows)

    def error(row: TableRow | None, target: str | None, message: str) -> None:
        where = f"row {row.number} (line {row.line_number})" if row else "the table"
        if target:
            where = f"{where}, {target!r}"
        report.errors.append(f"{report.name}: {where}: {message}")

    fixture_col = table.column("Fixture")
    surface_col = table.column("Surface")
    cap_col = table.column("CAP")

    linked: dict[str, list[TableRow]] = {}
    for row in table.rows:
        assert fixture_col is not None
        target = _link_target(row.cells[fixture_col])
        if target is None:
            error(row, None, "Fixture cell contains no Markdown link")
            continue
        normalized = _normalize_target(target)
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or ".." in pure.parts:
            error(row, normalized, "link target escapes the pack")
            continue
        linked.setdefault(normalized, []).append(row)
        path = pack_dir / pure
        if not path.is_file():
            error(row, normalized, "linked file does not exist")
        elif normalized not in fixtures:
            error(row, normalized, "linked file is not a *.toml fixture in this pack")

    for target, rows in linked.items():
        if len(rows) > 1:
            numbers = ", ".join(f"row {r.number}" for r in rows)
            error(
                rows[1],
                target,
                f"duplicate rows for the same fixture ({numbers}); "
                "each fixture must appear exactly once",
            )

    for target, rows in linked.items():
        row = rows[0]
        path = pack_dir / PurePosixPath(target)
        if not path.is_file() or target not in fixtures:
            continue  # already reported above
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            error(row, target, f"fixture is not parseable TOML: {exc}")
            continue

        relative = PurePosixPath(target)
        surface_field = data.get("surface")

        assert surface_col is not None
        surface_cell = _cell_value(row.cells[surface_col])
        if surface_cell != str(surface_field):
            error(
                row,
                target,
                f"Surface {surface_cell!r} does not match the fixture's "
                f"surface field {surface_field!r}",
            )
        if len(relative.parts) > 1 and surface_cell != relative.parts[0]:
            error(
                row,
                target,
                f"Surface {surface_cell!r} does not match the path's surface "
                f"directory {relative.parts[0]!r}",
            )

        if cap_col is None:
            continue
        cap_cell = _cell_value(row.cells[cap_col])
        cap_dirs = [p for p in relative.parts[:-1] if CAP_DIR_RE.match(p)]
        if not cap_dirs:
            if cap_cell not in CAP_DASHES:
                error(
                    row,
                    target,
                    f"CAP {cap_cell!r} but the fixture is not under a "
                    "cap-<4-digit number> directory",
                )
        else:
            cap_dir = cap_dirs[0]
            if cap_cell in CAP_DASHES:
                error(
                    row,
                    target,
                    f"CAP {cap_cell!r} but the fixture is under the "
                    f"{cap_dir!r} directory",
                )
            elif cap_cell.casefold() != cap_dir.casefold():
                error(
                    row,
                    target,
                    f"CAP {cap_cell!r} does not match the fixture's "
                    f"{cap_dir!r} directory",
                )

    for fixture in fixtures:
        if fixture not in linked:
            report.errors.append(
                f"{report.name}: fixture {fixture!r} has no row in the "
                "fixture table"
            )

    return report


def check_repository(root: Path) -> list[PackReport]:
    """Check every protocol-* pack under *root*."""
    packs = sorted(p for p in root.glob("protocol-*") if p.is_dir())
    return [check_pack(pack) for pack in packs]


# --------------------------------------------------------------------------
# Synthetic negative/positive tests, built entirely in temp directories.
# --------------------------------------------------------------------------

FIXTURE_HEADER = "| Fixture | Surface | CAP | What it proves |\n|---|---|---|---|\n"


def fixture_toml(surface: str) -> str:
    return (
        'id = "example"\n'
        "protocol = 28\n"
        f'surface = "{surface}"\n'
        'category = "general"\n'
        'description = "example"\n'
        'source_reference = "example"\n'
    )


def table_row(path: str, surface: str, cap: str, proves: str = "It proves X.") -> str:
    return f"| [`{path}`]({path}) | {surface} | {cap} | {proves} |"


def write(root: Path, name: str, contents: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8", newline="")
    return path


def build_pack(
    root: Path,
    name: str,
    fixtures: dict[str, str],
    readme: str | None,
) -> Path:
    """Create a pack dir: *fixtures* maps relative path -> surface field."""
    pack = root / name
    pack.mkdir(parents=True, exist_ok=True)
    for rel_path, surface in fixtures.items():
        write(pack, rel_path, fixture_toml(surface))
    if readme is not None:
        write(pack, "README.md", readme)
    return pack


class SyntheticPackReadmeTests(unittest.TestCase):
    """The checker accepts good packs and pinpoints every bad one."""

    def check(self, pack: PackReport) -> PackReport:
        self.assertEqual(pack.status, "checked", pack.reason)
        return pack

    def assertFailed(self, pack: PackReport, *fragments: str) -> None:
        self.assertTrue(pack.errors, f"expected errors, got none: {pack}")
        for fragment in fragments:
            self.assertTrue(
                any(fragment in e for e in pack.errors),
                f"no error contains {fragment!r}: {pack.errors}",
            )

    def test_consistent_pack_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {
                    "xdr/cap-0083/a.toml": "xdr",
                    "rpc/b.toml": "rpc",
                    "soroban/c.toml": "soroban",
                },
                FIXTURE_HEADER
                + table_row("xdr/cap-0083/a.toml", "xdr", "CAP-0083")
                + "\n"
                + table_row("rpc/b.toml", "rpc", "—")
                + "\n"
                + table_row("soroban/c.toml", "soroban", "—")
                + "\n",
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.errors, [])
            self.assertEqual(report.row_count, 3)
            self.assertEqual(report.fixture_count, 3)

    def test_missing_row_fails_naming_pack_and_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr", "xdr/b.toml": "xdr"},
                FIXTURE_HEADER + table_row("xdr/a.toml", "xdr", "—") + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(report, "protocol-99", "xdr/b.toml", "has no row")

    def test_extra_row_for_non_fixture_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER
                + table_row("xdr/a.toml", "xdr", "—")
                + "\n"
                + table_row("xdr/notes.md", "xdr", "—")
                + "\n",
            )
            write(pack, "xdr/notes.md", "notes\n")
            report = check_pack(pack)
            self.assertFailed(
                report, "protocol-99", "row 2", "not a *.toml fixture"
            )

    def test_row_for_missing_file_fails_naming_pack_and_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER
                + table_row("xdr/a.toml", "xdr", "—")
                + "\n"
                + table_row("xdr/ghost.toml", "xdr", "—")
                + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(
                report, "protocol-99", "row 2", "xdr/ghost.toml", "does not exist"
            )

    def test_duplicate_row_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER
                + table_row("xdr/a.toml", "xdr", "—")
                + "\n"
                + table_row("xdr/a.toml", "xdr", "—")
                + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(report, "protocol-99", "row 2", "duplicate rows")

    def test_wrong_surface_value_fails_naming_pack_and_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER + table_row("xdr/a.toml", "rpc", "—") + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(
                report, "protocol-99", "row 1", "Surface 'rpc'", "'xdr'"
            )

    def test_surface_must_match_the_path_surface_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                # surface field says rpc but the fixture lives under xdr/
                {"xdr/a.toml": "rpc"},
                FIXTURE_HEADER + table_row("xdr/a.toml", "rpc", "—") + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(
                report, "protocol-99", "row 1", "surface directory 'xdr'"
            )

    def test_wrong_cap_value_fails_naming_pack_and_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/cap-0083/a.toml": "xdr"},
                FIXTURE_HEADER + table_row("xdr/cap-0083/a.toml", "xdr", "CAP-0085")
                + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(
                report,
                "protocol-99",
                "row 1",
                "CAP 'CAP-0085'",
                "'cap-0083'",
            )

    def test_dash_cap_fails_under_a_cap_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/cap-0083/a.toml": "xdr"},
                FIXTURE_HEADER + table_row("xdr/cap-0083/a.toml", "xdr", "—") + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(
                report, "protocol-99", "row 1", "CAP '—'", "cap-0083"
            )

    def test_cap_value_fails_without_a_cap_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER + table_row("xdr/a.toml", "xdr", "CAP-0083") + "\n",
            )
            report = check_pack(pack)
            self.assertFailed(
                report, "protocol-99", "row 1", "not under a cap-<4-digit"
            )

    def test_readme_without_a_fixture_table_is_skipped_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                "# Pack\n\nNo table here.\n",
            )
            report = check_pack(pack)
            self.assertEqual(report.status, "skipped")
            self.assertIn("no fixture table", report.reason)
            self.assertEqual(report.errors, [])

    def test_pack_without_a_readme_is_skipped_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(root, "protocol-99", {"xdr/a.toml": "xdr"}, None)
            report = check_pack(pack)
            self.assertEqual(report.status, "skipped")
            self.assertIn("README", report.reason)
            self.assertEqual(report.errors, [])

    def test_escaped_pipe_in_a_cell_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER
                + table_row(
                    "xdr/a.toml", "xdr", "—", "Accepts `a \\| b` without splitting."
                )
                + "\n",
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.row_count, 1)

    def test_relative_dot_slash_link_prefix_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                FIXTURE_HEADER
                + "| [`xdr/a.toml`](./xdr/a.toml) | xdr | — | It proves X. |\n",
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.errors, [])

    def test_only_the_table_with_a_fixture_and_surface_header_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unrelated = (
                "| Column | Meaning |\n|---|---|\n| Fixture | not a real column |\n\n"
            )
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                unrelated
                + FIXTURE_HEADER
                + table_row("xdr/a.toml", "xdr", "—")
                + "\n",
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.errors, [])
            self.assertEqual(report.row_count, 1)

    def test_fixture_path_containing_spaces_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/my dir/a.toml": "xdr"},
                FIXTURE_HEADER
                + "| [`xdr/my dir/a.toml`](xdr/my%20dir/a.toml) | xdr | — | It proves X. |\n",
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.errors, [])

    def test_crlf_line_endings_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/a.toml": "xdr"},
                (FIXTURE_HEADER + table_row("xdr/a.toml", "xdr", "—") + "\n").replace(
                    "\n", "\r\n"
                ),
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.errors, [])
            self.assertEqual(report.row_count, 1)

    def test_backticked_surface_and_cap_cells_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = build_pack(
                root,
                "protocol-99",
                {"xdr/cap-0083/a.toml": "xdr"},
                FIXTURE_HEADER
                + "| [`xdr/cap-0083/a.toml`](xdr/cap-0083/a.toml) | `xdr` | `CAP-0083` | It proves X. |\n",
            )
            report = self.check(check_pack(pack))
            self.assertEqual(report.errors, [])


# --------------------------------------------------------------------------
# The real repository: every pack is checked or skipped, none fails.
# --------------------------------------------------------------------------


class RepositoryPackReadmeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reports = check_repository(REPO_ROOT)
        for report in cls.reports:
            if report.status == "skipped":
                print(f"skipped: {report.name}: {report.reason}")

    def report_named(self, name: str) -> PackReport:
        for report in self.reports:
            if report.name == name:
                return report
        self.fail(f"no report for {name}: {[r.name for r in self.reports]}")

    def test_every_protocol_pack_is_checked_or_skipped(self) -> None:
        expected = {p.name for p in REPO_ROOT.glob("protocol-*") if p.is_dir()}
        self.assertEqual({r.name for r in self.reports}, expected)
        for report in self.reports:
            self.assertIn(report.status, {"checked", "skipped"}, report.name)

    def test_no_pack_has_a_readme_table_mismatch(self) -> None:
        errors = [e for report in self.reports for e in report.errors]
        self.assertEqual(errors, [])

    def test_protocol_28_table_covers_every_fixture_exactly_once(self) -> None:
        report = self.report_named("protocol-28")
        self.assertEqual(report.status, "checked", report.reason)
        self.assertEqual(report.row_count, report.fixture_count)
        self.assertGreater(report.row_count, 0)

    def test_packs_without_a_fixture_table_are_skipped_with_a_reason(self) -> None:
        # protocol-27's README exists but has no fixture table; protocol-29
        # has no README at all. Both must be skipped, never failed.
        report27 = self.report_named("protocol-27")
        self.assertEqual(report27.status, "skipped")
        self.assertIn("no fixture table", report27.reason)
        self.assertEqual(report27.errors, [])

        report29 = self.report_named("protocol-29")
        self.assertEqual(report29.status, "skipped")
        self.assertIn("README", report29.reason)
        self.assertEqual(report29.errors, [])


if __name__ == "__main__":
    unittest.main()

"""Portable fixture-pack path names, shared by every checking tool (F-049).

A fixture pack is extracted on Windows as well as on Linux and macOS, so a
path that a POSIX filesystem accepts but Windows cannot create is a pack-wide
defect: it fails only when a contributor or user checks the pack out or
extracts it, long after merge. The rules below are the Windows half of the
portable-name contract:

* a segment that is (or ends in, before its first extension) a reserved
  device name -- ``CON``, ``PRN``, ``AUX``, ``NUL``, ``COM1`` to ``COM9`` or
  ``LPT1`` to ``LPT9`` -- in any letter case, with or without an extension;
* a segment ending in a dot or a space (Windows silently strips it, so two
  names could collide);
* a segment containing one of ``< > : " | ? *``.

This module deliberately contains only the shared rule. The validator, the
registry generator and the archive verifier each add their own contract
checks (empty or ``.``/``..`` segments, backslashes, control characters,
depth, case folding) around it, and all three call ``check_path`` or
``check_segment`` so they cannot disagree about what Windows accepts.

Standard library only.
"""
from __future__ import annotations

# Windows reserves these names as device names. The comparison is
# case-insensitive, and the name is reserved even when it has an extension:
# ``con.toml`` and ``aux.v2.toml`` are both unusable.
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)

# Characters Windows forbids in a file or directory name. Backslash is not
# here because the pack contract already rejects it as a separator; keeping
# the two rules separate lets each tool report the more specific problem.
WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')


class PortableNameError(ValueError):
    """A path segment (or path) is not portable to Windows."""

    def __init__(self, segment: str, problem: str) -> None:
        self.segment = segment
        self.problem = problem
        super().__init__(f"path segment {segment!r} {problem}")


def _reserved_stem(segment: str) -> bool:
    """Whether ``segment`` names a Windows device, before its first dot.

    Windows ignores a trailing dot or space when it resolves a name, so
    ``con .toml`` and ``con..toml`` are reserved too. Unicode look-alikes are
    not reserved: ``СON`` with a Cyrillic ``С`` is a different name and is
    valid on Windows, so the comparison is over the characters as written.
    """
    stem = segment.split(".", 1)[0].rstrip(" .")
    return stem.upper() in WINDOWS_RESERVED_NAMES


def check_segment(segment: str) -> None:
    """Raise ``PortableNameError`` unless ``segment`` is portable to Windows."""
    if not segment:
        raise PortableNameError(segment, "is empty")
    if segment.endswith((".", " ")):
        raise PortableNameError(
            segment,
            "ends in a dot or a space, which Windows silently strips from a "
            "file name and can make it collide with another name",
        )
    for character in segment:
        if character in WINDOWS_FORBIDDEN_CHARACTERS:
            raise PortableNameError(
                segment,
                f"contains {character!r}, which Windows does not allow in a "
                "file or directory name",
            )
    if _reserved_stem(segment):
        raise PortableNameError(
            segment,
            "is a Windows reserved device name (CON, PRN, AUX, NUL, COM1-COM9 "
            "or LPT1-LPT9), even when it has an extension",
        )


def check_path(path: str) -> None:
    """Raise ``PortableNameError`` unless every ``/``-separated segment is portable."""
    for segment in path.split("/"):
        check_segment(segment)

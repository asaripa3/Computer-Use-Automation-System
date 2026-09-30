"""The Makefile is the documented interface, so it is tested like one.

This file exists because of a real bug. `make fault` was silently broken for
four build steps: a mangled continuation line passed `-d` to curl as a
hostname, so the command printed the *unchanged* fault state and exited zero.
It looked like it worked. A reviewer following the README would have armed a
fault, seen the replay succeed, and concluded the error handling did not work.

Nothing caught it because the tests arm faults programmatically and never go
through `make`. These checks are cheap and cover the shape of that failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"
LINES = MAKEFILE.read_text().splitlines()

# A recipe line: a tab, optionally an @ to silence it, then the command.
RECIPE = re.compile(r"^\t@?(?P<body>.*)$")
TARGET = re.compile(r"^([a-z][a-z-]*):")


def recipes() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current = ""
    for line in LINES:
        target = TARGET.match(line)
        if target:
            current = target.group(1)
            found[current] = []
        elif line.startswith("\t") and current:
            found[current].append(line)
    return found


def test_no_recipe_line_starts_with_a_stray_at_sign():
    """The exact signature of the bug that shipped.

    `\\t@-d '{...}'` makes the shell treat `-d` as a command argument to
    nothing, which curl then reads as a hostname.
    """
    offenders = [
        (i, line) for i, line in enumerate(LINES, 1)
        if re.match(r"^\t@[-'\"]", line)
    ]
    assert not offenders, f"mangled continuation line(s): {offenders}"


def test_every_continuation_line_is_indented_not_silenced():
    # A line following a backslash continuation is part of the same command
    # and must not begin with @, which only makes sense at a recipe's start.
    for index, line in enumerate(LINES[:-1]):
        if line.rstrip().endswith("\\"):
            following = LINES[index + 1]
            assert not re.match(r"^\t@", following), (
                f"line {index + 2} continues a command but starts with @: {following!r}"
            )


@pytest.mark.parametrize(
    "target", ["help", "run", "test", "test-fast", "fault", "reset",
               "replay", "discover", "review", "observe", "capabilities", "evidence"],
)
def test_the_documented_targets_exist(target):
    assert target in recipes(), f"the README and docs refer to `make {target}`"


def test_every_target_is_declared_phony():
    # A target sharing a name with a directory -- `evidence` does -- is
    # skipped by make unless it is declared phony.
    phony = next(
        (line for line in LINES if line.startswith(".PHONY:")), ""
    ).removeprefix(".PHONY:").split()
    documented = [t for t in recipes() if t != "help"]
    missing = sorted(set(documented) - set(phony))
    assert not missing, f"not declared .PHONY: {missing}"


def test_targets_that_talk_to_the_server_print_only_its_reply():
    """`make fault` output is piped into a JSON parser in the docs.

    An un-silenced recipe echoes the command first, and the parse fails on
    text that is not JSON.
    """
    for target in ("fault", "reset", "faults"):
        for line in recipes()[target]:
            if "curl" in line:
                assert line.startswith("\t@"), (
                    f"`make {target}` would echo its own command before the "
                    f"reply: {line!r}"
                )

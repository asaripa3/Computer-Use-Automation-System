"""Print what the surface layer sees on one page.

    python -m surface.cli /console/member/12345

A debugging tool, and the quickest way to answer the question that matters
before any locator is written: what does this layer actually believe is on the
page, and which of those names did it have to infer?

Requires a running ShareBase (``make run``).
"""

from __future__ import annotations

import argparse
import os
import sys

from .view import render
from .browser import browser_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="surface.cli", description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default="/console/member/12345",
        help="path to observe, relative to the base URL",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("SHAREBASE_BASE", "http://127.0.0.1:8080"),
        help="base URL of a running ShareBase",
    )
    parser.add_argument(
        "--no-tables", action="store_true", help="skip the reassembled grids"
    )
    parser.add_argument(
        "--headed", action="store_true", help="show the browser while it works"
    )
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    path = args.path if args.path.startswith("/") else "/" + args.path

    with browser_session(headless=not args.headed) as surface:
        surface.goto(f"{base}/login")
        observation = surface.observe()

        operator = observation.field("Operator ID")
        if operator is None:
            print(f"no sign-on form at {base}/login -- is ShareBase running?", file=sys.stderr)
            return 1

        surface.fill(operator.ref, os.environ.get("SHAREBASE_USER", "svc_agent"))
        surface.fill(
            observation.field("Password").ref,
            os.environ.get("SHAREBASE_PASS", "Demo-Pass-1234"),
        )
        surface.click(observation.find(role="button", name="Sign On")[0].ref)

        surface.goto(f"{base}{path}")
        print(render(surface.observe(), include_tables=not args.no_tables))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Load the repository's .env for command-line entry points.

The ShareBase server loaded it; the CLIs did not, so a key set in .env was
invisible to exactly the command that needed it. Loading is done here, once,
so every entry point behaves the same way.

`override=False` on purpose: a value already exported in the shell wins over
the file, which is what someone switching keys for a single run expects.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # dotenv is optional; the environment still works
        return
    load_dotenv(ROOT / ".env", override=False)

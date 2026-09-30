"""Reading and writing capability artifacts.

JSON on disk, one file per capability, named for its id and version. Not a
database: a capability has to be reviewable in a pull request and diffable
between versions, and a text file is the only format that gives both for free.
"""

from __future__ import annotations

import json
from pathlib import Path

from .capability import Capability
from .errors import ContractError

CAPABILITY_SUFFIX = ".capability.json"


def dumps(capability: Capability) -> str:
    return json.dumps(capability.to_dict(), indent=2, ensure_ascii=False) + "\n"


def loads(text: str, *, origin: str = "<string>") -> Capability:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(origin, f"not valid JSON: {exc}") from None
    return Capability.from_dict(data)


def save(capability: Capability, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{capability.id}@{capability.version}{CAPABILITY_SUFFIX}"
    path.write_text(dumps(capability), encoding="utf-8")
    return path


def load(path: Path) -> Capability:
    return loads(Path(path).read_text(encoding="utf-8"), origin=str(path))


def catalog(directory: Path) -> list[Capability]:
    """Every capability in a directory, sorted by id and version.

    The beginnings of the agent-facing catalog in the stretch goals: a calling
    agent discovering what it can invoke reads exactly this.
    """
    directory = Path(directory)
    if not directory.exists():
        return []
    found = [load(p) for p in sorted(directory.glob(f"*{CAPABILITY_SUFFIX}"))]
    return sorted(found, key=lambda c: (c.id, c.version))

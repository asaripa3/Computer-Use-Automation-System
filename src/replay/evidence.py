"""What a run leaves behind.

§3.5 asks for a structured log of what happened and why, plus at least one
richer signal on failure. Three things are written:

    run.jsonl      one line per step: what was attempted, which locator rung
                   resolved it, what recoveries fired, how long it took
    result.json    the replay contract's own result object
    failure.txt    what the surface layer saw at the moment it failed

Pixel captures are not persisted because a screenshot can contain personal
data that text redaction cannot remove. `failure.txt` preserves the page shape
with values replaced by their lengths, which is enough to diagnose a locator
failure without retaining member information.

Everything written here passes through the redactor first. A run against a
real institution produces evidence containing member data, and evidence is
exactly the artefact most likely to be copied into a ticket.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from policy.redaction import Redactor
from surface.model import Observation
from surface.view import render


class Evidence:
    """A directory holding one run's record."""

    def __init__(self, directory: Path, redactor: Redactor | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or Redactor()
        self._log = self.directory / "run.jsonl"

    # -- the structured log ------------------------------------------------

    def note(self, event: str, **fields: Any) -> None:
        record = self.redactor.record({
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        })
        with self._log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def step(self, report) -> None:
        self.note("step", **report.to_dict())

    # -- the richer signal on failure --------------------------------------

    def capture_failure(self, surface, observation: Observation | None) -> list[str]:
        written: list[str] = []
        if observation is not None:
            seen = self.directory / "failure.txt"
            # Structure, not content: every control, name and column is
            # kept and the values are replaced by their length. A locator
            # failure is debugged from the shape of the page, and the shape
            # carries no member data.
            seen.write_text(
                self.redactor.text(render(observation, reveal=False)) or "",
                encoding="utf-8",
            )
            written.append(seen.name)

        return written

    # -- the result --------------------------------------------------------

    def finish(self, result) -> Path:
        path = self.directory / "result.json"
        payload = self.redactor.record(result.to_dict())
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        return path

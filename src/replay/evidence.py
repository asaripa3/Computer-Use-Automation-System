"""What a run leaves behind.

§3.5 asks for a structured log of what happened and why, plus at least one
richer signal on failure. Three things are written:

    run.jsonl      one line per step: what was attempted, which locator rung
                   resolved it, what recoveries fired, how long it took
    result.json    the replay contract's own result object
    failure.png    a screenshot, written only when a run fails
    failure.txt    what the surface layer saw at the moment it failed

The screenshot is for a person; `failure.txt` is for whoever has to work out
*why* the locator did not resolve, and a rendering of the perceived controls
answers that far better than a picture does.

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
        try:
            shot = self.directory / "failure.png"
            shot.write_bytes(surface.screenshot())
            written.append(shot.name)
        except Exception as exc:  # a screenshot must never mask the real failure
            self.note("evidence_warning", detail=f"screenshot unavailable: {exc}")

        if observation is not None:
            seen = self.directory / "failure.txt"
            seen.write_text(
                self.redactor.text(render(observation)) or "", encoding="utf-8"
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

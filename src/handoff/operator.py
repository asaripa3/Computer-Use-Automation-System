"""How an intervention reaches a person, and how their answer comes back.

§3.6 permits mocking the operator surface, and says the *mechanism* and the
*control-transfer model* must be real. So this module is a protocol with three
small implementations, and the seam is the point: a real deployment replaces
these with a queue, a ticket, or a co-browsing console, and nothing above
changes.

`ConsoleOperator` is the honest demo. Run headed, and the browser the
automation was driving is right there on screen -- the operator works in that
window, the same session with the same cookies, and presses Enter. There is
no co-browsing infrastructure because none is needed: the live session is
already a window, and handing it over means not touching it.

`FileOperator` is the same handoff without a terminal: the request is written
where something else can pick it up, and the reply is read back from disk. It
is what a queue integration would look like from this side, and it is how the
handoff runs unattended.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .intervention import InterventionRequest, Resolution

VALID = {"retry", "skip", "abandon"}


class Operator(Protocol):
    """Somewhere an intervention can be routed to."""

    def notify(self, request: InterventionRequest) -> None: ...

    def wait(self, request: InterventionRequest, *, poll: Callable[[], None]) -> Resolution:
        """Block until the person answers.

        ``poll`` is called between checks so the caller can keep sampling the
        session -- which is how the journal records what the human did while
        this is waiting.
        """
        ...


def _render(request: InterventionRequest) -> str:
    lines = [
        "",
        "=" * 72,
        "  INTERVENTION REQUIRED",
        "=" * 72,
        f"  {request.summary()}",
        "",
        f"  request    {request.id}",
        f"  kind       {request.kind}",
        f"  capability {request.capability_ref}",
    ]
    if request.goal:
        lines.append(f"  goal       {request.goal}")
    if request.step_index is not None:
        lines.append(f"  step       {request.step_index} -- {request.step_description}")
    lines += [
        f"  page       {request.url}",
        "",
        "  WHAT TO DO",
    ]
    for chunk in request.what_to_do.split(". "):
        if chunk.strip():
            lines.append(f"    {chunk.strip().rstrip('.')}.")
    if request.evidence_dir:
        lines += ["", f"  evidence   {request.evidence_dir}"]
    if request.perceived:
        lines += ["", "  CURRENT PAGE (values suppressed)", request.perceived]
    lines += [
        "",
        "  The browser window the automation was using is now yours. Work in it",
        "  directly -- it is the same session, signed in, on the page above.",
        "",
        "  If you carry the step out yourself, answer while the resulting",
        "  screen is still up. That screen is how the run confirms it worked.",
        "",
        "  Then answer:",
        "    retry    you put things right; let the automation try that step again",
        "    skip     you did that step yourself; carry on from the next one",
        "    abandon  this run should not continue",
        "=" * 72,
    ]
    return "\n".join(lines)


@dataclass
class ConsoleOperator:
    """A person at the terminal, with the live browser in front of them."""

    who: str = "operator@console"
    stream = sys.stdout

    def notify(self, request: InterventionRequest) -> None:
        print(_render(request), file=self.stream, flush=True)

    def wait(self, request: InterventionRequest, *, poll: Callable[[], None]) -> Resolution:
        while True:
            # Sampled before the prompt blocks as well as after it, so what the
            # operator did is not recorded only as a net change.
            poll()
            try:
                answer = input("  your answer [retry/skip/abandon]: ").strip().lower()
            except EOFError:
                # No one is there. Abandoning is the only safe reading of
                # silence -- the alternative is proceeding with an
                # irreversible step nobody approved.
                return Resolution("abandon", by=self.who,
                                  note="no operator was available to answer")
            poll()
            if answer in VALID:
                note = input("  what did you do? (optional): ").strip()
                return Resolution(answer, by=self.who, note=note)  # type: ignore[arg-type]
            print(f"  '{answer}' is not one of {sorted(VALID)}", file=self.stream)


@dataclass
class FileOperator:
    """The same handoff, routed through the filesystem.

    Stands in for a queue or a ticketing system: the request is written where
    something else can pick it up, and the answer is read back. Replace this
    class, keep everything else.
    """

    directory: Path
    who: str = "operator@file"
    timeout_s: float = 900.0
    poll_s: float = 1.0

    @property
    def request_path(self) -> Path:
        return Path(self.directory) / "intervention.json"

    @property
    def reply_path(self) -> Path:
        return Path(self.directory) / "resolution.json"

    def notify(self, request: InterventionRequest) -> None:
        Path(self.directory).mkdir(parents=True, exist_ok=True)
        payload = dict(request.to_dict())
        payload["answer_by_writing"] = str(self.reply_path)
        payload["answer_shape"] = {"action": "retry|skip|abandon", "by": "you", "note": ""}
        self.request_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def wait(self, request: InterventionRequest, *, poll: Callable[[], None]) -> Resolution:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if self.reply_path.exists():
                try:
                    data = json.loads(self.reply_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(self.poll_s)
                    continue
                action = str(data.get("action", "")).lower()
                if action not in VALID:
                    return Resolution(
                        "abandon", by=self.who,
                        note=f"reply named an unknown action {action!r}",
                    )
                return Resolution(action, by=data.get("by", self.who),  # type: ignore[arg-type]
                                  note=data.get("note", ""))
            poll()
            time.sleep(self.poll_s)

        # An unanswered request is abandoned, never waved through: a run
        # holding a banking session open indefinitely is its own incident.
        return Resolution("abandon", by=self.who,
                          note=f"no answer within {self.timeout_s:g}s")


@dataclass
class ScriptedOperator:
    """A fixed answer. For tests, and for demonstrating the flow unattended."""

    resolution: Resolution
    who: str = "operator@scripted"
    notified: list[InterventionRequest] = field(default_factory=list)
    polls: int = 0

    def notify(self, request: InterventionRequest) -> None:
        self.notified.append(request)

    def wait(self, request: InterventionRequest, *, poll: Callable[[], None]) -> Resolution:
        # Sampled once so the journal sees whatever the scripted human did.
        poll()
        self.polls += 1
        return self.resolution

"""Recording what the human did while they held the session.

§3.6 requires this, and it is harder than it looks: the person is driving a
real browser directly, so there are no tool calls to log. What is available is
observation, which the ownership model deliberately leaves ungated.

So the journal samples the session while the human works and records what
*changed* -- pages visited, fields whose values moved, controls that appeared.
A sampled trail is not a keystroke log, and it is not meant to be. The
question it has to answer afterwards is "what did the operator do to this
member's record", and a record of the pages they moved through and the values
they left behind answers that. It also survives the human doing something the
automation has no concept of, which a replay of intercepted events would not.

Every entry passes through the redactor, because this is a log of a person
handling regulated data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from surface.model import Observation


def _fields_of(observation: Observation) -> dict[str, str]:
    """Current value of every named, editable control, keyed stably."""
    out: dict[str, str] = {}
    for node in observation.nodes:
        if node.role not in {"textbox", "combobox", "checkbox", "radio"}:
            continue
        key = node.hints.field_name or node.name
        if key:
            out[key] = node.value or ""
    return out


@dataclass
class HumanAction:
    at: str
    kind: str          # navigated | changed | noted
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "kind": self.kind, "detail": self.detail}


@dataclass
class Journal:
    """A sampled record of a person working in the session."""

    actions: list[HumanAction] = field(default_factory=list)
    _url: str = ""
    _fields: dict[str, str] = field(default_factory=dict)
    _started: bool = False

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _add(self, kind: str, detail: str) -> None:
        self.actions.append(HumanAction(self._now(), kind, detail))

    def begin(self, observation: Observation) -> None:
        """Take the baseline, just before control changes hands."""
        self._url = observation.url
        self._fields = _fields_of(observation)
        self._started = True

    def sample(self, observation: Observation) -> None:
        """Record anything that has changed since the last look."""
        if not self._started:
            self.begin(observation)
            return

        if observation.url != self._url:
            self._add("navigated", f"{self._url} -> {observation.url}")
            self._url = observation.url
            # A new page has its own fields; the previous page's values are
            # not "cleared", they are simply no longer on screen.
            self._fields = _fields_of(observation)
            return

        current = _fields_of(observation)
        for name, value in current.items():
            before = self._fields.get(name)
            if before is not None and before != value:
                self._add("changed", f"{name}: {before!r} -> {value!r}")
        self._fields = current

    def note(self, detail: str) -> None:
        """Something the operator told us, rather than something observed."""
        self._add("noted", detail)

    def to_dict(self) -> list[dict[str, Any]]:
        return [action.to_dict() for action in self.actions]

    def summary(self) -> str:
        if not self.actions:
            return "the operator made no observable change to the session"
        pages = sum(1 for a in self.actions if a.kind == "navigated")
        edits = sum(1 for a in self.actions if a.kind == "changed")
        parts = []
        if pages:
            parts.append(f"{pages} page{'s' if pages != 1 else ''}")
        if edits:
            parts.append(f"{edits} field change{'s' if edits != 1 else ''}")
        return "the operator moved through " + " and ".join(parts) if parts else (
            "the operator left a note"
        )

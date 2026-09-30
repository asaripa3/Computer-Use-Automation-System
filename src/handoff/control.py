"""Who is in control of the session, enforced rather than agreed.

§3.6 asks for "a way to know who is (or should be) in control". A field
holding the answer is easy; the useful part is that the answer is *binding*.

`Supervised` wraps a surface and refuses every acting method while the human
holds it. Without that the ownership is documentation -- a note saying the
automation ought not to click, in a process that is perfectly capable of
clicking. With it, an automation that tries to act during a handoff fails
loudly instead of racing a person who is halfway through typing an amount into
a banking screen.

Perceiving is deliberately *not* gated. While the human drives, the system
still needs to watch: that is how it records what they did, and observing
changes nothing. Ownership governs acting, not looking.

The states are few on purpose:

    automation ──request──> awaiting ──take──> human ──release──> automation
                                │                 │
                                └────abandon──────┴──> abandoned
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from surface.model import Observation, Surface

from .intervention import InterventionRequest, Resolution

AUTOMATION = "automation"
HUMAN = "human"

RUNNING = "running"
AWAITING_HUMAN = "awaiting_human"
HUMAN_DRIVING = "human_driving"
RESUMED = "resumed"
ABANDONED = "abandoned"


class ControlError(RuntimeError):
    """An action was attempted by whoever does not hold the session."""


@dataclass
class Control:
    """Who holds the session, and how it has changed hands."""

    owner: str = AUTOMATION
    status: str = RUNNING
    request: InterventionRequest | None = None
    resolution: Resolution | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    # -- queries -----------------------------------------------------------

    @property
    def automation_may_act(self) -> bool:
        return self.owner == AUTOMATION and self.status in {RUNNING, RESUMED}

    @property
    def held_by_human(self) -> bool:
        return self.owner == HUMAN or self.status == AWAITING_HUMAN

    # -- transitions -------------------------------------------------------

    def _note(self, event: str, **fields: Any) -> None:
        self.history.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event, "owner": self.owner, "status": self.status, **fields,
        })

    def request_intervention(self, request: InterventionRequest) -> None:
        """Automation stops acting the moment the request is raised.

        Not when a human picks it up -- between those two moments nobody is
        driving, and the automation carrying on 'until someone arrives' is
        exactly the window in which it would do the thing it just asked
        permission for.
        """
        if self.status in {AWAITING_HUMAN, HUMAN_DRIVING}:
            raise ControlError("an intervention is already outstanding")
        self.request = request
        self.status = AWAITING_HUMAN
        self._note("requested", intervention=request.id, kind=request.kind,
                   reason=request.reason)

    def hand_over(self) -> None:
        """A human has picked the request up and now holds the session."""
        if self.status != AWAITING_HUMAN:
            raise ControlError(f"nothing is awaiting a human (status {self.status})")
        self.owner = HUMAN
        self.status = HUMAN_DRIVING
        self._note("taken")

    def hand_back(self, resolution: Resolution) -> None:
        if self.status not in {HUMAN_DRIVING, AWAITING_HUMAN}:
            raise ControlError(f"the human does not hold this session ({self.status})")
        self.resolution = resolution
        if resolution.resumes:
            self.owner = AUTOMATION
            self.status = RESUMED
            self._note("returned", action=resolution.action, by=resolution.by,
                       note=resolution.note)
        else:
            self.owner = HUMAN
            self.status = ABANDONED
            self._note("abandoned", by=resolution.by, note=resolution.note)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "status": self.status,
            "intervention": self.request.to_dict() if self.request else None,
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "history": self.history,
        }


class Supervised:
    """A surface that only the current owner may act on.

    Wraps any `Surface`, so a desktop implementation gets the same guarantee
    without knowing this class exists.
    """

    def __init__(self, surface: Surface, control: Control) -> None:
        self._surface = surface
        self.control = control

    # -- perceiving: always permitted -------------------------------------

    @property
    def url(self) -> str:
        return self._surface.url

    def observe(self) -> Observation:
        return self._surface.observe()

    def screenshot(self) -> bytes:
        return self._surface.screenshot()

    # -- acting: only while the automation holds the session --------------

    def _check(self, what: str) -> None:
        if not self.control.automation_may_act:
            holder = "a human operator" if self.control.held_by_human else "nobody"
            raise ControlError(
                f"refusing to {what}: this session is held by {holder} "
                f"(status {self.control.status}). The automation does not act "
                f"while a person has control."
            )

    def goto(self, url: str) -> None:
        self._check(f"navigate to {url}")
        self._surface.goto(url)

    def click(self, ref: str) -> None:
        self._check("click")
        self._surface.click(ref)

    def fill(self, ref: str, text: str) -> None:
        self._check("type")
        self._surface.fill(ref, text)

    def select(self, ref: str, value: str) -> None:
        self._check("select")
        self._surface.select(ref, value)

    def press(self, ref: str, key: str) -> None:
        self._check("press a key")
        self._surface.press(ref, key)

    def close(self) -> None:
        self._surface.close()

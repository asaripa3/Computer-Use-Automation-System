"""What gets routed to a human, and what they send back.

§3.6 asks that an intervention request carry enough context to act on: which
capability and goal, the current step, the current state or a screenshot, and
why it stopped. The shape below is that list, plus one field the brief does
not ask for and which turns out to matter most in practice -- `what_to_do`.

An operator receiving "step 11 needs authorisation" has to reconstruct what
the run was trying to achieve before they can decide anything. An operator
receiving "the sub-account form is filled in and waiting on the review screen;
confirm it if the member requested this, otherwise abandon" can act in
seconds. The system knows which it is, so it says so.

The reply is equally deliberate. A human does not simply say "done" -- they
say what they did, and the two cases differ:

    retry    "I fixed the situation; try that step again."
    skip     "I did that step myself; carry on from the next one."
    abandon  "This should not continue."

`skip` is the interesting one, because the automation does not take it on
trust. The step's checkpoint is still evaluated afterwards, so a human who
believes they completed something they did not gets a failed checkpoint rather
than a run that quietly carries on from a state nobody verified.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# Why a human is being asked. Each maps to a different thing they must decide,
# which is the only reason to distinguish them.
KINDS = (
    "authorization_required",  # an irreversible step needs a person to approve
    "target_unresolvable",     # the recorded control is not on the page
    "checkpoint_failed",       # the page is not what the artifact expected
    "session_lost",            # authentication expired and cannot be recovered
    "policy_blocked",          # a guardrail stopped the run
    "model_stuck",             # a discovery run could not proceed
)

Action = Literal["retry", "skip", "abandon"]


@dataclass(frozen=True)
class InterventionRequest:
    """A request for a person to take over, with what they need to act."""

    kind: str
    reason: str
    what_to_do: str
    capability_ref: str = ""
    goal: str = ""
    step_index: int | None = None
    step_description: str = ""
    url: str = ""
    tenant: str | None = None
    evidence_dir: str | None = None
    screenshot: str | None = None
    perceived: str | None = None
    id: str = field(default_factory=lambda: f"int_{uuid.uuid4().hex[:10]}")
    raised_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "raised_at": self.raised_at,
            "kind": self.kind,
            "reason": self.reason,
            "what_to_do": self.what_to_do,
            "capability": self.capability_ref,
            "goal": self.goal,
            "step_index": self.step_index,
            "step_description": self.step_description,
            "url": self.url,
            "tenant": self.tenant,
            "evidence_dir": self.evidence_dir,
            "screenshot": self.screenshot,
            "perceived": self.perceived,
        }

    def summary(self) -> str:
        where = f"step {self.step_index}" if self.step_index is not None else "the run"
        return f"{self.capability_ref}: {where} needs a person -- {self.reason}"


@dataclass(frozen=True)
class Resolution:
    """What the human decided, and what they say they did."""

    action: Action
    by: str = "operator"
    note: str = ""
    decided_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def resumes(self) -> bool:
        return self.action in {"retry", "skip"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "by": self.by,
            "note": self.note, "decided_at": self.decided_at,
        }


def what_to_do_for(kind: str, step_description: str, capability_ref: str) -> str:
    """Plain instructions for whoever picks this up.

    Written for someone who did not see the run start and has a queue of these
    to get through.
    """
    if kind == "authorization_required":
        return (
            f"The run has completed every step up to this one and is waiting on "
            f"the screen where it would {step_description.rstrip('.').lower()}. "
            f"Check the details on screen are what was intended. Confirm it "
            f"yourself and reply 'skip', or reply 'retry' to let the automation "
            f"do it now that you have seen it. Reply 'abandon' if it should not "
            f"happen. If you do it yourself, answer while the confirmation is "
            f"still on screen -- that screen is how the run knows it worked, "
            f"and navigating away first leaves it unable to tell."
        )
    if kind == "target_unresolvable":
        return (
            f"The automation cannot find the control it needs for: "
            f"{step_description} It may have moved, or the page may not be the "
            f"one expected. Put the session on the right screen and reply "
            f"'retry', or carry out the step yourself and reply 'skip'."
        )
    if kind == "checkpoint_failed":
        return (
            f"After {step_description} the page is not what {capability_ref} "
            f"expected. Have a look, put things right if you can, and reply "
            f"'retry' -- or 'abandon' if this needs investigating."
        )
    if kind == "session_lost":
        return (
            "The session expired and the automation has no way to sign back in. "
            "Sign on in the browser window and reply 'retry'."
        )
    if kind == "policy_blocked":
        return (
            "A guardrail stopped this run. It cannot be waved through from here "
            "-- the policy has to change, and that is a separate decision. "
            "Reply 'abandon'."
        )
    return (
        "The automation could not proceed. Take a look at the session and "
        "either put it right and reply 'retry', or reply 'abandon'."
    )

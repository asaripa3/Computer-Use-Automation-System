"""What to do about a step that cannot be undone.

§3.4 asks for risky and irreversible actions to be handled conservatively and
for the choice to be defended. The choice here is **neither block nor
auto-confirm, but escalate**.

Blocking outright would make half the useful capabilities in a back-office
system unbuildable: opening an account, posting a transaction and releasing a
hold are the work, not an edge case. Auto-confirming on a flag reduces the
guardrail to a boolean somebody sets once in a config file and never revisits.

So an irreversible step needs an authorisation that names who granted it and
for which capability, and in its absence the run does not fail -- it pauses
and asks a human, through the same escalation path as any other stuck state.
That keeps the dangerous case on a route that already carries context,
evidence and a record of who decided what, instead of inventing a second,
weaker approval mechanism beside it.

Three further constraints fall out of that, all enforced here:

* An authorisation is for one capability at one version. Approving
  "open a sub-account" v1.0.0 does not approve v1.1.0, because the thing that
  was reviewed has changed.
* A draft capability is never authorised unattended, whatever the flag says.
* The schema already forbids two irreversible steps in one capability, so
  there is exactly one decision point to reason about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .allowlist import Allowlist

Disposition = Literal["proceed", "escalate", "block"]


@dataclass(frozen=True)
class Authorization:
    """A human's decision to permit one capability's irreversible step."""

    capability_ref: str  # "id@version" -- deliberately version-pinned
    granted_by: str
    reason: str = ""

    def covers(self, capability) -> bool:
        return self.capability_ref == capability.ref


@dataclass(frozen=True)
class RiskDecision:
    disposition: Disposition
    reason: str

    @property
    def may_proceed(self) -> bool:
        return self.disposition == "proceed"

    @property
    def needs_human(self) -> bool:
        return self.disposition == "escalate"


def decide(
    step,
    capability,
    *,
    policy: Allowlist,
    authorization: Authorization | None = None,
) -> RiskDecision:
    """Whether ``step`` may run now."""
    if not step.is_irreversible:
        return RiskDecision("proceed", "step is reversible")

    if capability.status != "approved":
        return RiskDecision(
            "block",
            f"capability {capability.ref} is {capability.status}; an "
            f"irreversible step may not run from an unapproved capability",
        )

    if not policy.allow_irreversible:
        return RiskDecision(
            "escalate",
            f"policy {policy.label!r} does not permit unattended irreversible "
            f"actions; step {step.index} ({step.description!r}) needs a human",
        )

    if authorization is None:
        return RiskDecision(
            "escalate",
            f"step {step.index} ({step.description!r}) is irreversible and no "
            f"authorization was supplied for this invocation",
        )

    if not authorization.covers(capability):
        return RiskDecision(
            "block",
            f"authorization is for {authorization.capability_ref}, not "
            f"{capability.ref}; a capability that has changed since it was "
            f"approved has not been approved",
        )

    return RiskDecision(
        "proceed",
        f"authorized by {authorization.granted_by} for {capability.ref}",
    )

"""Running one handoff, start to finish.

The sequence, and why it is in this order:

1. **Capture the state first.** A redacted page snapshot is taken before
   anyone is told, because once a human starts working the state the request
   describes is gone. Pixel screenshots are not persisted.
2. **Stop the automation.** Ownership changes the moment the request is
   raised, not when someone picks it up. Between those two moments nobody is
   driving, and an automation that carried on "until someone arrives" would
   do the very thing it just asked permission for.
3. **Route it**, and wait -- sampling the session throughout, which is how the
   journal records what the person did.
4. **Take the session back**, verify, and record everything that happened.

The invariant worth stating: control returns to the automation only through
`hand_back`, and `Supervised` refuses to act until it has. There is no path
where both are driving.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .control import Control, Supervised
from .intervention import InterventionRequest, Resolution, what_to_do_for
from .journal import Journal
from .operator import Operator
from policy.redaction import Redactor
from surface.view import render


@dataclass
class Handoff:
    """The record of one transfer of control."""

    request: InterventionRequest
    resolution: Resolution
    journal: Journal
    returned_url: str = ""
    # The state at which each watched condition first held, keyed by name.
    # A confirmation screen is often both the only proof an irreversible step
    # took effect *and* the only place its outputs appear -- and operators
    # navigate on from it. Keeping the observation means the proof and the
    # values survive them moving on.
    proof: dict = field(default_factory=dict)

    @property
    def checkpoint_seen(self) -> bool:
        return "step" in self.proof

    @property
    def resumed(self) -> bool:
        return self.resolution.resumes

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "resolution": self.resolution.to_dict(),
            "human_actions": self.journal.to_dict(),
            "returned_on": self.returned_url,
            "verified": sorted(self.proof),
        }


@dataclass
class Coordinator:
    """Routes interventions for one run and records what came back."""

    operator: Operator
    control: Control = field(default_factory=Control)
    evidence: Any = None
    redactor: Redactor | None = None
    handoffs: list[Handoff] = field(default_factory=list)

    def escalate(
        self,
        surface: Supervised,
        *,
        kind: str,
        reason: str,
        capability_ref: str = "",
        goal: str = "",
        step_index: int | None = None,
        step_description: str = "",
        tenant: str | None = None,
        watch_for=None,
        inputs: dict | None = None,
    ) -> Handoff:
        """Hand the live session to a person, and take it back afterwards."""
        # 1. The state, before anyone touches it.
        observation = surface.observe()
        perceived = render(observation, reveal=False)

        def scrub(value: str | None) -> str | None:
            return self.redactor.text(value) if value and self.redactor else value

        request = InterventionRequest(
            kind=kind,
            reason=scrub(reason) or "",
            what_to_do=scrub(what_to_do_for(kind, step_description, capability_ref)) or "",
            capability_ref=capability_ref,
            goal=scrub(goal) or "",
            step_index=step_index,
            step_description=scrub(step_description) or "",
            url=scrub(observation.url) or "",
            tenant=tenant,
            evidence_dir=str(self.evidence.directory) if self.evidence else None,
            # A screenshot can contain unstructured PII that cannot be
            # reliably redacted after capture. The same live browser remains
            # available to the operator; persist only the redacted structure.
            screenshot=None,
            perceived=scrub(perceived),
        )

        # 2. The automation stops here, not when someone answers.
        self.control.request_intervention(request)
        if self.evidence:
            self.evidence.note("intervention_raised", **request.to_dict())

        journal = Journal()
        journal.begin(observation)
        proof: dict = {}

        def look(state) -> None:
            """Note the first state in which each watched condition holds."""
            if not watch_for:
                return
            from replay.conditions import holds

            for name, condition in watch_for.items():
                if name in proof or condition is None:
                    continue
                if holds(condition, state, inputs=inputs):
                    proof[name] = state

        # 3. Route it, and watch while they work.
        self.operator.notify(request)
        self.control.hand_over()

        def sample() -> None:
            state = surface.observe()
            journal.sample(state)
            look(state)

        resolution = self.operator.wait(request, poll=sample)

        # A last look, so anything done just before answering is recorded.
        final = surface.observe()
        journal.sample(final)
        look(final)
        if resolution.note:
            journal.note(resolution.note)

        # 4. Back to the automation -- or not.
        self.control.hand_back(resolution)

        handoff = Handoff(
            request=request,
            resolution=resolution,
            journal=journal,
            returned_url=surface.url,
            proof=proof,
        )
        self.handoffs.append(handoff)

        if self.evidence:
            self.evidence.note(
                "intervention_resolved",
                intervention=request.id,
                action=resolution.action,
                by=resolution.by,
                note=resolution.note,
                human_actions=journal.to_dict(),
                summary=journal.summary(),
                returned_on=handoff.returned_url,
                verified=sorted(handoff.proof),
            )

        return handoff

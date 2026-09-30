"""Compiling a discovery run into a capability artifact.

The through-line of the whole project lands here: *the model discovers, the
artifact becomes a reusable capability.* What the model did is a transcript;
what this produces is a contract.

The important property is that the artifact is built from **observations, not
from the transcript**. When the model fills a field, the recorder takes the
node the surface layer perceived and asks `contract.derive.locator_for` for
every way of finding it again, ordered by trustworthiness. The model never
chooses a selector and never sees one -- it points at a control, and the
locator ladder is derived from what was actually on the page.

That separation is what §3.2 means by an artifact decoupled from the raw model
transcript. The transcript is kept as evidence; the capability owes it nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from contract.capability import (
    Capability, Condition, InputSpec, OutcomeSpec, OutputSpec, Provenance,
    RecoverySpec, Step, SurfaceSpec,
)
from contract.derive import locator_for
from contract.locator import DERIVED, Locator, LocatorCandidate
from contract.values import Value
from surface.model import Node


@dataclass
class Recorder:
    """Accumulates a run, then emits a capability."""

    capability_id: str
    version: str
    goal: str
    base_url: str
    entry_path: str = "/"
    recorded_by: str = ""
    app_fingerprint: str = ""
    recoveries: tuple[RecoverySpec, ...] = ()

    steps: list[Step] = field(default_factory=list)
    inputs: list[InputSpec] = field(default_factory=list)
    outputs: list[OutputSpec] = field(default_factory=list)
    outcomes: list[OutcomeSpec] = field(default_factory=list)
    success: Condition | None = None
    summary: str = ""
    title: str = ""

    # -- declarations ------------------------------------------------------

    def declared_input(self, args: dict[str, Any]) -> None:
        self.inputs = [i for i in self.inputs if i.name != args["name"]]
        self.inputs.append(InputSpec(
            name=args["name"],
            type=args["type"],
            description=args["description"],
            pattern=args.get("pattern") or None,
            sensitivity=args.get("sensitivity") or "none",
            example=args.get("example"),
        ))

    def example_for(self, name: str | None) -> str | None:
        """The value to actually type for a declared input.

        Discovery drives the application with the declared example, so the run
        exercises the same shape a caller will later supply.
        """
        for spec in self.inputs:
            if spec.name == name:
                return spec.example
        return None

    def declared_output(self, args: dict[str, Any], node: Node | None) -> None:
        source = self._output_locator(args, node)
        self.outputs = [o for o in self.outputs if o.name != args["name"]]
        self.outputs.append(OutputSpec(
            name=args["name"],
            type=args["type"],
            description=args["description"],
            source=source,
            sensitivity=args.get("sensitivity") or "none",
            optional=bool(args.get("optional")),
        ))

    def _output_locator(self, args: dict[str, Any], node: Node | None) -> Locator:
        column = args.get("column")
        if column:
            key_value = None
            if args.get("key_from_input"):
                key_value = Value(from_input=args["key_from_input"])
            elif args.get("key_value") is not None:
                key_value = Value(literal=args["key_value"])
            return Locator(
                description=(
                    f'the {column} cell of the row where {args["key_column"]} '
                    f"identifies the record"
                    if args.get("key_column") else f"the {column} cell"
                ),
                frame="contentFrame",
                candidates=(LocatorCandidate(
                    strategy="grid_cell",
                    robustness=DERIVED,
                    column=column,
                    key_column=args.get("key_column"),
                    key_value=key_value,
                    rationale=(
                        "addressed by column and a key in another column, so it "
                        "survives the row moving when the record has a different "
                        "number of rows"
                    ),
                ),),
            )

        if node is None:
            raise ValueError(
                f"output {args['name']!r} needs either a ref or a column"
            )
        return locator_for(node, description=f"the {args['name']} value on screen")

    def noted_outcome(self, args: dict[str, Any]) -> None:
        code = args["code"].upper()
        if any(o.code == code for o in self.outcomes):
            return
        self.outcomes.append(OutcomeSpec(
            code=code,
            kind=args["kind"],
            description=args["description"],
            detect=Condition("text_present", f"the {code} banner", text=args["text"]),
        ))

    def expected(self, description: str, text: str) -> None:
        """Attach a checkpoint to the step just taken."""
        if not self.steps:
            return
        last = self.steps[-1]
        self.steps[-1] = Step(
            index=last.index, action=last.action, description=last.description,
            target=last.target, value=last.value, url=last.url, risk=last.risk,
            expect=Condition("text_present", description, text=text),
            timeout_ms=last.timeout_ms,
        )

    def finished(self, args: dict[str, Any]) -> None:
        self.success = Condition(
            "text_present", args["success_description"], text=args["success_text"]
        )
        self.summary = args.get("summary", "")

    # -- actions -----------------------------------------------------------

    def discard_steps(self) -> int:
        """Forget the steps taken so far, keeping what was learned.

        A real run explores before it performs: checking what an empty result
        looks like, backing out of a wrong page. Those detours are how the
        model learns, and they have no business in the capability -- replaying
        them would make every invocation repeat somebody else's wrong turns.

        Declarations survive deliberately. An outcome observed while exploring
        is exactly the knowledge worth keeping.
        """
        dropped = len(self.steps)
        self.steps = []
        return dropped

    def navigated(self, path: str) -> None:
        self.steps.append(Step(
            index=len(self.steps) + 1,
            action="navigate",
            description=f"Go to {path}.",
            url=path,
        ))

    def _input_matching(self, node: Node) -> InputSpec | None:
        """A declared input whose example is exactly what this control shows."""
        text = (node.text or node.name or "").strip()
        if not text:
            return None
        for spec in self.inputs:
            if spec.example and spec.example.strip() == text:
                return spec
        return None

    def _locator_for(self, node: Node, described: str) -> Locator:
        """How to find this control again -- parameterised where it must be.

        The case that forces this: opening a member's record means clicking
        the row whose member number matches. Recorded literally, that control
        is named "12345", and the capability would only ever work for member
        12345. Since the run already declared that value as an input, the cell
        is recorded as *the row where this column matches the supplied input*
        instead -- concrete value in, parameterised pattern out.

        The same reasoning applies to a route like /member/12345, and the same
        substitution would apply there.
        """
        matched = self._input_matching(node)
        if matched and node.table is not None and node.table.column_header:
            column = node.table.column_header
            frame = (
                node.frame_path[-1]
                if node.frame_path and node.frame_path[-1] != "main" else None
            )
            return Locator(
                description=(
                    f"the {column} cell of the row matching the supplied "
                    f"{matched.name}"
                ),
                frame=frame,
                candidates=(LocatorCandidate(
                    strategy="grid_cell",
                    robustness=DERIVED,
                    column=column,
                    key_column=column,
                    key_value=Value(from_input=matched.name),
                    rationale=(
                        f"the run typed {matched.name} to get here, so the row "
                        f"is addressed by that input rather than by the one "
                        f"value this recording happened to use"
                    ),
                ),),
            )
        return locator_for(node, description=described)

    def acted(
        self,
        action: str,
        node: Node,
        *,
        literal: str | None = None,
        from_input: str | None = None,
        irreversible: bool = False,
    ) -> None:
        described = node.name or node.describe()
        verb = {"click": "Click", "fill": "Enter", "select": "Choose"}[action]

        value = None
        if from_input:
            value = Value(from_input=from_input)
        elif literal is not None:
            value = Value(literal=literal)

        self.steps.append(Step(
            index=len(self.steps) + 1,
            action=action,
            description=f"{verb} {described}.",
            # The ladder comes from what the surface layer perceived, not from
            # anything the model chose. The model pointed; this decides how to
            # find that control again.
            target=self._locator_for(node, f"the {described} control"),
            value=value,
            risk="irreversible" if irreversible else "safe",
            # A commit posts to the core and waits on it; the default is
            # generous for a page load and not for a transaction.
            timeout_ms=30_000 if irreversible else None,
        ))

    # -- emitting ----------------------------------------------------------

    @property
    def is_complete(self) -> bool:
        return self.success is not None and bool(self.steps)

    def missing(self) -> list[str]:
        """What still stops this run becoming a valid capability."""
        problems: list[str] = []
        if not self.steps:
            problems.append("no steps were taken")
        if self.success is None:
            problems.append("finish was never called, so there is no success condition")
        if not self.outcomes:
            problems.append(
                "no non-success outcome was recorded; a capability that can only "
                "succeed or crash cannot report a business answer"
            )
        declared = {i.name for i in self.inputs}
        for step in self.steps:
            for name in sorted(step.inputs_used):
                if name not in declared:
                    problems.append(f"step {step.index} uses undeclared input {name!r}")
        return problems

    def _steps_from_a_known_start(self) -> tuple[Step, ...]:
        """Ensure the flow begins by establishing where it starts.

        A discovery run begins wherever the harness put the browser, so the
        recorded steps often start mid-flow. Replayed that way the capability
        would act on whatever page the caller's session happened to be
        showing. A capability that does not state its own starting point is
        not replayable, so one is prepended when the run did not record it.
        """
        if self.steps and self.steps[0].action == "navigate":
            return tuple(self.steps)

        opening = Step(
            index=1, action="navigate",
            description=f"Go to {self.entry_path}.",
            url=self.entry_path,
        )
        renumbered = [
            replace(step, index=step.index + 1) for step in self.steps
        ]
        return (opening, *renumbered)

    def bakes_in_a_parameter(self, text: str) -> str | None:
        """The declared input whose value appears in ``text``, if any.

        A checkpoint reading "1 record(s) returned for member number 12345" is
        a real thing a model records: it is true of the run it just watched,
        and false of every other invocation. The same goes for an outcome
        detector. Because the run has already declared what varies, this is
        mechanically checkable rather than a matter of hoping the model was
        careful -- so it is checked.
        """
        haystack = (text or "").casefold()
        for spec in self.inputs:
            value = (spec.example or "").strip()
            # Very short values appear coincidentally; a member number does not.
            if len(value) >= 3 and value.casefold() in haystack:
                return spec.name
        return None

    def blocking_finish(self) -> list[str]:
        """What must be fixed before the run may be declared complete.

        Everything `missing()` reports except the absence of a finish itself,
        since that is what is being attempted. Consulted by the `finish` tool
        so the model is told what is still outstanding while it can still act
        on it -- rather than the run ending and the artifact being refused
        afterwards, when nobody is left to fix it.
        """
        return [p for p in self.missing() if "finish was never called" not in p]

    def build(self, *, title: str = "", description: str = "",
              status: str = "draft") -> Capability:
        """Emit the capability. Raises if the run did not produce a valid one.

        A discovery run is only useful if it produced something replayable, so
        the same validation a hand-written artifact faces is applied here --
        the recorder gets no special dispensation.
        """
        problems = self.missing()
        if problems:
            raise ValueError(
                "this run did not produce a usable capability:\n  - "
                + "\n  - ".join(problems)
            )

        assert self.success is not None

        steps = self._steps_from_a_known_start()

        return Capability(
            id=self.capability_id,
            version=self.version,
            # Recorded runs start as drafts. An irreversible step may not run
            # from an unapproved capability, so nothing the model produced can
            # commit anything until a person has read it.
            status=status,
            title=title or self.title or self.goal,
            description=description or self.summary or self.goal,
            surface=SurfaceSpec(
                kind="web",
                entry_url=self.base_url.rstrip("/") + self.entry_path,
                requires_origins=(self.base_url.rstrip("/"),),
                app_fingerprint=self.app_fingerprint,
            ),
            inputs=tuple(self.inputs),
            outputs=tuple(self.outputs),
            steps=steps,
            success=self.success,
            outcomes=tuple(self.outcomes),
            recoveries=self.recoveries,
            provenance=Provenance(
                recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                recorded_by=self.recorded_by,
                goal=self.goal,
            ),
        )

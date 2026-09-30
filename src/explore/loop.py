"""The observe → decide → act loop.

Small on purpose. The loop asks the model what to do, does it, hands back what
happened, and stops when the goal is reached, the model gives up, or the
budget runs out. Everything interesting lives on either side of it: the
surface layer decides what "observe" means, the recorder decides what the run
*was*, and the policy decides what is allowed.

Two things the loop owns that are worth naming.

**Every decision is written down with its reasoning.** §3.5 asks for a log of
what the agent did *and why*; the model's own text before each tool call is
the why, and it is recorded verbatim. It is also what makes the run
replayable later without a model.

**The budget is a stopping condition, not a suggestion.** A loop that can run
forever against a banking application is not a loop, it is an outage waiting
for a cause.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract.capability import Capability
from policy.allowlist import Allowlist
from surface.model import Surface

from .model import ModelProvider, ModelTurn
from .prompt import SYSTEM, goal_message
from .recorder import Recorder
from policy.redaction import Redactor

from .tools import (
    Session, ToolCall, ToolResult, descriptor_for, execute, tool_schemas,
)

DEFAULT_MAX_STEPS = 30
# A wall-clock bound as well as a step bound. A model that keeps choosing
# cheap actions can burn a long time inside a small step budget, and a run
# against a banking application should not be able to do that unattended.
DEFAULT_TIME_LIMIT_S = 300.0


@dataclass
class DiscoveryRun:
    goal: str
    model: str
    turns: int
    stopped_because: str
    capability: Capability | None = None
    elapsed_s: float = 0.0
    summary: str = ""
    give_up_reason: str = ""
    problems: list[str] = field(default_factory=list)
    transcript: Path | None = None

    @property
    def ok(self) -> bool:
        return self.capability is not None


class Transcript:
    """The model's decisions, in a form a later run can replay."""

    def __init__(self, path: Path, model: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write({"event": "start", "model": model,
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def _write(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def turn(self, turn: ModelTurn, targets: dict[str, dict[str, Any]]) -> None:
        """Record a decision in a form a later run can re-issue.

        Refs are replaced by a description of the control they pointed at.
        A ref is valid for one observation only, so a transcript of refs
        replays into nothing; a transcript of descriptions replays into the
        same decisions against a freshly rendered page.
        """
        record = turn.to_record()
        for call in record["calls"]:
            described = targets.get(call["id"])
            if described is None:
                continue
            arguments = dict(call["arguments"])
            arguments.pop("ref", None)
            arguments["target"] = described
            call["arguments"] = arguments
        self._write({"event": "turn", **record})

    def results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        """Whether each call worked -- deliberately not what the page said.

        A replayed run re-executes the tools against the live application, so
        the page content is never read back from here. Recording it would put
        member data into a file that exists to be attached to tickets, which
        §3.4 forbids. When a call fails the message is ours, not the
        application's, so it is safe and useful to keep.
        """
        self._write({
            "event": "results",
            "results": [
                {
                    "call": call.name,
                    "is_error": result.is_error,
                    **({"error": result.text[:300]} if result.is_error else {}),
                }
                for call, result in results
            ],
        })


def _sync_redaction(redactor: Redactor, recorder: Recorder) -> None:
    """Teach the redactor what the run has declared so far."""
    for spec in (*recorder.inputs, *recorder.outputs):
        redactor.sensitivity[spec.name] = spec.sensitivity
    for spec in recorder.inputs:
        if spec.example:
            redactor.learn(spec.name, spec.example)


def discover(
    goal: str,
    surface: Surface,
    *,
    provider: ModelProvider,
    policy: Allowlist,
    base: str,
    entry_path: str = "/",
    capability_id: str = "recorded.capability",
    version: str = "0.1.0",
    app_fingerprint: str = "",
    recoveries=(),
    max_steps: int = DEFAULT_MAX_STEPS,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    transcript: Path | None = None,
    evidence=None,
    session: Session | None = None,
) -> DiscoveryRun:
    """Drive the application toward ``goal``, recording a capability.

    ``session`` may be supplied by a caller that needs to hold the same view
    of the page the loop has -- a harness resolving refs on the model's
    behalf, for instance. Left unset, the loop makes its own.
    """
    recorder = Recorder(
        capability_id=capability_id,
        version=version,
        goal=goal,
        base_url=base,
        entry_path=entry_path,
        recorded_by=provider.name,
        app_fingerprint=app_fingerprint,
        recoveries=tuple(recoveries),
    )
    session = session or Session(surface=surface, policy=policy, base=base)
    log = Transcript(transcript, provider.name) if transcript else None

    # Discovery cannot know what is sensitive before the model says so, so the
    # redactor is rebuilt from the declarations after every turn and applied
    # to everything written from then on.
    redactor = Redactor()
    if evidence:
        evidence.redactor = redactor

    provider.start(SYSTEM, goal_message(goal, entry_path, max_steps), tool_schemas())

    stopped = "the step budget ran out"
    deadline = time.monotonic() + time_limit_s
    turns = 0
    idle = 0

    for turns in range(1, max_steps + 1):
        if time.monotonic() >= deadline:
            stopped = f"the run exceeded its {time_limit_s:g}s time limit"
            break

        turn = provider.decide()

        if evidence:
            evidence.note("decision", turn=turns, reasoning=turn.text,
                          tools=[c.name for c in turn.calls])

        if not turn.calls:
            # A turn with nothing to do twice running means the model has
            # stopped making progress; continuing would only burn budget.
            idle += 1
            if idle >= 2:
                stopped = "the model stopped choosing actions"
                break
            provider.record_results([])
            continue
        idle = 0

        results: list[tuple[ToolCall, ToolResult]] = []
        targets: dict[str, dict[str, Any]] = {}
        for call in turn.calls:
            result = execute(call, session, recorder)
            results.append((call, result))
            if result.node is not None:
                targets[call.id] = descriptor_for(result.node)
            if evidence:
                # The page the model was looking at is not written down: it is
                # member data, and evidence is the artefact most likely to be
                # copied into a ticket. What it did and why is what matters.
                evidence.note(
                    "action", turn=turns, tool=call.name,
                    arguments=call.arguments, is_error=result.is_error,
                    **({"error": result.text[:300]} if result.is_error else {}),
                )

        _sync_redaction(redactor, recorder)

        provider.record_results(results)
        if log:
            log.turn(turn, targets)
            log.results(results)

        if session.finished:
            stopped = "the model reported the goal was reached"
            break
        if session.gave_up:
            stopped = "the model gave up"
            break

    problems = recorder.missing()
    capability = None
    if not problems and not session.gave_up:
        capability = recorder.build()

    return DiscoveryRun(
        goal=goal,
        model=provider.name,
        turns=turns,
        elapsed_s=round(time_limit_s - (deadline - time.monotonic()), 1),
        stopped_because=stopped,
        capability=capability,
        summary=session.summary,
        give_up_reason=session.give_up_reason,
        problems=problems,
        transcript=log.path if log else None,
    )

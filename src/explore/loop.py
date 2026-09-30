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
from .tools import (
    Session, ToolCall, ToolResult, descriptor_for, execute, tool_schemas,
)

DEFAULT_MAX_STEPS = 30


@dataclass
class DiscoveryRun:
    goal: str
    model: str
    turns: int
    stopped_because: str
    capability: Capability | None = None
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
        # Recorded for evidence only; a replayed run re-executes the tools
        # against the live application rather than reusing these.
        self._write({
            "event": "results",
            "results": [
                {"call": call.name, "is_error": result.is_error,
                 "text": result.text[:400]}
                for call, result in results
            ],
        })


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

    provider.start(SYSTEM, goal_message(goal, entry_path, max_steps), tool_schemas())

    stopped = "the step budget ran out"
    turns = 0
    idle = 0

    for turns in range(1, max_steps + 1):
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
                evidence.note("action", turn=turns, tool=call.name,
                              arguments=call.arguments, is_error=result.is_error,
                              result=result.text[:300])

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
        stopped_because=stopped,
        capability=capability,
        summary=session.summary,
        give_up_reason=session.give_up_reason,
        problems=problems,
        transcript=log.path if log else None,
    )

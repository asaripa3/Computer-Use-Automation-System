"""A scripted stand-in for the model.

The loop, the tool surface and the recorder are all testable without an API
key, because none of them depend on a model being *intelligent* -- only on it
issuing tool calls. A scripted provider issues a fixed sequence against the
real application, which is enough to prove that a discovery run produces a
capability that actually replays.

What this deliberately does not test is whether a real model makes good
choices. That needs a real run, and there is one in evidence/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from explore.model import ModelTurn
from explore.tools import Session, ToolCall, ToolResult

# Each intent is given the live session, so it can look up refs on whatever is
# currently on screen -- exactly as a model would, reading the rendered page.
Intent = Callable[[Session], ModelTurn]


@dataclass
class ScriptedProvider:
    """Replays a fixed sequence of tool calls against the live application."""

    session: Session
    intents: list[Intent]
    label: str = "scripted"
    _index: int = 0
    seen: list[tuple[ToolCall, ToolResult]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.label

    def start(self, system: str, goal: str, tools: list[dict[str, Any]]) -> None:
        self.system = system
        self.goal = goal
        self.tools = tools

    def decide(self) -> ModelTurn:
        if self._index >= len(self.intents):
            return ModelTurn(text="nothing further to do", calls=[])
        intent = self.intents[self._index]
        self._index += 1
        return intent(self.session)

    def record_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        self.seen.extend(results)


_counter = iter(range(1, 10_000))


def call(tool: str, /, **arguments: Any) -> ToolCall:
    """Build a tool call.

    The tool name is positional-only so that a tool argument genuinely called
    `name` -- declare_input has one -- does not collide with it.
    """
    return ToolCall(id=f"call_{next(_counter)}", name=tool, arguments=arguments)


def turn(text: str, *calls: ToolCall) -> ModelTurn:
    return ModelTurn(text=text, calls=list(calls))


def ref_of(session: Session, *, role: str, name: str) -> str:
    """The ref of a named control on the page as it stands."""
    found = session.observation.find(role=role, name=name)
    assert found, f"no {role} named {name!r} on {session.observation.url}"
    return found[0].ref


def field_ref(session: Session, label: str) -> str:
    found = session.observation.field(label)
    assert found is not None, f"no field labelled {label!r}"
    return found.ref


def row_ref(session: Session, *, column: str, key_column: str, key_value: str) -> str:
    found = session.observation.cell(
        column=column, where_column=key_column, where_value=key_value
    )
    assert found is not None, f"no row where {key_column} is {key_value!r}"
    return found.ref

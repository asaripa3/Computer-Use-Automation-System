"""The model behind the discovery loop, and how to run without one.

Two providers implement the same small protocol:

`OpenAIProvider` drives a real model. Provider choice is explicitly left open
by the brief, and the loop is written against this protocol rather than
against any vendor's SDK -- the conversation format, the tool-calling shape
and the retry behaviour all live behind it. Swapping vendor means adding a
sibling class here and nothing else moves.

`TranscriptProvider` replays the decisions a previous run made, from the
transcript that run recorded. That is not a mock: the same tools execute
against the same live application and the same artifact comes out the other
end. It exists so a reviewer without an API key can still watch the discovery
loop work end to end, and so a discovery run is reproducible rather than being
a thing that happened once.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol

from .tools import ToolCall, ToolResult

FALLBACK_MODEL = "gpt-4.1"


def default_model() -> str:
    """The model to use, resolved when asked rather than at import.

    A module-level constant would be evaluated before the CLI loads .env,
    which would silently ignore OPENAI_MODEL set there -- the same trap that
    made a key in .env invisible.
    """
    return os.environ.get("OPENAI_MODEL") or FALLBACK_MODEL


@dataclass
class ModelTurn:
    """One decision: what the model said, and what it wants to do."""

    text: str
    calls: list[ToolCall] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.calls
            ],
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "ModelTurn":
        return cls(
            text=data.get("text", ""),
            calls=[
                ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
                for c in data.get("calls") or []
            ],
        )


class ModelProvider(Protocol):
    def start(self, system: str, goal: str, tools: list[dict[str, Any]]) -> None: ...

    def decide(self) -> ModelTurn: ...

    def record_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None: ...

    @property
    def name(self) -> str: ...


class NoModelAvailable(RuntimeError):
    """No API key, and no transcript to replay instead."""


# -- a real model ----------------------------------------------------------

class OpenAIProvider:
    """Drives the loop with an OpenAI chat model using tool calling."""

    def __init__(self, model: str | None = None, *, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise NoModelAvailable(
                "OPENAI_API_KEY is not set. Either export it, or replay a "
                "recorded run with --transcript to see the loop work without "
                "an API call."
            )
        from openai import OpenAI

        self._client = OpenAI(api_key=key)
        self._model = model or default_model()
        self._messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    def start(self, system: str, goal: str, tools: list[dict[str, Any]]) -> None:
        # The neutral tool schemas are adapted here, so nothing above this
        # module knows what shape a particular vendor wants them in.
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]
        self._messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": goal},
        ]

    def decide(self) -> ModelTurn:
        # The SDK types these as TypedDicts; building them as plain dicts is
        # the ordinary pattern and keeps the neutral schemas above untouched.
        response = self._client.chat.completions.create(
            model=self._model,
            messages=self._messages,  # type: ignore[arg-type]
            tools=self._tools,  # type: ignore[arg-type]
            tool_choice="auto",
            temperature=0,  # discovery should be as repeatable as the model allows
        )
        message = response.choices[0].message

        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=json.loads(call.function.arguments or "{}"),
            )
            for call in (message.tool_calls or [])
        ]

        # Kept verbatim so the tool results can be attached to the right call.
        self._messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in (message.tool_calls or [])
            ] or None,
        })

        return ModelTurn(text=message.content or "", calls=calls)

    def record_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        for call, result in results:
            self._messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result.text,
            })


# -- replaying a recorded run ---------------------------------------------

class TranscriptProvider:
    """Replays the decisions a previous discovery run made.

    The tools still execute for real against the live application, so this
    exercises everything except the network call to the model.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        records = [
            json.loads(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self._turns: Iterator[dict[str, Any]] = iter(
            [r for r in records if r.get("event") == "turn"]
        )
        self._source = records[0].get("model", "unknown") if records else "unknown"

    @property
    def name(self) -> str:
        return f"transcript:{self._path.name} (recorded by {self._source})"

    def start(self, system: str, goal: str, tools: list[dict[str, Any]]) -> None:
        return None

    def decide(self) -> ModelTurn:
        try:
            return ModelTurn.from_record(next(self._turns))
        except StopIteration:
            raise NoModelAvailable(
                f"{self._path.name} has no further turns; the recorded run "
                f"ended here. If the application has changed since it was "
                f"recorded, the replayed decisions may no longer fit."
            ) from None

    def record_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        return None


def provider_for(*, transcript: Path | None = None, model: str | None = None):
    """Pick a provider: a recorded transcript if given, otherwise a real model."""
    if transcript is not None:
        return TranscriptProvider(transcript)
    return OpenAIProvider(model)

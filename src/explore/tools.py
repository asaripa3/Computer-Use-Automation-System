"""The tool surface a model drives the application through.

The non-obvious part of this design: **the model does not only act, it
annotates.**

An artifact cannot be derived from an action trace alone. A trace records that
"12345" was typed into a field; it cannot say whether that was a parameter the
caller will supply each time or an incidental value the model happened to
choose, and it cannot say which screen proves the goal was reached. Those are
judgements, and the model is the only thing in the loop that holds them.

So half of these tools do not touch the application at all. `declare_input`,
`declare_output`, `expect` and `note_outcome` exist purely to let the model
state what it has worked out, and it is those declarations -- not the clicks --
that turn a transcript into a capability contract.

Every acting tool returns the resulting page, so the loop is observe → decide →
act without the model having to ask for a fresh look each time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from policy.allowlist import Allowlist, PolicyViolation
from surface.model import Node, Observation, Surface
from surface.view import render

VALUE_TYPES = ["string", "integer", "money", "date", "boolean", "enum"]
SENSITIVITIES = ["none", "internal", "pii", "secret"]


def tool_schemas() -> list[dict[str, Any]]:
    """The tools, as the model sees them."""
    return [
        {
            "name": "observe",
            "description": (
                "Look at the current page again. Every other tool already "
                "returns the resulting page, so use this only when you want a "
                "fresh look without acting."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "navigate",
            "description": (
                "Go to a path within the target application, e.g. "
                "'/console/search'. Paths only -- the capability must be "
                "replayable against another institution's host."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "fill",
            "description": (
                "Type into a text field. Give EITHER a literal 'value', OR "
                "'from_input' naming an input you declared with declare_input "
                "-- use from_input whenever the value would differ between "
                "invocations, which is what makes the capability reusable."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "a ref from the current page"},
                    "value": {"type": "string"},
                    "from_input": {"type": "string"},
                },
                "required": ["ref"],
            },
        },
        {
            "name": "select",
            "description": "Choose an option from a dropdown, by its visible label.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "value": {"type": "string"},
                    "from_input": {"type": "string"},
                },
                "required": ["ref"],
            },
        },
        {
            "name": "click",
            "description": (
                "Click a control. Set 'irreversible' when the click commits "
                "something that cannot be undone by navigating away -- opening "
                "an account, posting a transaction. Say so honestly: it is what "
                "decides whether this capability may ever run unattended."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "irreversible": {"type": "boolean"},
                },
                "required": ["ref"],
            },
        },
        {
            "name": "expect",
            "description": (
                "Record what proves the step you just took worked -- a phrase "
                "that appears on the resulting page and would not appear if it "
                "had failed. Replay waits for this."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "text": {"type": "string", "description": "text present on the page"},
                },
                "required": ["description", "text"],
            },
        },
        {
            "name": "declare_input",
            "description": (
                "Declare a value the calling agent supplies per invocation. Do "
                "this BEFORE the step that uses it, then reference it with "
                "from_input. Mark anything identifying a person as pii."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": VALUE_TYPES},
                    "description": {"type": "string"},
                    "example": {"type": "string"},
                    "pattern": {"type": "string", "description": "optional regex"},
                    "sensitivity": {"type": "string", "enum": SENSITIVITIES},
                },
                "required": ["name", "type", "description", "example"],
            },
        },
        {
            "name": "declare_output",
            "description": (
                "Declare a value the capability returns. Give EITHER 'ref' for "
                "a value on the current page, OR 'column' plus 'key_column' and "
                "one of key_value/key_from_input to address a cell in a grid by "
                "the row it belongs to -- which survives the record having a "
                "different number of rows."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": VALUE_TYPES},
                    "description": {"type": "string"},
                    "ref": {"type": "string"},
                    "column": {"type": "string"},
                    "key_column": {"type": "string"},
                    "key_value": {"type": "string"},
                    "key_from_input": {"type": "string"},
                    "optional": {"type": "boolean"},
                    "sensitivity": {"type": "string", "enum": SENSITIVITIES},
                },
                "required": ["name", "type", "description"],
            },
        },
        {
            "name": "note_outcome",
            "description": (
                "Record a legitimate ending that is NOT success and that you "
                "can see is possible -- 'no such member', 'permission denied'. "
                "kind is 'business' when it is an answer the caller needs, or "
                "'hard' when it means the application is broken. Replay uses "
                "these to tell an answer apart from a failure."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "UPPER_SNAKE_CASE"},
                    "kind": {"type": "string", "enum": ["business", "hard"]},
                    "description": {"type": "string"},
                    "text": {"type": "string", "description": "text that identifies it"},
                },
                "required": ["code", "kind", "description", "text"],
            },
        },
        {
            "name": "restart_flow",
            "description": (
                "Discard the steps recorded so far and start the flow properly "
                "from here. Use it once you have finished exploring -- checking "
                "what an empty result looks like, backing out of a wrong page -- "
                "so the capability contains only the steps that matter. What you "
                "declared along the way is kept."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "finish",
            "description": (
                "The goal is reached. Give the text that proves it -- the "
                "capability's success condition -- and a short summary."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "success_text": {"type": "string"},
                    "success_description": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["success_text", "success_description", "summary"],
            },
        },
        {
            "name": "give_up",
            "description": (
                "You cannot safely proceed. Say why, in terms a human operator "
                "taking over would find useful."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    ]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    # The control this call acted on, when it acted on one. Used to write a
    # durable description of the target into the transcript.
    node: "Node | None" = None


def descriptor_for(node: Node) -> dict[str, Any]:
    """Describe a control in terms that survive the page being re-rendered.

    A ref is valid for exactly one observation, so a transcript full of refs
    replays into nothing. Recording what the control *was* -- its role and
    name, or its column and value in a grid -- lets the same decision be
    re-issued against a fresh page.
    """
    if node.table is not None and node.table.column_header:
        return {"column": node.table.column_header, "text": node.text}
    return {"role": node.role, "name": node.name, "name_source": node.name_source}


def find_by_descriptor(observation: Observation, spec: dict[str, Any]) -> Node | None:
    if spec.get("column"):
        return observation.cell(
            column=spec["column"], where_column=spec["column"],
            where_value=spec.get("text"),
        )
    matches = observation.find(role=spec.get("role"), name=spec.get("name"))
    # Prefer the same provenance, so a caption is not mistaken for its value.
    same = [n for n in matches if n.name_source == spec.get("name_source")]
    pool = same or matches
    return pool[0] if pool else None


@dataclass
class Session:
    """Everything a tool needs to do its job."""

    surface: Surface
    policy: Allowlist
    base: str
    observation: Observation | None = None
    # Every page this run has rendered. An outcome detector has to be wording
    # the run actually saw, and this is what makes that checkable.
    seen: list[str] = field(default_factory=list)
    finished: bool = False
    gave_up: bool = False
    give_up_reason: str = ""
    summary: str = ""

    def look(self) -> Observation:
        self.observation = self.surface.observe()
        return self.observation

    def node(self, ref: str) -> Node:
        if self.observation is None:
            raise LookupError("nothing has been observed yet")
        found = self.observation.by_ref(ref)
        if found is None:
            raise LookupError(
                f"{ref!r} is not on the current page. Refs are only valid for "
                f"the most recent observation -- look again and use a fresh one."
            )
        return found

    def locate(self, args: dict[str, Any]) -> Node:
        """The control a call names, by ref or by durable description."""
        if args.get("ref"):
            return self.node(args["ref"])
        target = args.get("target")
        if not target:
            raise LookupError("this call names no control: give a 'ref'")
        if self.observation is None:
            raise LookupError("nothing has been observed yet")
        found = find_by_descriptor(self.observation, target)
        if found is None:
            raise LookupError(
                f"nothing on this page matches {target} -- the recorded "
                f"decision no longer fits the application"
            )
        return found

    def page(self) -> str:
        rendered = render(self.look())
        self.seen.append(rendered)
        # Bounded: a long run should not accumulate the whole application.
        del self.seen[:-60]
        return rendered

    def has_seen(self, text: str) -> bool:
        needle = (text or "").strip()
        return bool(needle) and any(needle in page for page in self.seen)


def execute(call: ToolCall, session: Session, recorder) -> ToolResult:
    """Run one tool call and describe the result back to the model."""
    name, args = call.name, call.arguments

    try:
        if name == "observe":
            return ToolResult(session.page())

        if name == "navigate":
            path = args["path"]
            url = path if path.startswith("http") else session.base.rstrip("/") + path
            # The guardrail applies during discovery too, not only on replay:
            # §3.4 says the agent must not act outside the allowlist, and
            # discovery is when the agent is least predictable.
            session.policy.check_navigation(url)
            session.surface.goto(url)
            recorder.navigated(path if path.startswith("/") else url)
            return ToolResult(session.page())

        if name in {"fill", "select"}:
            node = session.locate(args)
            literal, from_input = args.get("value"), args.get("from_input")
            if (literal is None) == (from_input is None):
                return ToolResult(
                    "give exactly one of 'value' or 'from_input'", is_error=True
                )
            text = literal if literal is not None else recorder.example_for(from_input)
            if text is None:
                return ToolResult(
                    f"input {from_input!r} has not been declared yet -- call "
                    f"declare_input first so the capability knows its shape",
                    is_error=True,
                )
            session.policy.check_action(name)
            getattr(session.surface, name)(node.ref, text)
            recorder.acted(name, node, literal=literal, from_input=from_input)
            return ToolResult(session.page(), node=node)

        if name == "click":
            node = session.locate(args)
            session.policy.check_action("click")
            session.surface.click(node.ref)
            recorder.acted("click", node, irreversible=bool(args.get("irreversible")))
            return ToolResult(session.page(), node=node)

        if name == "expect":
            baked = recorder.bakes_in_a_parameter(args["text"])
            if baked:
                return ToolResult(
                    f"that checkpoint contains the value of the {baked!r} input, "
                    f"so it would only ever hold for this one invocation. Pick "
                    f"text that proves the step worked for ANY value of "
                    f"{baked!r} -- a heading, a section title, a label.",
                    is_error=True,
                )
            recorder.expected(args["description"], args["text"])
            # Checked immediately rather than trusted. A checkpoint the model
            # believes in but the page does not show would make replay wait for
            # something that never arrives, and the run that recorded it would
            # look like a success.
            if args["text"] in session.page():
                return ToolResult("recorded; the page does show that text")
            return ToolResult(
                "recorded, but that text is NOT on the current page -- a replay "
                "would wait for it and time out. Pick a phrase that is actually "
                "visible now.",
                is_error=True,
            )

        if name == "declare_input":
            recorder.declared_input(args)
            return ToolResult(
                f"input {args['name']!r} declared; reference it with "
                f"from_input={args['name']!r}"
            )

        if name == "declare_output":
            node = session.locate(args) if (args.get("ref") or args.get("target")) else None
            recorder.declared_output(args, node)
            return ToolResult(f"output {args['name']!r} declared", node=node)

        if name == "restart_flow":
            dropped = recorder.discard_steps()
            return ToolResult(
                f"discarded {dropped} exploratory step(s); the flow starts here. "
                f"Everything you declared is kept."
            )

        if name == "note_outcome":
            baked = recorder.bakes_in_a_parameter(args["text"])
            if baked:
                return ToolResult(
                    f"that detector contains the value of the {baked!r} input, "
                    f"so replay would only recognise this outcome for one "
                    f"invocation. Use the wording the application shows "
                    f"regardless of the value.",
                    is_error=True,
                )
            if not session.has_seen(args["text"]):
                return ToolResult(
                    f"you have not seen {args['text']!r} anywhere in this run, "
                    f"so it is a guess. Replay matches this text literally: if "
                    f"the wording is wrong the outcome is never recognised and "
                    f"a legitimate answer gets reported as an outage.\n"
                    f"Bring the outcome about safely -- searching for a value "
                    f"that will not match, for instance -- then record the "
                    f"exact wording the page shows.",
                    is_error=True,
                )
            recorder.noted_outcome(args)
            return ToolResult(f"outcome {args['code']!r} recorded")

        if name == "finish":
            # Refuse a finish that would produce an unusable artifact, and say
            # exactly what is missing. A model that has just completed the task
            # is the only thing still able to fill the gap -- once the loop
            # ends, the run is wasted.
            # "The goal is reached" means the goal state is on screen now. A
            # run that wandered off after completing the task -- exploring an
            # empty result, say -- would otherwise record a flow ending
            # somewhere its own success condition does not hold, and every
            # replay of it would fail the final checkpoint.
            if not args.get("success_text", "") in session.page():
                return ToolResult(
                    f"{args.get('success_text')!r} is not on the page you are "
                    f"on now, so the flow as recorded does not end at the "
                    f"goal. Either return to the goal state, or call "
                    f"restart_flow and perform the task cleanly from here.",
                    is_error=True,
                )

            outstanding = recorder.blocking_finish()
            if outstanding:
                return ToolResult(
                    "not finished yet. Before this run can become a capability:\n  - "
                    + "\n  - ".join(outstanding)
                    + "\nFix those with the relevant tools, then call finish again.",
                    is_error=True,
                )
            recorder.finished(args)
            session.finished = True
            session.summary = args.get("summary", "")
            return ToolResult("recorded; the run is complete")

        if name == "give_up":
            session.gave_up = True
            session.give_up_reason = args.get("reason", "")
            return ToolResult("recorded")

        return ToolResult(f"unknown tool {name!r}", is_error=True)

    except PolicyViolation as exc:
        # Refused, and the model is told why rather than being left to guess.
        return ToolResult(f"refused by policy -- {exc}", is_error=True)
    except LookupError as exc:
        return ToolResult(str(exc), is_error=True)
    except Exception as exc:  # noqa: BLE001 - the model gets to react to anything
        return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)

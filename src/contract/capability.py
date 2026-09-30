"""The capability contract: what a recorded flow *is*.

A capability is not a script. It is a contract an agent can call and a human
can approve, and the schema is shaped by who has to read it.

Four things here are deliberate and worth defending.

**Declared outcomes are part of the contract.** A flow does not only succeed
or crash. "No such member" and "permission denied" are answers, and a caller
that cannot tell them apart from a broken application cannot do its job. The
artifact therefore enumerates the endings it knows about, each with a way to
detect it, so replay classifies rather than guesses. Conflating these is the
mistake the brief calls out by name, and the schema is where it gets prevented.

**Recoveries are declared, not improvised.** A known interstitial, a transient
error, an expired session -- each is written down with how to detect it and
what to do about it, with a bounded attempt count. Replay absorbing a
condition it was told about is deterministic; replay reasoning its way out of
a surprise is not.

**Outputs carry their own locator.** Extraction is not a step, it is a
declaration: *this* output comes from *that* place in the final state. That
keeps the step list about acting and makes the returned shape reviewable on
its own.

**Every value is typed and its sensitivity stated.** A member id, a name and a
password are not the same kind of thing, and the redaction policy needs the
schema to say so rather than pattern-matching field names and hoping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from .errors import ContractError, require, require_key, require_one_of, require_str
from .locator import Locator
from .values import Value

SCHEMA_VERSION = "1.0"

# How long replay waits for a step's expectation to come true before giving
# up. Waiting is always *for a declared condition*, never a fixed sleep: a
# sleep is either too short when the application is slow or wasted when it is
# not, and it cannot tell "still loading" apart from "will never happen".
DEFAULT_STEP_TIMEOUT_MS = 10_000
MAX_STEP_TIMEOUT_MS = 120_000

VALUE_TYPES = frozenset({"string", "integer", "money", "date", "boolean", "enum"})

# How much care a value needs. Drives redaction, and nothing else reads field
# names to guess.
SENSITIVITY = frozenset({
    "none",      # safe to log in full
    "internal",  # business data: log, but never leaves the evidence directory
    "pii",       # identifies a member: masked in logs and artifacts
    "secret",    # credentials and tokens: never recorded anywhere, at all
})

ACTIONS = frozenset({"navigate", "click", "fill", "select", "wait_for", "assert"})

# §3.4's distinction. `irreversible` means the step commits something that
# cannot be undone by navigating away -- opening an account, posting a
# transaction, sending a notice.
RISKS = frozenset({"safe", "irreversible"})

CONDITION_KINDS = frozenset({"node_present", "node_absent", "text_present", "url_matches"})
OUTCOME_KINDS = frozenset({"business", "hard"})
RECOVERY_ACTIONS = frozenset({"dismiss", "retry", "reauthenticate"})
STATUSES = frozenset({"draft", "approved"})
SURFACE_KINDS = frozenset({"web", "desktop"})


@dataclass(frozen=True)
class Condition:
    """A predicate over an observed state.

    Kept to four kinds on purpose. Each is checkable against an `Observation`
    without a model and without surface-specific knowledge, which is what lets
    the same condition be evaluated on a desktop surface later.
    """

    kind: str
    description: str
    locator: Locator | None = None
    text: str | None = None
    pattern: str | None = None

    def __post_init__(self) -> None:
        require_one_of(self.kind, CONDITION_KINDS, "condition.kind")
        require(bool(self.description.strip()), "condition", "a condition needs a description")
        if self.kind in {"node_present", "node_absent"}:
            require(self.locator is not None, "condition", f"{self.kind} needs a locator")
        if self.kind == "text_present":
            require(bool(self.text), "condition", "text_present needs text")
        if self.kind == "url_matches":
            require(bool(self.pattern), "condition", "url_matches needs a pattern")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "description": self.description}
        if self.locator is not None:
            out["locator"] = self.locator.to_dict()
        if self.text is not None:
            out["text"] = self.text
        if self.pattern is not None:
            out["pattern"] = self.pattern
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "Condition":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        locator = data.get("locator")
        try:
            return cls(
                kind=require_str(data, "kind", path),
                description=require_str(data, "description", path),
                locator=Locator.from_dict(locator, f"{path}.locator") if locator else None,
                text=data.get("text"),
                pattern=data.get("pattern"),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None


@dataclass(frozen=True)
class InputSpec:
    name: str
    type: str
    description: str
    required: bool = True
    pattern: str | None = None
    choices: tuple[str, ...] = ()
    sensitivity: str = "none"
    example: str | None = None

    def __post_init__(self) -> None:
        require_one_of(self.type, VALUE_TYPES, f"input[{self.name}].type")
        require_one_of(self.sensitivity, SENSITIVITY, f"input[{self.name}].sensitivity")
        if self.type == "enum":
            require(bool(self.choices), f"input[{self.name}]", "an enum input needs choices")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "type": self.type, "description": self.description,
            "required": self.required, "sensitivity": self.sensitivity,
        }
        if self.pattern:
            out["pattern"] = self.pattern
        if self.choices:
            out["choices"] = list(self.choices)
        if self.example is not None:
            out["example"] = self.example
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "InputSpec":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        try:
            return cls(
                name=require_str(data, "name", path),
                type=require_str(data, "type", path),
                description=require_str(data, "description", path),
                required=bool(data.get("required", True)),
                pattern=data.get("pattern"),
                choices=tuple(data.get("choices") or ()),
                sensitivity=data.get("sensitivity", "none"),
                example=data.get("example"),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None


@dataclass(frozen=True)
class OutputSpec:
    """A declared return value and where it is read from.

    `after_step` reads the value from the state *following* that step, for
    data that is only on screen briefly -- a confirmation number on a page the
    flow then leaves. Left unset, the value is read from the final state.
    """

    name: str
    type: str
    description: str
    source: Locator
    sensitivity: str = "none"
    optional: bool = False
    after_step: int | None = None

    def __post_init__(self) -> None:
        require_one_of(self.type, VALUE_TYPES, f"output[{self.name}].type")
        require_one_of(self.sensitivity, SENSITIVITY, f"output[{self.name}].sensitivity")
        require(
            self.sensitivity != "secret",
            f"output[{self.name}]",
            "a capability may not return a secret; there is no safe way to log the result",
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "type": self.type, "description": self.description,
            "source": self.source.to_dict(), "sensitivity": self.sensitivity,
            "optional": self.optional,
        }
        if self.after_step is not None:
            out["after_step"] = self.after_step
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "OutputSpec":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        try:
            return cls(
                name=require_str(data, "name", path),
                type=require_str(data, "type", path),
                description=require_str(data, "description", path),
                source=Locator.from_dict(require_key(data, "source", path), f"{path}.source"),
                sensitivity=data.get("sensitivity", "none"),
                optional=bool(data.get("optional", False)),
                after_step=data.get("after_step"),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None


@dataclass(frozen=True)
class Step:
    index: int
    action: str
    description: str
    target: Locator | None = None
    value: Value | None = None
    url: str | None = None
    risk: str = "safe"
    expect: Condition | None = None
    # How long to wait for `expect` to become true. Unset means the
    # capability's default.
    timeout_ms: int | None = None

    def __post_init__(self) -> None:
        path = f"steps[{self.index}]"
        require_one_of(self.action, ACTIONS, f"{path}.action")
        require_one_of(self.risk, RISKS, f"{path}.risk")
        if self.action in {"click", "fill", "select", "wait_for"}:
            require(self.target is not None, path, f"{self.action} needs a target")
        if self.action in {"fill", "select"}:
            require(self.value is not None, path, f"{self.action} needs a value")
        if self.action == "navigate":
            require(bool(self.url), path, "navigate needs a url")
        if self.action == "assert":
            require(self.expect is not None, path, "assert needs an expectation")
        if self.timeout_ms is not None:
            require(
                0 < self.timeout_ms <= MAX_STEP_TIMEOUT_MS,
                f"{path}.timeout_ms",
                f"must be between 1 and {MAX_STEP_TIMEOUT_MS}; an unbounded "
                f"wait is not a wait strategy, it is a hang",
            )

    @property
    def is_irreversible(self) -> bool:
        return self.risk == "irreversible"

    @property
    def inputs_used(self) -> frozenset[str]:
        used = set(self.target.inputs_used) if self.target else set()
        if self.value is not None and self.value.from_input:
            used.add(self.value.from_input)
        return frozenset(used)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "index": self.index, "action": self.action,
            "description": self.description, "risk": self.risk,
        }
        if self.target is not None:
            out["target"] = self.target.to_dict()
        if self.value is not None:
            out["value"] = self.value.to_dict()
        if self.url is not None:
            out["url"] = self.url
        if self.expect is not None:
            out["expect"] = self.expect.to_dict()
        if self.timeout_ms is not None:
            out["timeout_ms"] = self.timeout_ms
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "Step":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        target, value, expect = data.get("target"), data.get("value"), data.get("expect")
        index = data.get("index")
        if not isinstance(index, int):
            raise ContractError(f"{path}.index", "expected an integer")
        # Report against the step's own declared index rather than its position
        # in the array, so that every error about a step names it the same way
        # a reviewer reading the artifact would.
        path = f"steps[{index}]"
        try:
            return cls(
                index=index,
                action=require_str(data, "action", path),
                description=require_str(data, "description", path),
                target=Locator.from_dict(target, f"{path}.target") if target else None,
                value=Value.from_dict(value, f"{path}.value") if value else None,
                url=data.get("url"),
                risk=data.get("risk", "safe"),
                expect=Condition.from_dict(expect, f"{path}.expect") if expect else None,
                timeout_ms=data.get("timeout_ms"),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None


@dataclass(frozen=True)
class OutcomeSpec:
    """A known ending that is not success.

    `kind` separates the two that matter to a caller: a *business* outcome is
    an answer to report ("this member does not exist"), a *hard* one is a
    failure to surface ("the member index is down"). Both are expected in the
    sense that we know they can happen; only one of them means something is
    broken.
    """

    code: str
    kind: str
    description: str
    detect: Condition

    def __post_init__(self) -> None:
        require_one_of(self.kind, OUTCOME_KINDS, f"outcome[{self.code}].kind")
        require(self.code == self.code.upper(), f"outcome[{self.code}]",
                "outcome codes are upper case so they read as constants to a caller")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "kind": self.kind,
                "description": self.description, "detect": self.detect.to_dict()}

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "OutcomeSpec":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        try:
            return cls(
                code=require_str(data, "code", path),
                kind=require_str(data, "kind", path),
                description=require_str(data, "description", path),
                detect=Condition.from_dict(require_key(data, "detect", path), f"{path}.detect"),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None


@dataclass(frozen=True)
class RecoverySpec:
    """A runtime condition replay is permitted to absorb, and how."""

    name: str
    description: str
    detect: Condition
    action: str
    target: Locator | None = None
    max_attempts: int = 2

    def __post_init__(self) -> None:
        require_one_of(self.action, RECOVERY_ACTIONS, f"recovery[{self.name}].action")
        if self.action == "dismiss":
            require(self.target is not None, f"recovery[{self.name}]",
                    "dismiss needs a target to click")
        require(1 <= self.max_attempts <= 5, f"recovery[{self.name}]",
                "max_attempts must be between 1 and 5 -- an unbounded retry is not a recovery")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "description": self.description,
            "detect": self.detect.to_dict(), "action": self.action,
            "max_attempts": self.max_attempts,
        }
        if self.target is not None:
            out["target"] = self.target.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "RecoverySpec":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        target = data.get("target")
        try:
            return cls(
                name=require_str(data, "name", path),
                description=require_str(data, "description", path),
                detect=Condition.from_dict(require_key(data, "detect", path), f"{path}.detect"),
                action=require_str(data, "action", path),
                target=Locator.from_dict(target, f"{path}.target") if target else None,
                max_attempts=int(data.get("max_attempts", 2)),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None


@dataclass(frozen=True)
class SurfaceSpec:
    """What kind of surface this capability runs against, and what it touches.

    `requires_origins` is a declaration of intent, not a grant. Policy decides
    whether an artifact is allowed what it asks for; the artifact only states
    what it needs, so the two can be reviewed independently.
    """

    kind: str
    entry_url: str
    requires_origins: tuple[str, ...] = ()
    app_fingerprint: str = ""

    def __post_init__(self) -> None:
        require_one_of(self.kind, SURFACE_KINDS, "surface.kind")

    @property
    def origin(self) -> str:
        """Scheme and host the capability runs against.

        Every navigation in a capability is recorded as a *path* and resolved
        against this. That is what lets one artifact serve hundreds of tenants
        on different hosts: an overlay changes this one field and the whole
        flow relocates. An artifact whose steps carried absolute URLs would
        quietly keep navigating to the institution it was recorded at.
        """
        parsed = urlparse(self.entry_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def absolute(self, url: str) -> str:
        """Resolve a recorded path against this surface's origin."""
        if url.startswith(("http://", "https://")):
            return url
        return urljoin(self.origin, url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "entry_url": self.entry_url,
            "requires_origins": list(self.requires_origins),
            "app_fingerprint": self.app_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "SurfaceSpec":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        return cls(
            kind=require_str(data, "kind", path),
            entry_url=require_str(data, "entry_url", path),
            requires_origins=tuple(data.get("requires_origins") or ()),
            app_fingerprint=data.get("app_fingerprint", ""),
        )


@dataclass(frozen=True)
class Provenance:
    """Where this artifact came from. Evidence, and a drift baseline."""

    recorded_at: str = ""
    recorded_by: str = ""
    discovery_run: str = ""
    goal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded_at": self.recorded_at, "recorded_by": self.recorded_by,
            "discovery_run": self.discovery_run, "goal": self.goal,
        }

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "Provenance":
        if not isinstance(data, dict):
            return cls()
        return cls(
            recorded_at=data.get("recorded_at", ""),
            recorded_by=data.get("recorded_by", ""),
            discovery_run=data.get("discovery_run", ""),
            goal=data.get("goal", ""),
        )


@dataclass(frozen=True)
class Capability:
    """One reusable, reviewable, agent-invocable flow."""

    id: str
    version: str
    title: str
    description: str
    surface: SurfaceSpec
    steps: tuple[Step, ...]
    success: Condition
    inputs: tuple[InputSpec, ...] = ()
    outputs: tuple[OutputSpec, ...] = ()
    outcomes: tuple[OutcomeSpec, ...] = ()
    recoveries: tuple[RecoverySpec, ...] = ()
    status: str = "draft"
    default_timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance = field(default_factory=Provenance)

    def __post_init__(self) -> None:
        require_one_of(self.status, STATUSES, "status")
        require(
            0 < self.default_timeout_ms <= MAX_STEP_TIMEOUT_MS,
            "default_timeout_ms",
            f"must be between 1 and {MAX_STEP_TIMEOUT_MS}",
        )
        self.validate()

    # -- cross-field validation -------------------------------------------

    def validate(self) -> None:
        """Check the things that only make sense across the whole artifact.

        Per-field validation happens in each dataclass. What is left is the
        consistency a reviewer would otherwise have to hold in their head: do
        the steps refer to inputs that exist, are the step numbers a sequence,
        does anything actually produce the declared outputs. An artifact that
        passes this is not necessarily *correct*, but it is coherent -- and
        every incoherence caught here is one that would otherwise surface as a
        confusing failure halfway through a run against member records.
        """
        require(bool(self.id.strip()), "id", "a capability needs an id")
        require(bool(self.steps), "steps", "a capability needs at least one step")

        self._check_unique_names()
        self._check_step_numbering()
        self._check_input_references()
        self._check_outputs()
        self._check_outcomes()
        self._check_irreversible_steps_are_last()

    def _check_unique_names(self) -> None:
        for label, names in (
            ("inputs", [i.name for i in self.inputs]),
            ("outputs", [o.name for o in self.outputs]),
            ("outcomes", [o.code for o in self.outcomes]),
            ("recoveries", [r.name for r in self.recoveries]),
        ):
            duplicates = {n for n in names if names.count(n) > 1}
            require(not duplicates, label, f"duplicate name(s): {sorted(duplicates)}")

    def _check_step_numbering(self) -> None:
        expected = list(range(1, len(self.steps) + 1))
        actual = [s.index for s in self.steps]
        require(
            actual == expected,
            "steps",
            f"step indices must run 1..{len(self.steps)} in order, got {actual}",
        )

    def _check_input_references(self) -> None:
        declared = {i.name for i in self.inputs}
        for step in self.steps:
            for name in sorted(step.inputs_used):
                require(
                    name in declared,
                    f"steps[{step.index}]",
                    f"refers to input {name!r}, which is not declared",
                )
        for output in self.outputs:
            for name in sorted(output.source.inputs_used):
                require(
                    name in declared,
                    f"output[{output.name}]",
                    f"refers to input {name!r}, which is not declared",
                )

    def _check_outputs(self) -> None:
        last = len(self.steps)
        for output in self.outputs:
            if output.after_step is not None:
                require(
                    1 <= output.after_step <= last,
                    f"output[{output.name}].after_step",
                    f"must name a step between 1 and {last}",
                )

    def _check_outcomes(self) -> None:
        # A capability that declares no non-success ending is almost certainly
        # incomplete: every flow against a real system can fail to find its
        # subject. Flagging it here is cheap; discovering it in production is
        # not.
        require(
            bool(self.outcomes),
            "outcomes",
            "declare at least one known non-success outcome; a flow that can "
            "only succeed or crash cannot report a business answer",
        )

    def _check_irreversible_steps_are_last(self) -> None:
        """An irreversible step may not be followed by another one.

        Two commits in one capability cannot be replayed safely: if the second
        fails there is no way to undo the first, and the caller is left with a
        partial change it was never told about. Flows that genuinely need that
        belong behind a transaction the application itself provides, not
        behind a replay engine.
        """
        irreversible = [s.index for s in self.steps if s.is_irreversible]
        require(
            len(irreversible) <= 1,
            "steps",
            f"more than one irreversible step ({irreversible}); a replay cannot "
            "undo the first if the second fails",
        )

    # -- derived properties ------------------------------------------------

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def is_irreversible(self) -> bool:
        return any(s.is_irreversible for s in self.steps)

    @property
    def irreversible_step(self) -> Step | None:
        return next((s for s in self.steps if s.is_irreversible), None)

    @property
    def handles_sensitive_data(self) -> bool:
        return any(
            spec.sensitivity in {"pii", "secret"}
            for spec in (*self.inputs, *self.outputs)
        )

    @property
    def weakest_targets(self) -> tuple[Step, ...]:
        """Steps that can only be found by document position.

        The review surface for "which part of this will break first".
        """
        return tuple(
            s for s in self.steps
            if s.target is not None and s.target.is_positional_only
        )

    def timeout_for(self, step: Step) -> int:
        """How long replay should wait on this step."""
        return step.timeout_ms or self.default_timeout_ms

    def required_inputs(self) -> tuple[str, ...]:
        return tuple(i.name for i in self.inputs if i.required)

    def input_named(self, name: str) -> InputSpec | None:
        return next((i for i in self.inputs if i.name == name), None)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "default_timeout_ms": self.default_timeout_ms,
            "surface": self.surface.to_dict(),
            "inputs": [i.to_dict() for i in self.inputs],
            "outputs": [o.to_dict() for o in self.outputs],
            "steps": [s.to_dict() for s in self.steps],
            "success": self.success.to_dict(),
            "outcomes": [o.to_dict() for o in self.outcomes],
            "recoveries": [r.to_dict() for r in self.recoveries],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Capability":
        if not isinstance(data, dict):
            raise ContractError("", "expected an object at the top level")

        version = data.get("schema_version", SCHEMA_VERSION)
        require(
            version == SCHEMA_VERSION,
            "schema_version",
            f"this build reads schema {SCHEMA_VERSION}, artifact declares {version!r}",
        )

        def listed(key: str, factory):
            raw = data.get(key) or []
            if not isinstance(raw, list):
                raise ContractError(key, "expected a list")
            return tuple(factory(item, f"{key}[{i}]") for i, item in enumerate(raw))

        return cls(
            schema_version=version,
            id=require_str(data, "id", ""),
            version=require_str(data, "version", ""),
            status=data.get("status", "draft"),
            default_timeout_ms=int(data.get("default_timeout_ms", DEFAULT_STEP_TIMEOUT_MS)),
            title=require_str(data, "title", ""),
            description=require_str(data, "description", ""),
            surface=SurfaceSpec.from_dict(require_key(data, "surface", ""), "surface"),
            inputs=listed("inputs", InputSpec.from_dict),
            outputs=listed("outputs", OutputSpec.from_dict),
            steps=listed("steps", Step.from_dict),
            success=Condition.from_dict(require_key(data, "success", ""), "success"),
            outcomes=listed("outcomes", OutcomeSpec.from_dict),
            recoveries=listed("recoveries", RecoverySpec.from_dict),
            provenance=Provenance.from_dict(data.get("provenance"), "provenance"),
        )

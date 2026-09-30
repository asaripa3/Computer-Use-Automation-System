"""Binding invocation parameters to a contract, and giving outputs their shape.

§3.2 asks for typed inputs and for typed outputs *and their shape*; §3.3 for
replay to be given "a set of input parameters". Without this module both of
those are decorative: `type: "money"` would be a label on a string, and
nothing would stop a caller passing a member id of `"'; DROP"` straight into a
banking search box.

Two directions, and they are not symmetric.

**Inbound is strict.** A supplied parameter is checked against the declared
type, pattern and choices before a single step runs, and anything wrong is
refused up front. Failing before touching the application is the only point at
which a bad parameter is free; once a flow has started, the cheapest outcome
is a confusing failure and the worst is a transaction against the wrong
record.

**Outbound is forgiving, then exact.** A value read off a legacy screen
arrives as display text -- "4,821.37" with grouping and no currency symbol,
"03/11/1974" in the local convention. Coercion accepts what the surface
actually renders and returns something a caller can compute on, so the calling
agent is not left parsing bank screens that it never saw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import ContractError

TRUTHY = frozenset({"true", "yes", "y", "1", "on", "checked", "active"})
FALSY = frozenset({"false", "no", "n", "0", "off", "unchecked", "inactive"})

# Display formats a legacy screen might render a date in. ISO first so that a
# value that has already been normalised passes through unchanged.
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d %b %Y", "%b %d, %Y")


class BindingError(ContractError):
    """A supplied parameter does not satisfy the contract."""


@dataclass(frozen=True)
class Bound:
    """Validated invocation parameters, ready to run with."""

    values: dict[str, Any]
    supplied: frozenset[str]

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)


# -- inbound ---------------------------------------------------------------

def _coerce_scalar(raw: Any, type_name: str, path: str) -> Any:
    text = str(raw).strip() if raw is not None else ""

    if type_name == "string":
        return text
    if type_name == "integer":
        try:
            return int(text.replace(",", "").replace(" ", ""))
        except ValueError:
            raise BindingError(path, f"{raw!r} is not an integer") from None
    if type_name == "money":
        cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
        if not cleaned or cleaned in {"-", ".", "-."}:
            raise BindingError(path, f"{raw!r} is not an amount")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            raise BindingError(path, f"{raw!r} is not an amount") from None
    if type_name == "date":
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise BindingError(path, f"{raw!r} is not a date in a recognised format")
    if type_name == "boolean":
        lowered = text.casefold()
        if lowered in TRUTHY:
            return True
        if lowered in FALSY:
            return False
        raise BindingError(path, f"{raw!r} is not a boolean")
    if type_name == "enum":
        return text

    raise BindingError(path, f"unknown declared type {type_name!r}")


def bind_inputs(capability, supplied: dict[str, Any]) -> Bound:
    """Validate and coerce the parameters for one invocation.

    Every problem is collected rather than the first one raised: a caller
    fixing an invocation wants the whole list, not one objection at a time.
    """
    problems: list[str] = []
    values: dict[str, Any] = {}

    declared = {spec.name: spec for spec in capability.inputs}

    unexpected = sorted(set(supplied) - set(declared))
    if unexpected:
        problems.append(
            f"not declared by this capability: {unexpected}. "
            f"Declared inputs are {sorted(declared)}"
        )

    for name, spec in declared.items():
        if name not in supplied or supplied[name] is None or supplied[name] == "":
            if spec.required:
                problems.append(f"{name}: required, but not supplied")
            continue

        raw = supplied[name]
        path = f"inputs.{name}"

        try:
            value = _coerce_scalar(raw, spec.type, path)
        except BindingError as exc:
            problems.append(exc.message)
            continue

        # The pattern is checked against the *supplied text*, not the coerced
        # value, because it exists to constrain what will be typed into the
        # application -- which is the text.
        if spec.pattern and not re.fullmatch(spec.pattern, str(raw).strip()):
            problems.append(f"{name}: {raw!r} does not match {spec.pattern}")
            continue

        if spec.choices and str(value) not in spec.choices:
            problems.append(
                f"{name}: {raw!r} is not one of {list(spec.choices)}"
            )
            continue

        values[name] = value

    if problems:
        raise BindingError(
            capability.ref,
            "invocation parameters rejected:\n  - " + "\n  - ".join(problems),
        )

    return Bound(values=values, supplied=frozenset(values))


def as_step_values(bound: Bound) -> dict[str, str]:
    """The form a step actually types into the application.

    Coercion produced `Decimal` and `date` objects so a caller can compute on
    them. What goes back into a text box has to be the text the application
    expects, so this renders them back -- deliberately, and in one place,
    rather than leaving each step to guess.
    """
    out: dict[str, str] = {}
    for name, value in bound.values.items():
        if isinstance(value, Decimal):
            out[name] = f"{value:.2f}"
        elif isinstance(value, date):
            out[name] = value.strftime("%m/%d/%Y")
        elif isinstance(value, bool):
            out[name] = "true" if value else "false"
        else:
            out[name] = str(value)
    return out


# -- outbound --------------------------------------------------------------

def coerce_output(spec, raw: str | None) -> Any:
    """Give an extracted screen value the shape the contract declares.

    An output that could not be found is `None` when the contract says it is
    optional, and an error when it does not -- because a required output
    silently coming back empty is how a caller ends up acting on a balance it
    never actually read.
    """
    if raw is None or str(raw).strip() == "":
        if spec.optional:
            return None
        raise BindingError(
            f"outputs.{spec.name}",
            f"required output {spec.name!r} was not found on the page "
            f"(expected at {spec.source.description})",
        )
    return _coerce_scalar(raw, spec.type, f"outputs.{spec.name}")


def coerce_outputs(capability, raw: dict[str, str | None]) -> dict[str, Any]:
    return {spec.name: coerce_output(spec, raw.get(spec.name)) for spec in capability.outputs}


def jsonable(value: Any) -> Any:
    """Render a coerced value for JSON, without losing precision."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value

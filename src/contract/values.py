"""How a step gets the value it acts with.

A value is either a literal recorded at discovery time, or a reference to one
of the capability's declared inputs. There is deliberately no string
interpolation and no expression language.

That is a real constraint, and it is chosen. An artifact is meant to be read
and approved by a human before it is allowed to run unattended against member
records; every expression syntax added here is something a reviewer has to
evaluate in their head to know what the capability will actually type into a
banking screen. Concatenation can be added when a flow genuinely needs it,
as an explicit node type, rather than by making every value a small program.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ContractError, require


@dataclass(frozen=True)
class Value:
    literal: str | None = None
    from_input: str | None = None

    def __post_init__(self) -> None:
        provided = [f for f in (self.literal, self.from_input) if f is not None]
        require(
            len(provided) == 1,
            "value",
            "exactly one of 'literal' or 'from_input' must be set",
        )

    def resolve(self, inputs: dict[str, Any], *, path: str = "value") -> str:
        """Produce the concrete string this value stands for."""
        if self.literal is not None:
            return self.literal
        if self.from_input not in inputs:
            raise ContractError(path, f"no value supplied for input {self.from_input!r}")
        value = inputs[self.from_input]
        return "" if value is None else str(value)

    @property
    def is_parameterised(self) -> bool:
        return self.from_input is not None

    def to_dict(self) -> dict[str, Any]:
        if self.literal is not None:
            return {"literal": self.literal}
        return {"from_input": self.from_input}

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "Value":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object with 'literal' or 'from_input'")
        unknown = set(data) - {"literal", "from_input"}
        if unknown:
            raise ContractError(path, f"unknown field(s): {sorted(unknown)}")
        try:
            return cls(literal=data.get("literal"), from_input=data.get("from_input"))
        except ContractError as exc:
            raise ContractError(path, exc.message) from None

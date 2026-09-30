"""How a recorded step says which control it means.

This is the part of the schema the brief asks to see reasoning about, so the
reasoning is here rather than in the write-up alone.

A locator is not one selector. It is an **ordered ladder of candidates**, each
tagged with how much the application itself vouches for it. Replay walks the
ladder and uses the first candidate that resolves to exactly one node.

Three consequences follow from that shape, and all three are the point:

1. **Robustness is ranked, not assumed.** A name the application asserts
   outranks one the surface layer inferred, which outranks a position in the
   document. The recorder does not have to guess which will survive; it
   records all of them in order of how much they can be trusted.

2. **The ladder is a drift detector.** If the first candidate stops resolving
   and a lower one takes over, something about the surface changed. Replay
   reports which rung it landed on, so drift shows up as a signal on a
   successful run instead of waiting to become a failure. That is the answer
   to "how do you detect and manage per-tenant/version drift" -- detection is
   a by-product of how targeting already works.

3. **Tenant fragility and version fragility are different axes.** An asserted
   identifier (a form field name) is stable when a tenant rebrands but can
   move between vendor releases. A role-and-name is stable across releases but
   is exactly what a tenant rebrands. Carrying both means one artifact can
   survive either kind of change, and the overlay mechanism only has to handle
   the case where both fail at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ContractError, require, require_one_of, require_str
from .values import Value

# How much the application vouches for a candidate, worst-to-best ordering
# applied by `Locator.ladder()`.
ASSERTED = "asserted"      # the application states this itself
DERIVED = "derived"        # the surface layer inferred it from layout
POSITIONAL = "positional"  # neither; a position in the document

ROBUSTNESS = (ASSERTED, DERIVED, POSITIONAL)
ROBUSTNESS_RANK = {level: index for index, level in enumerate(ROBUSTNESS)}

# Targeting strategies. Each is expressed so that a non-web surface could
# implement it: `asserted_id` is a form field name on the web and an
# AutomationId under UIA; `role_and_name` is the accessibility vocabulary
# every platform exposes; `grid_cell` maps to UIA's GridItem pattern.
STRATEGIES = frozenset({"asserted_id", "role_and_name", "grid_cell", "structural_path"})


@dataclass(frozen=True)
class LocatorCandidate:
    """One way of finding a control, and how much it can be trusted."""

    strategy: str
    robustness: str
    rationale: str = ""

    # role_and_name
    role: str | None = None
    name: str | None = None
    name_source: str | None = None

    # asserted_id -- `kind` names what sort of identifier this is, so a
    # desktop surface can use the same strategy with kind="automation_id".
    id_kind: str | None = None
    id_value: str | None = None

    # grid_cell -- a value addressed by the column it sits under and a key in
    # another column, which is what survives a member holding a different
    # number of accounts.
    column: str | None = None
    key_column: str | None = None
    key_value: Value | None = None

    # structural_path
    path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_one_of(self.strategy, STRATEGIES, "candidate.strategy")
        require_one_of(self.robustness, frozenset(ROBUSTNESS), "candidate.robustness")

        if self.strategy == "role_and_name":
            require(bool(self.role), "candidate", "role_and_name needs a role")
            require(bool(self.name), "candidate", "role_and_name needs a name")
        elif self.strategy == "asserted_id":
            require(bool(self.id_kind), "candidate", "asserted_id needs an id_kind")
            require(bool(self.id_value), "candidate", "asserted_id needs an id_value")
            require(
                self.robustness == ASSERTED,
                "candidate",
                "asserted_id is by definition asserted by the application",
            )
        elif self.strategy == "grid_cell":
            require(bool(self.column), "candidate", "grid_cell needs a column")
            require(
                (self.key_column is None) == (self.key_value is None),
                "candidate",
                "grid_cell needs key_column and key_value together, or neither",
            )
        elif self.strategy == "structural_path":
            require(bool(self.path), "candidate", "structural_path needs a path")

        # The tiers are only meaningful if they cannot be claimed arbitrarily.
        # A document position is positional and nothing else; a role and name
        # is a description of the control, never a position. Letting a
        # recorder label either one freely would silently reorder the ladder.
        require(
            (self.robustness == POSITIONAL) == (self.strategy == "structural_path"),
            "candidate",
            "a structural path is positional by definition, and nothing else is",
        )

    @property
    def rank(self) -> int:
        return ROBUSTNESS_RANK[self.robustness]

    def describe(self) -> str:
        if self.strategy == "role_and_name":
            return f'{self.role} named "{self.name}"'
        if self.strategy == "asserted_id":
            return f"{self.id_kind}={self.id_value!r}"
        if self.strategy == "grid_cell":
            if self.key_column:
                key = self.key_value.literal or f"<{self.key_value.from_input}>"
                return f'column "{self.column}" where "{self.key_column}" is {key!r}'
            return f'column "{self.column}"'
        return "/".join(self.path)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"strategy": self.strategy, "robustness": self.robustness}
        if self.rationale:
            out["rationale"] = self.rationale
        for name in ("role", "name", "name_source", "id_kind", "id_value", "column", "key_column"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.key_value is not None:
            out["key_value"] = self.key_value.to_dict()
        if self.path:
            out["path"] = list(self.path)
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "LocatorCandidate":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        known = {
            "strategy", "robustness", "rationale", "role", "name", "name_source",
            "id_kind", "id_value", "column", "key_column", "key_value", "path",
        }
        unknown = set(data) - known
        if unknown:
            raise ContractError(path, f"unknown field(s): {sorted(unknown)}")

        key_value = data.get("key_value")
        try:
            return cls(
                strategy=require_str(data, "strategy", path),
                robustness=require_str(data, "robustness", path),
                rationale=data.get("rationale", ""),
                role=data.get("role"),
                name=data.get("name"),
                name_source=data.get("name_source"),
                id_kind=data.get("id_kind"),
                id_value=data.get("id_value"),
                column=data.get("column"),
                key_column=data.get("key_column"),
                key_value=Value.from_dict(key_value, f"{path}.key_value") if key_value else None,
                path=tuple(data.get("path") or ()),
            )
        except ContractError as exc:
            raise ContractError(path, exc.message) from None


@dataclass(frozen=True)
class Locator:
    """A described target plus the ordered ways of finding it."""

    description: str
    candidates: tuple[LocatorCandidate, ...]
    frame: str | None = None

    def __post_init__(self) -> None:
        require(bool(self.description.strip()), "locator", "a locator needs a description")
        require(bool(self.candidates), "locator", "a locator needs at least one candidate")

    def ladder(self) -> tuple[LocatorCandidate, ...]:
        """Candidates in the order replay should try them.

        Stable-sorted by how much the application vouches for each, so a
        recorder's own ordering is preserved within a tier.
        """
        return tuple(sorted(self.candidates, key=lambda c: c.rank))

    @property
    def best_robustness(self) -> str:
        return self.ladder()[0].robustness

    @property
    def is_positional_only(self) -> bool:
        """True when nothing better than a document position was found.

        Worth surfacing to a reviewer: a step that can only be found by
        position is the one most likely to silently target the wrong control
        after a vendor update.
        """
        return all(c.robustness == POSITIONAL for c in self.candidates)

    @property
    def inputs_used(self) -> frozenset[str]:
        return frozenset(
            c.key_value.from_input
            for c in self.candidates
            if c.key_value is not None and c.key_value.from_input is not None
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "description": self.description,
            "candidates": [c.to_dict() for c in self.ladder()],
        }
        if self.frame:
            out["frame"] = self.frame
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str) -> "Locator":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        unknown = set(data) - {"description", "candidates", "frame"}
        if unknown:
            raise ContractError(path, f"unknown field(s): {sorted(unknown)}")

        raw = data.get("candidates")
        if not isinstance(raw, list) or not raw:
            raise ContractError(f"{path}.candidates", "expected a non-empty list")
        try:
            return cls(
                description=require_str(data, "description", path),
                candidates=tuple(
                    LocatorCandidate.from_dict(item, f"{path}.candidates[{i}]")
                    for i, item in enumerate(raw)
                ),
                frame=data.get("frame"),
            )
        except ContractError as exc:
            raise ContractError(exc.path or path, exc.message) from None

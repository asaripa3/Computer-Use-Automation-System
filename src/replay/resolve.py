"""Turning a recorded locator back into a live control.

This is where the ladder recorded in step 2 earns its keep. The rules:

**Try candidates in trust order, and stop at the first that resolves to
exactly one control.** Not the first that matches *something* -- a candidate
matching two controls has not identified anything, and acting on the first of
them would be a coin flip against a banking screen.

**Report which rung won.** A target that resolved on a less-trusted candidate
than the one recorded is drift: the surface moved under the artifact. That is
reported on runs which *succeed*, because the entire value of noticing drift
is noticing it before it becomes a failure.

**Distinguish "found nothing" from "found too many".** They need different
fixes -- one means the control moved or was renamed, the other means the
recorded description was never specific enough -- so they are different
failure kinds rather than one "could not resolve".
"""

from __future__ import annotations

from dataclasses import dataclass

from contract.derive import node_matches
from contract.locator import Locator, LocatorCandidate
from surface.model import Node, Observation


@dataclass(frozen=True)
class Resolution:
    node: Node
    candidate: LocatorCandidate
    drifted: bool
    tried: tuple[str, ...]

    @property
    def rung(self) -> str:
        return self.candidate.strategy


@dataclass(frozen=True)
class Unresolved:
    """Why nothing usable was found, in terms a person can act on."""

    ambiguous: bool
    tried: tuple[str, ...]
    detail: str

    @property
    def kind(self) -> str:
        return "target_ambiguous" if self.ambiguous else "target_not_found"


def _frame_is_present(observation: Observation, frame: str) -> bool:
    return any(frame in path for path in observation.frames)


def _candidates_in_frame(observation: Observation, locator: Locator) -> list[Node]:
    """Narrow to the recorded frame -- but only when that frame is there.

    The frame exists to disambiguate: the navigation panel and the content
    panel both hold a control called "Member Search", and without it a locator
    would match two things. It is deliberately *not* a requirement. The same
    application page is served framed when reached through the console and
    unframed when reached by a direct link, and a capability that only worked
    one of those ways would be brittle for no reason a user could see.
    """
    if not locator.frame or not _frame_is_present(observation, locator.frame):
        return list(observation.nodes)
    return [n for n in observation.nodes if locator.frame in n.frame_path]


def _matches_for(
    observation: Observation,
    locator: Locator,
    candidate: LocatorCandidate,
    inputs: dict[str, str],
) -> list[Node]:
    if candidate.strategy == "grid_cell":
        # A grid cell is addressed through the observation's own row
        # reassembly, because the key lives in a sibling column that the cell
        # itself cannot see.
        key_value = (
            candidate.key_value.resolve(inputs) if candidate.key_value is not None else None
        )
        found = observation.cell(
            column=candidate.column or "",
            where_column=candidate.key_column,
            where_value=key_value,
        )
        if found is None:
            return []
        if (
            locator.frame
            and _frame_is_present(observation, locator.frame)
            and locator.frame not in found.frame_path
        ):
            return []
        return [found]

    pool = _candidates_in_frame(observation, locator)
    return [n for n in pool if n.visible and node_matches(n, candidate)]


def resolve(
    locator: Locator,
    observation: Observation,
    *,
    inputs: dict[str, str] | None = None,
) -> Resolution | Unresolved:
    """Find the one control ``locator`` describes, or explain why not."""
    inputs = inputs or {}
    ladder = locator.ladder()
    best = ladder[0]
    tried: list[str] = []
    ambiguous_at: list[str] = []

    for candidate in ladder:
        tried.append(candidate.strategy)
        matches = _matches_for(observation, locator, candidate, inputs)

        if len(matches) == 1:
            return Resolution(
                node=matches[0],
                candidate=candidate,
                drifted=candidate is not best,
                tried=tuple(tried),
            )
        if len(matches) > 1:
            # Do not guess. A lower candidate may still be specific enough.
            ambiguous_at.append(f"{candidate.strategy} matched {len(matches)}")

    if ambiguous_at:
        return Unresolved(
            ambiguous=True,
            tried=tuple(tried),
            detail=(
                f"{locator.description}: no candidate identified exactly one "
                f"control ({'; '.join(ambiguous_at)})"
            ),
        )
    return Unresolved(
        ambiguous=False,
        tried=tuple(tried),
        detail=(
            f"{locator.description}: none of {list(tried)} matched anything on "
            f"the page"
        ),
    )

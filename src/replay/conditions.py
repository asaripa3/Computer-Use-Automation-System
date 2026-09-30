"""Evaluating a recorded condition against an observed state.

Checkpoints, declared outcomes and recovery triggers are all the same four
predicates, which is why there are only four. Each is checkable without a
model and without surface-specific knowledge, so the same artifact's
conditions would evaluate unchanged on a desktop surface.
"""

from __future__ import annotations

import re

from contract.capability import Capability, Condition, OutcomeSpec, RecoverySpec
from surface.model import Observation

from .resolve import Resolution, resolve


def _page_text(observation: Observation) -> str:
    parts = [observation.title]
    for node in observation.nodes:
        if node.text:
            parts.append(node.text)
        if node.name:
            parts.append(node.name)
    return "\n".join(parts)


def holds(condition: Condition, observation: Observation, *, inputs: dict[str, str] | None = None) -> bool:
    if condition.kind == "text_present":
        return (condition.text or "") in _page_text(observation)

    if condition.kind == "url_matches":
        return re.search(condition.pattern or "", observation.url) is not None

    if condition.kind in {"node_present", "node_absent"}:
        found = isinstance(
            resolve(condition.locator, observation, inputs=inputs), Resolution
        )
        return found if condition.kind == "node_present" else not found

    return False


def first_outcome(
    capability: Capability, observation: Observation, *, inputs: dict[str, str] | None = None
) -> OutcomeSpec | None:
    """The first declared outcome the page satisfies, in declaration order.

    Declaration order is the tie-break on purpose: it is visible in the
    artifact and a reviewer can reorder it. Deriving a precedence from
    anything else would make the classification depend on something nobody
    can see.
    """
    for outcome in capability.outcomes:
        if holds(outcome.detect, observation, inputs=inputs):
            return outcome
    return None


def first_recovery(
    capability: Capability,
    observation: Observation,
    *,
    budget: dict[str, int],
    inputs: dict[str, str] | None = None,
) -> RecoverySpec | None:
    """The first declared recovery whose trigger is on screen and has budget left."""
    for recovery in capability.recoveries:
        if budget.get(recovery.name, recovery.max_attempts) <= 0:
            continue
        if holds(recovery.detect, observation, inputs=inputs):
            return recovery
    return None

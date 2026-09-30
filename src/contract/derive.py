"""Build a locator from something the surface layer observed.

This module is the translation seam, and the only place in the contract
package permitted to read a surface's own hints. Everything else works on the
`Locator` data model alone.

That restriction is what keeps the schema portable. A desktop surface would
add a sibling function here that proposes `asserted_id` candidates from UIA
AutomationIds instead of HTML form field names, and nothing in the capability
schema, the replay engine or a recorded artifact would change.

The ordering rule, which is the substance of §3.2's "reasoning about
robustness":

    Prefer what the application asserts over what we inferred.

A form field name is a fact the application states about itself. An
accessible name that came from an `aria-label` is also such a fact. A name the
surface layer reconstructed from the neighbouring table cell is a good guess,
and on this target it is usually the only name available -- but it is still a
guess, and it ranks below a fact. A position in the document ranks below
everything, because it is the one that breaks silently.
"""

from __future__ import annotations

from surface.model import DERIVED_NAME_SOURCES, Node

from .locator import ASSERTED, DERIVED, POSITIONAL, Locator, LocatorCandidate
from .values import Value


def _asserted_id_candidate(node: Node) -> LocatorCandidate | None:
    """A machine identifier the application states about the control.

    On the web that is the form field name. Deliberately *not* the element id:
    this target regenerates ids on every render, and an id-based locator is
    dead on the next page load. The field name survives, because the
    application's own form handling depends on it -- which is exactly why it
    is worth targeting.
    """
    name = node.hints.field_name
    if not name:
        return None
    return LocatorCandidate(
        strategy="asserted_id",
        robustness=ASSERTED,
        id_kind="field_name",
        id_value=name,
        rationale=(
            "the form field name the application posts back; it survives a "
            "re-render and a restyle, and the application's own request "
            "handling depends on it. Stable across tenant branding, but can "
            "move between vendor releases."
        ),
    )


def _role_and_name_candidate(node: Node) -> LocatorCandidate | None:
    if not node.name:
        return None
    derived = node.name_source in DERIVED_NAME_SOURCES
    return LocatorCandidate(
        strategy="role_and_name",
        robustness=DERIVED if derived else ASSERTED,
        role=node.role,
        name=node.name,
        name_source=node.name_source,
        rationale=(
            (
                "name inferred by the surface layer from the neighbouring "
                "cell; there is no label association in the markup. Survives "
                "a vendor release, but is exactly what a tenant rebrands."
            )
            if derived
            else (
                f"accessible name asserted by the application via "
                f"{node.name_source}; portable to any accessibility API."
            )
        ),
    )


def _grid_cell_candidate(node: Node, key_column: str | None, key_value: Value | None):
    if node.table is None or not node.table.column_header:
        return None
    return LocatorCandidate(
        strategy="grid_cell",
        robustness=DERIVED,
        column=node.table.column_header,
        key_column=key_column,
        key_value=key_value,
        rationale=(
            "addressed by the column it sits under and a key in another "
            "column, so it survives the row moving when the record has a "
            "different number of rows. A row index would not."
        ),
    )


def _structural_candidate(node: Node) -> LocatorCandidate | None:
    if not node.path:
        return None
    return LocatorCandidate(
        strategy="structural_path",
        robustness=POSITIONAL,
        path=node.path,
        rationale=(
            "position in the document; a last resort that breaks silently "
            "when anything above it in the page changes."
        ),
    )


def locator_for(
    node: Node,
    *,
    description: str,
    key_column: str | None = None,
    key_value: Value | None = None,
) -> Locator:
    """Propose every way of finding ``node``, ordered by trustworthiness.

    All viable candidates are recorded, not just the best one. Replay walks
    them in order, and the rung it lands on is the drift signal: a run that
    succeeds on a lower candidate than last time says the surface moved,
    before that movement becomes a failure.
    """
    candidates = [
        _asserted_id_candidate(node),
        _role_and_name_candidate(node),
        _grid_cell_candidate(node, key_column, key_value),
        _structural_candidate(node),
    ]
    viable = tuple(c for c in candidates if c is not None)
    if not viable:
        raise ValueError(
            f"nothing identifies {node.describe()}: it has no field name, no "
            "name, no grid position and no path"
        )

    frame = node.frame_path[-1] if node.frame_path and node.frame_path[-1] != "main" else None
    return Locator(description=description, candidates=viable, frame=frame)


def node_matches(node: Node, candidate: LocatorCandidate, *, key_value: str | None = None) -> bool:
    """Does ``node`` satisfy ``candidate``?

    The other half of the translation seam. `locator_for` turns an observed
    node into candidates; this turns a candidate back into a predicate over
    observed nodes. Both directions read the surface's own hints, and keeping
    them in one module is what lets the rule "nothing above the surface layer
    reads `WebHints`" stay true of the replay engine.

    A desktop surface would add its own pair here and the engine would not
    change.
    """
    if candidate.strategy == "asserted_id":
        if candidate.id_kind == "field_name":
            return node.hints.field_name == candidate.id_value
        return False

    if candidate.strategy == "role_and_name":
        if node.role != candidate.role or not _same_name(node.name, candidate.name or ""):
            return False
        # Where the name came from is part of the identity, not decoration.
        # Both halves of a `Date of Birth | 03/11/1974` panel answer to "Date
        # of Birth" -- the caption by its own text, the value by borrowing its
        # neighbour's -- so without provenance the candidate matches two
        # controls and identifies neither.
        #
        # If the application later gains a real label the provenance changes,
        # this candidate stops matching, and the ladder falls through to the
        # next rung while reporting drift. That is the intended behaviour: the
        # run still works, and it says the surface moved.
        if candidate.name_source:
            return node.name_source == candidate.name_source
        return True

    if candidate.strategy == "structural_path":
        return tuple(node.path) == tuple(candidate.path)

    if candidate.strategy == "grid_cell":
        if node.table is None:
            return False
        if not _same_name(node.table.column_header or "", candidate.column or ""):
            return False
        # The key is checked by the caller, which has the row in hand; a cell
        # on its own cannot see its sibling columns.
        return key_value is None or True

    return False


def _same_name(left: str, right: str) -> bool:
    def fold(value: str) -> str:
        cleaned = (value or "").replace(" ", " ").strip().rstrip(":*").strip()
        return " ".join(cleaned.split()).casefold()

    return fold(left) == fold(right)

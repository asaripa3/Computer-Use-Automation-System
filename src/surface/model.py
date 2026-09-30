"""The seam between perceiving a surface and the flow recorded on top of it.

Everything above this module -- the discovery loop, the capability artifact,
the replay engine -- is written against :class:`Observation` and :class:`Node`.
Nothing above it may reference a DOM, a selector, a CSS property or a browser.
That restriction is the whole point: it is what lets a second surface
implementation (a legacy web app in a frameset, or a native desktop window)
be substituted without the recorded flow changing shape.

The node model is deliberately expressed in terms that exist in all three
accessibility systems this has to reach:

    this model      web (ARIA / AXObject)   Windows UIA        macOS AX
    ----------      ---------------------   ---------------    -----------------
    role            computed role           ControlType        AXRole
    name            accessible name         Name               AXTitle / AXDescription
    value           value                   ValuePattern       AXValue
    bounds          bounding client rect    BoundingRectangle  AXFrame
    enabled         not [disabled]          IsEnabled          AXEnabled
    frame_path      frame / iframe chain    window + pane      AXWindow chain
    table           table coordinates       GridItemPattern    AXTable coordinates

Anything that exists only on the web lives in :class:`WebHints`, which the core
model treats as opaque. A desktop surface would populate its own hints type and
leave web hints empty, and the layers above would not notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Protocol, runtime_checkable

# Normalised roles. Kept small on purpose: every entry has a direct equivalent
# in UIA and AX, so a desktop surface can populate the same vocabulary. A
# surface that cannot classify an element reports "unknown" rather than
# inventing a web-shaped role.
ROLES = frozenset(
    {
        "button",
        "cell",
        "checkbox",
        "columnheader",
        "combobox",
        "document",
        "form",
        "heading",
        "image",
        "link",
        "listitem",
        "menuitem",
        "radio",
        "row",
        "table",
        "text",
        "textbox",
        "unknown",
    }
)

# How a node's name was arrived at. This travels with the node because the
# artifact needs it: a name taken from an explicit ARIA label is a far safer
# thing to build a locator on than one inferred from the neighbouring table
# cell, and the recorded flow should be able to say which it had.
NAME_SOURCES = frozenset(
    {
        "aria-label",       # explicit author-supplied label
        "aria-labelledby",  # explicit, resolved through a reference
        "label-element",    # a real <label for> / wrapping label
        "text",             # the control's own text, e.g. a link or button
        "value",            # a submit button's value attribute
        "placeholder",
        "title",
        "adjacent-label",   # derived: the neighbouring cell in a table layout
        "column-header",    # derived: the column this cell sits under
        "none",
    }
)

# Name sources we derived ourselves rather than read from the application.
# Derived names are legitimate -- on a legacy surface they are often the only
# names available -- but a locator built on one carries more risk, and the
# artifact records that distinction.
DERIVED_NAME_SOURCES = frozenset({"adjacent-label", "column-header"})


class SurfaceError(RuntimeError):
    """Acting on the surface failed.

    Defined here rather than in the browser implementation so that the layers
    above can react to *what happened* without importing anyone's driver. A
    desktop surface raises the same two, and the replay engine handles both
    without knowing which surface it is on.
    """


class StaleElement(SurfaceError):
    """The control was there when we looked, and is not there now.

    Nearly always a race with a navigation the previous step started: the
    observation captured a page that was already on its way out. Recoverable
    by looking again, which is what makes it worth telling apart from a
    control that is genuinely absent.
    """


class ActionFailed(SurfaceError):
    """The control is there, but the action would not complete."""


@dataclass(frozen=True)
class Bounds:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


@dataclass(frozen=True)
class TableCell:
    """Where a cell sits in a grid.

    This is what makes a legacy table-layout application legible. In the
    accessibility tree a grid collapses into a flat run of unassociated nodes;
    restoring the coordinates and the column header is what turns "4,821.37"
    back into "the current balance of account 0001234501".
    """

    table_index: int
    row: int
    column: int
    column_header: str | None
    row_header: str | None
    is_header: bool = False


@dataclass(frozen=True)
class WebHints:
    """Surface-specific detail. Opaque to everything above this layer.

    A desktop surface would leave every field empty and populate its own hints
    type instead. Nothing in the capability schema or the replay engine may
    read these directly -- they exist so that a *web* locator strategy can be
    derived, and so failures can be debugged against the real markup.
    """

    tag: str = ""
    input_type: str = ""
    field_name: str = ""   # the form field name -- survives a re-render, unlike id
    element_id: str = ""   # captured only to prove it is volatile; never targeted
    href: str = ""
    form_name: str = ""
    has_click_handler: bool = False


@dataclass(frozen=True)
class Node:
    """One perceivable, possibly actionable thing on a surface."""

    ref: str
    role: str
    name: str
    name_source: str
    value: str | None = None
    enabled: bool = True
    visible: bool = True
    bounds: Bounds | None = None
    frame_path: tuple[str, ...] = ()
    # Structural position within its frame, e.g. ("table[1]", "tr[3]", "td[5]").
    # A positional fallback for when nothing better identifies a node, and the
    # closest equivalent to a UIA runtime id.
    path: tuple[str, ...] = ()
    table: TableCell | None = None
    text: str = ""
    options: tuple[str, ...] = ()  # for combobox
    hints: WebHints = field(default_factory=WebHints)

    @property
    def name_is_derived(self) -> bool:
        return self.name_source in DERIVED_NAME_SOURCES

    @property
    def is_interactive(self) -> bool:
        return self.role in {
            "button", "checkbox", "combobox", "link", "menuitem", "radio", "textbox"
        }

    def describe(self) -> str:
        label = f'{self.role} "{self.name}"' if self.name else self.role
        if self.frame_path:
            label = f"{'/'.join(self.frame_path)}:{label}"
        return label


@dataclass(frozen=True)
class Observation:
    """A single look at a surface.

    Refs are valid only for the observation that produced them, and say so:
    each one is prefixed with the observation's token. That is not bookkeeping
    for its own sake. Without it a ref from a previous page still resolves --
    to a different element at the same position -- and an automation acts on
    something nobody chose. Anything that needs to survive to the next
    observation, or to a replay months later, has to be expressed as a
    description of the node rather than as a ref.
    """

    url: str
    title: str
    nodes: tuple[Node, ...]
    frames: tuple[tuple[str, ...], ...] = ()
    captured_at: str = ""
    truncated: bool = False
    # Identifies this particular look at the surface. Every ref carries it, so
    # a ref handed back after the page has moved on can be rejected instead of
    # resolving to whatever now occupies that position.
    token: str = ""

    def __iter__(self) -> Iterator[Node]:
        return iter(self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)

    def by_ref(self, ref: str) -> Node | None:
        for node in self.nodes:
            if node.ref == ref:
                return node
        return None

    def find(
        self,
        *,
        role: str | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        frame: str | None = None,
        interactive: bool | None = None,
        visible: bool | None = True,
    ) -> list[Node]:
        """Filter nodes. Name matching is case-insensitive and ignores
        surrounding punctuation, because legacy labels carry trailing colons
        and non-breaking spaces inconsistently."""

        def matches(node: Node) -> bool:
            if role is not None and node.role != role:
                return False
            if name is not None and _normalise(node.name) != _normalise(name):
                return False
            if name_contains is not None and _normalise(name_contains) not in _normalise(node.name):
                return False
            if frame is not None and frame not in node.frame_path:
                return False
            if interactive is not None and node.is_interactive != interactive:
                return False
            if visible is not None and node.visible != visible:
                return False
            return True

        return [node for node in self.nodes if matches(node)]

    def field(self, label: str, *, frame: str | None = None) -> Node | None:
        """The input control labelled ``label``.

        Prefers a real label association over a derived one, so that if an
        application ever gains proper labels the safer name wins automatically.
        """
        candidates = [
            n for n in self.find(name=label, frame=frame)
            if n.role in {"textbox", "combobox", "checkbox", "radio"}
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda n: n.name_is_derived)
        return candidates[0]

    def value_cell(self, label: str, *, frame: str | None = None) -> Node | None:
        """Read a value out of a label/value panel.

        Both halves of a ``Date of Birth | 03/11/1974`` pair end up named
        "Date of Birth" -- the caption by its own text, the value by borrowing
        its neighbour's. Provenance is what tells them apart: the value is the
        one whose name was derived. Asking for a label and getting the label
        back would be useless, so this prefers the derived node.
        """
        candidates = [
            n for n in self.find(role="cell", name=label, frame=frame)
            if n.name_is_derived
        ]
        return candidates[0] if candidates else None

    def rows(self, table_index: int) -> dict[int, dict[str, Node]]:
        """Reassemble one table as ``{row: {column header: cell}}``."""
        out: dict[int, dict[str, Node]] = {}
        for node in self.nodes:
            cell = node.table
            if cell is None or cell.table_index != table_index or cell.is_header:
                continue
            header = cell.column_header or f"column {cell.column}"
            out.setdefault(cell.row, {})[header] = node
        return out

    def cell(
        self,
        *,
        column: str,
        where_column: str | None = None,
        where_value: str | None = None,
        table_index: int | None = None,
    ) -> Node | None:
        """Look up a grid value by column, optionally keyed on another column.

        This is the primitive that a legacy table-layout application demands.
        "The current balance on the row whose account number is 0001234501" is
        expressible; "the sixth generic node after the word Balance" is not,
        and would not survive a member holding a different number of accounts.
        """
        tables = (
            [table_index]
            if table_index is not None
            else sorted({n.table.table_index for n in self.nodes if n.table})
        )
        for index in tables:
            for _, cells in sorted(self.rows(index).items()):
                if where_column is not None:
                    key = cells.get(where_column)
                    if key is None or _normalise(key.text) != _normalise(where_value or ""):
                        continue
                target = cells.get(column)
                if target is not None:
                    return target
        return None


def _normalise(value: str) -> str:
    """Fold whitespace, case and trailing label punctuation."""
    cleaned = (value or "").replace(" ", " ").strip().rstrip(":*").strip()
    return " ".join(cleaned.split()).casefold()


@runtime_checkable
class Surface(Protocol):
    """What every surface implementation must provide.

    Perceiving and acting sit behind one protocol on purpose. They are the two
    halves of the same abstraction -- a surface you can read but not drive, or
    drive but not read, is of no use to either the discovery loop or replay.

    Actions take a ref from the most recent observation. Resolving a durable
    description back to a live node is emphatically *not* this layer's job:
    that belongs to replay, which needs to decide what to do when a
    description matches nothing, or matches more than one thing.
    """

    @property
    def url(self) -> str: ...

    def observe(self) -> Observation: ...

    def goto(self, url: str) -> None: ...

    def click(self, ref: str) -> None: ...

    def fill(self, ref: str, text: str) -> None: ...

    def select(self, ref: str, value: str) -> None: ...

    def press(self, ref: str, key: str) -> None: ...

    def screenshot(self) -> bytes: ...

    def close(self) -> None: ...


def roles_present(nodes: Iterable[Node]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.role] = counts.get(node.role, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

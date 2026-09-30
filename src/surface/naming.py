"""Turn raw captured facts into named, roled nodes.

This is where the judgement lives, and it is deliberately pure: every function
here takes plain dictionaries and returns plain values, so the rules can be
tested exhaustively without launching a browser.

Two problems are solved here, and both of them are the reason a naive
"find the element labelled X" strategy fails on a legacy surface.

**Controls have no labels.** There is not one ``<label for>`` in the target
application. A field is tied to its caption only by sitting in the table cell
next to it. So when nothing authoritative names a control, its name is derived
from the neighbouring cell -- and the node records that the name was derived,
because a locator built on an inferred name deserves less trust than one built
on an author-supplied label.

**Grids collapse.** In the accessibility tree a data grid becomes a flat run of
untyped nodes: the header "Current Balance" and the value "4,821.37" are
siblings with nothing joining them. Naming each cell after the column it sits
under restores the association, so a balance can be addressed as a column on
an identified row rather than as an offset into a list.
"""

from __future__ import annotations

from typing import Any

from .model import Bounds, Node, TableCell, WebHints

# input types that behave as a single-line text entry
TEXT_INPUT_TYPES = frozenset(
    {"", "text", "password", "email", "tel", "number", "search", "url", "date"}
)
BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset", "image"})

# Explicit role attributes we honour, mapped into the normalised vocabulary.
ROLE_ATTR_MAP = {
    "button": "button",
    "link": "link",
    "textbox": "textbox",
    "checkbox": "checkbox",
    "radio": "radio",
    "combobox": "combobox",
    "listbox": "combobox",
    "menuitem": "menuitem",
    "heading": "heading",
    "table": "table",
    "grid": "table",
    "row": "row",
    "cell": "cell",
    "gridcell": "cell",
    "columnheader": "columnheader",
    "img": "image",
    "image": "image",
}


def derive_role(record: dict[str, Any]) -> str:
    """Classify a captured element by how it *behaves*, not by its tag.

    The one judgement call worth naming: an anchor whose href is a
    ``javascript:`` URL is reported as a button. It does not navigate, and
    describing it as a link would tell both the model and the replay engine
    something untrue about what clicking it does. Legacy applications are full
    of these.
    """
    explicit = ROLE_ATTR_MAP.get((record.get("roleAttr") or "").lower())
    if explicit:
        return explicit

    tag = (record.get("tag") or "").lower()
    input_type = (record.get("type") or "").lower()

    if tag == "input":
        if input_type in BUTTON_INPUT_TYPES:
            return "button"
        if input_type == "checkbox":
            return "checkbox"
        if input_type == "radio":
            return "radio"
        if input_type in TEXT_INPUT_TYPES:
            return "textbox"
        return "textbox"
    if tag == "textarea":
        return "textbox"
    if tag == "select":
        return "combobox"
    if tag == "button":
        return "button"
    if tag == "a":
        href = (record.get("href") or "").strip()
        if href.lower().startswith("javascript:") or not href:
            return "button"
        return "link"
    if tag == "th":
        return "columnheader"
    if tag == "td":
        return "cell"
    if tag in {"h1", "h2", "h3"}:
        return "heading"
    if tag in {"label", "legend"}:
        return "text"
    if record.get("hasClickHandler"):
        return "button"
    return "unknown"


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).replace(" ", " ").split()).strip()


def derive_name(record: dict[str, Any], role: str) -> tuple[str, str]:
    """Return ``(name, name_source)`` for a captured element.

    Authoritative sources are tried before derived ones, always. If the target
    application ever gains real labels, the safer name wins automatically and
    nothing above this layer has to change.
    """
    # 1. Author-supplied, unambiguous.
    if aria := _clean(record.get("ariaLabel")):
        return aria, "aria-label"
    if labelled := _clean(record.get("ariaLabelledByText")):
        return labelled, "aria-labelledby"
    if label := _clean(record.get("labelText")):
        return label, "label-element"

    # 2. The control's own content. A submit button carries its caption in the
    #    value attribute; links and headings carry theirs as text.
    if role == "button" and (record.get("tag") or "").lower() == "input":
        if caption := _clean(record.get("value")):
            return caption, "value"
    if role in {"button", "link", "heading", "columnheader", "menuitem", "text"}:
        if own := _clean(record.get("ownText")) or _clean(record.get("text")):
            return own, "text"

    # 3. A cell takes its meaning from its position in the grid or the panel.
    #    Cells always have text of their own, so the rules differ from those
    #    for controls: a cell is named by the column it sits under, or by the
    #    caption immediately to its left, and failing both it *is* the caption
    #    and is named by its own text. The cell above is deliberately not
    #    consulted -- in a label/value panel that would name every caption
    #    after the caption above it.
    table = record.get("table")
    if role == "cell":
        if table and table.get("isDataTable"):
            if header := _clean(table.get("columnHeader")):
                return header, "column-header"
        if table and (previous := _clean(table.get("prevCellText"))):
            return previous, "adjacent-label"
        if own := _clean(record.get("text")):
            return own, "text"

    # 4. A control with no label of its own borrows the neighbouring caption.
    #    The cell to the left first, since that is how these forms are laid
    #    out; the cell above second, for column-style forms. A control has no
    #    text of its own, so there is nothing else left to go on.
    else:
        context = record.get("cellContext") or table
        if context:
            if previous := _clean(context.get("prevCellText")):
                return previous, "adjacent-label"
            if above := _clean(context.get("aboveCellText")):
                return above, "adjacent-label"

    # 5. Weak, but better than nothing.
    if placeholder := _clean(record.get("placeholder")):
        return placeholder, "placeholder"
    if title := _clean(record.get("title")):
        return title, "title"
    if alt := _clean(record.get("alt")):
        return alt, "text"

    return "", "none"


def derive_value(record: dict[str, Any], role: str) -> str | None:
    if role == "combobox":
        return _clean(record.get("selectedOption")) or None
    if role in {"checkbox", "radio"}:
        checked = record.get("checked")
        return None if checked is None else ("checked" if checked else "unchecked")
    if role == "textbox":
        return _clean(record.get("value"))
    if role == "cell":
        return _clean(record.get("text")) or None
    return None


def _table_cell(record: dict[str, Any]) -> TableCell | None:
    """Grid coordinates, but only for genuine data tables.

    A layout table gets no coordinates. Its cells are still captured and still
    borrow their names from the neighbouring caption -- that is how a
    label/value panel stays readable -- but they are not presented as a grid,
    because treating page chrome as data is how an extraction ends up pointed
    at the page footer.
    """
    table = record.get("table")
    if not table or not table.get("isDataTable"):
        return None
    return TableCell(
        table_index=int(table["tableIndex"]),
        row=int(table["row"]),
        column=int(table["column"]),
        column_header=_clean(table.get("columnHeader")) or None,
        row_header=_clean(table.get("rowHeader")) or None,
        is_header=bool(table.get("isHeaderRow") or table.get("isTh")),
    )


def to_node(record: dict[str, Any], *, ref: str, frame_path: tuple[str, ...]) -> Node:
    role = derive_role(record)
    name, name_source = derive_name(record, role)
    bounds_raw = record.get("bounds") or {}

    return Node(
        ref=ref,
        role=role,
        name=name,
        name_source=name_source,
        value=derive_value(record, role),
        enabled=not bool(record.get("disabled")),
        visible=bool(record.get("visible", True)),
        bounds=Bounds(
            x=float(bounds_raw.get("x", 0.0)),
            y=float(bounds_raw.get("y", 0.0)),
            width=float(bounds_raw.get("width", 0.0)),
            height=float(bounds_raw.get("height", 0.0)),
        ),
        frame_path=frame_path,
        path=tuple(record.get("path") or ()),
        table=_table_cell(record),
        text=_clean(record.get("text")),
        options=tuple(_clean(o) for o in (record.get("options") or [])),
        hints=WebHints(
            tag=(record.get("tag") or "").lower(),
            input_type=(record.get("type") or "").lower(),
            field_name=record.get("fieldName") or "",
            element_id=record.get("elementId") or "",
            href=record.get("href") or "",
            form_name=record.get("formName") or "",
            has_click_handler=bool(record.get("hasClickHandler")),
        ),
    )


def build_nodes(
    records: list[dict[str, Any]],
    *,
    frame_path: tuple[str, ...],
    ref_prefix: str,
) -> list[Node]:
    """Convert one frame's raw capture into nodes.

    Refs encode the frame and the element's index within that frame's capture,
    which is how the driver maps a ref back to a live element handle.
    """
    return [
        to_node(record, ref=f"{ref_prefix}{record['index']}", frame_path=frame_path)
        for record in records
    ]

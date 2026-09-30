"""Render an observation as text a model can reason about.

Two rules shape this.

*Grids are rendered as grids.* Listing a table's cells one per line loses the
only thing that makes them meaningful -- which column a value sits under. The
model is shown the reassembled table instead.

*Derived names are marked.* A name that came from a neighbouring cell rather
than from a real label is flagged with a tilde. The model is choosing what to
build a durable locator on, so it should be able to see which names the
surface actually asserted and which the surface layer inferred.
"""

from __future__ import annotations

from .model import Node, Observation

TILDE = "~"


def _flag(node: Node) -> str:
    return TILDE if node.name_is_derived else " "


def _masked(value: str) -> str:
    """A value's shape, without the value."""
    return f"<{len(value)} chars>" if value else ""


# Roles whose name is the application's own vocabulary -- a button caption, a
# column header, a field label. Safe to write down, and the only thing a
# locator failure is debugged from.
VOCABULARY_ROLES = frozenset({
    "button", "link", "textbox", "combobox", "checkbox", "radio",
    "menuitem", "columnheader", "table", "form",
})


def _name_is_content(node: Node) -> bool:
    """True when a node's name is page content rather than app vocabulary.

    A cell named by its own text, or a text block, is named after whatever it
    happens to say -- which on a member record is the member. A cell named by
    its column or its adjacent label is named after the application.
    """
    if node.role in VOCABULARY_ROLES:
        return False
    return node.name_source in {"text", "none"}


def render_node(node: Node, *, reveal: bool = True) -> str:
    parts = [f"  {_flag(node)}{node.ref:<15} {node.role:<12}"]
    if node.name and not reveal and _name_is_content(node):
        parts.append(f"<named by its own text, {len(node.name)} chars>")
    else:
        parts.append(f'"{node.name}"' if node.name else "(unnamed)")
    if node.value:
        parts.append(f"= {node.value!r}" if reveal else f"= {_masked(node.value)}")
    if node.options:
        shown = ", ".join(node.options[:6])
        more = "" if len(node.options) <= 6 else f", +{len(node.options) - 6} more"
        parts.append(f"[{shown}{more}]")
    if not node.enabled:
        parts.append("(disabled)")
    if node.hints.has_click_handler and node.role == "cell":
        parts.append("(clickable)")
    return " ".join(parts)


def render_table(observation: Observation, table_index: int,
                 *, reveal: bool = True) -> list[str]:
    rows = observation.rows(table_index)
    if not rows:
        return []

    headers: list[str] = []
    for _, cells in sorted(rows.items()):
        for header in cells:
            if header not in headers:
                headers.append(header)
    if not headers:
        return []

    # Each row carries the ref of its first cell, so a caller can act on the
    # row -- open this member's record, select that account. Without it a grid
    # is readable but not usable, and a flow whose whole point is
    # search → detail → action has no way to take the middle step.
    lines = [
        f"  table {table_index}:",
        "    | " + " | ".join(["ref", *headers]) + " |",
    ]
    for _, cells in sorted(rows.items()):
        present = [cells[h] for h in headers if cells.get(h) is not None]
        ref = present[0].ref if present else ""
        values = [
            ((cell.text if reveal else _masked(cell.text)) if (cell := cells.get(h)) else "")
            for h in headers
        ]
        lines.append("    | " + " | ".join([ref, *values]) + " |")
    return lines


def render(observation: Observation, *, include_tables: bool = True,
           reveal: bool = True) -> str:
    """A compact, frame-grouped view of everything perceivable.

    ``reveal=False`` keeps every control, name, column and coordinate and
    replaces the *values* with their length. That is the form written into
    evidence: what a locator failure needs is the structure of the page, and
    a page of a member's record is exactly the regulated data §3.4 says must
    not be written down. Schema-driven redaction cannot help here, because it
    only knows the values a capability declared -- a member's name appears on
    screen without any capability ever naming it.
    """
    lines = [
        f"url:   {observation.url}",
        f"title: {observation.title}",
    ]
    if observation.truncated:
        lines.append("note:  capture was truncated; the page is larger than shown")

    table_indices = sorted(
        {n.table.table_index for n in observation.nodes if n.table is not None}
    )

    by_frame: dict[tuple[str, ...], list[Node]] = {}
    for node in observation.nodes:
        by_frame.setdefault(node.frame_path, []).append(node)

    for frame_path, nodes in by_frame.items():
        # Cells are shown in the table rendering rather than twice over.
        loose = [n for n in nodes if n.table is None]
        if not loose and not table_indices:
            continue
        lines.append("")
        lines.append(f"[{'/'.join(frame_path)}]")
        for node in loose:
            lines.append(render_node(node, reveal=reveal))

    if include_tables:
        for index in table_indices:
            rendered = render_table(observation, index, reveal=reveal)
            if rendered:
                lines.append("")
                lines.extend(rendered)

    if any(n.name_is_derived for n in observation.nodes):
        lines.append("")
        lines.append(f"  {TILDE} = name inferred by the surface layer, not asserted by the application")

    return "\n".join(lines)

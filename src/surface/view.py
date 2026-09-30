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


def render_node(node: Node) -> str:
    parts = [f"  {_flag(node)}{node.ref:<15} {node.role:<12}"]
    parts.append(f'"{node.name}"' if node.name else "(unnamed)")
    if node.value:
        parts.append(f"= {node.value!r}")
    if node.options:
        shown = ", ".join(node.options[:6])
        more = "" if len(node.options) <= 6 else f", +{len(node.options) - 6} more"
        parts.append(f"[{shown}{more}]")
    if not node.enabled:
        parts.append("(disabled)")
    if node.hints.has_click_handler and node.role == "cell":
        parts.append("(clickable)")
    return " ".join(parts)


def render_table(observation: Observation, table_index: int) -> list[str]:
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
        values = [(cell.text if (cell := cells.get(h)) else "") for h in headers]
        lines.append("    | " + " | ".join([ref, *values]) + " |")
    return lines


def render(observation: Observation, *, include_tables: bool = True) -> str:
    """A compact, frame-grouped view of everything perceivable."""
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
            lines.append(render_node(node))

    if include_tables:
        for index in table_indices:
            rendered = render_table(observation, index)
            if rendered:
                lines.append("")
                lines.extend(rendered)

    if any(n.name_is_derived for n in observation.nodes):
        lines.append("")
        lines.append(f"  {TILDE} = name inferred by the surface layer, not asserted by the application")

    return "\n".join(lines)

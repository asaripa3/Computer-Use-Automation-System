"""The query API, tested against hand-built nodes.

These are the primitives every layer above the surface layer will use to say what it
wants from a page. They are worth testing without a browser because their
behaviour at the edges -- no match, several matches, a key that is not there --
is what the replay engine will have to make decisions on.
"""

from __future__ import annotations

import pytest

from surface.model import Node, Observation, TableCell


def cell(text: str, *, table: int, row: int, col: int, header: str, is_header: bool = False) -> Node:
    return Node(
        ref=f"c{table}-{row}-{col}",
        role="columnheader" if is_header else "cell",
        name=header,
        name_source="text" if is_header else "column-header",
        text=text,
        table=TableCell(
            table_index=table, row=row, column=col,
            column_header=header, row_header=None, is_header=is_header,
        ),
    )


@pytest.fixture
def accounts() -> Observation:
    """A stand-in for the member detail grid."""
    headers = ["Account No", "Description", "Status", "Current Balance"]
    rows = [
        ["0001234501", "Regular Savings", "ACTIVE", "4,821.37"],
        ["0001234502", "Free Checking", "ACTIVE", "1,290.04"],
        ["0001234503", "Holiday Club Savings", "DORMANT", "310.00"],
    ]
    nodes = [
        cell(h, table=0, row=0, col=i, header=h, is_header=True)
        for i, h in enumerate(headers)
    ]
    for r, values in enumerate(rows, start=1):
        nodes += [
            cell(v, table=0, row=r, col=i, header=headers[i])
            for i, v in enumerate(values)
        ]
    nodes += [
        Node(ref="p1", role="cell", name="Date of Birth", name_source="text",
             text="Date of Birth"),
        Node(ref="p2", role="cell", name="Date of Birth", name_source="adjacent-label",
             text="03/11/1974"),
        Node(ref="t1", role="textbox", name="Member Number", name_source="adjacent-label",
             frame_path=("main", "contentFrame")),
        Node(ref="t2", role="textbox", name="Member Number", name_source="label-element",
             frame_path=("main", "contentFrame")),
        Node(ref="b1", role="button", name="Search", name_source="value"),
        Node(ref="h1", role="button", name="Hidden", name_source="value", visible=False),
    ]
    return Observation(url="http://x/member/12345", title="Member Detail", nodes=tuple(nodes))


# -- find ------------------------------------------------------------------

def test_find_matches_names_case_and_punctuation_insensitively(accounts):
    # Legacy captions carry trailing colons and non-breaking spaces
    # inconsistently; a caller should not have to guess which.
    assert accounts.find(role="button", name="search")
    assert accounts.find(role="button", name="Search:")
    assert accounts.find(role="button", name="  SEARCH ")


def test_find_hides_invisible_nodes_by_default(accounts):
    assert accounts.find(name="Hidden") == []
    assert len(accounts.find(name="Hidden", visible=None)) == 1


def test_find_can_filter_to_a_frame(accounts):
    assert len(accounts.find(frame="contentFrame")) == 2
    assert accounts.find(frame="navFrame") == []


def test_find_can_filter_to_interactive_nodes(accounts):
    roles = {n.role for n in accounts.find(interactive=True)}
    assert roles == {"textbox", "button"}


# -- field -----------------------------------------------------------------

def test_field_prefers_an_authoritative_name_over_a_derived_one(accounts):
    # Two controls answer to "Member Number"; the one with a real label is the
    # safer thing to target, so it wins regardless of capture order.
    assert accounts.field("Member Number").ref == "t2"


def test_field_returns_none_when_nothing_matches(accounts):
    assert accounts.field("Sort Code") is None


# -- grid ------------------------------------------------------------------

def test_rows_reassembles_the_grid_without_the_header_row(accounts):
    rows = accounts.rows(0)
    assert len(rows) == 3
    assert rows[1]["Account No"].text == "0001234501"
    assert rows[3]["Current Balance"].text == "310.00"


def test_cell_looks_a_value_up_by_a_key_in_another_column(accounts):
    found = accounts.cell(
        column="Current Balance", where_column="Account No", where_value="0001234501"
    )
    assert found.text == "4,821.37"


def test_cell_keys_are_matched_leniently(accounts):
    found = accounts.cell(
        column="Status", where_column="Description", where_value="  regular savings "
    )
    assert found.text == "ACTIVE"


def test_cell_returns_none_for_a_key_that_is_not_present(accounts):
    # Absence is an answer, not an exception. A member who does not hold the
    # account being asked about is a business outcome for the caller to report,
    # and raising here would make it look like a crash.
    assert accounts.cell(
        column="Current Balance", where_column="Account No", where_value="9999999999"
    ) is None


def test_cell_returns_none_for_a_column_that_is_not_present(accounts):
    assert accounts.cell(
        column="Interest Rate", where_column="Account No", where_value="0001234501"
    ) is None


def test_cell_without_a_key_takes_the_first_row(accounts):
    assert accounts.cell(column="Account No").text == "0001234501"


# -- label/value panels ----------------------------------------------------

def test_value_cell_returns_the_value_not_the_caption(accounts):
    assert accounts.value_cell("Date of Birth").text == "03/11/1974"


def test_value_cell_returns_none_when_only_a_caption_exists():
    observation = Observation(
        url="http://x", title="t",
        nodes=(Node(ref="a", role="cell", name="Branch", name_source="text", text="Branch"),),
    )
    assert observation.value_cell("Branch") is None


# -- refs ------------------------------------------------------------------

def test_refs_resolve_within_the_observation_that_produced_them(accounts):
    assert accounts.by_ref("b1").name == "Search"
    assert accounts.by_ref("nope") is None

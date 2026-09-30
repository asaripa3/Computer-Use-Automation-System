"""Walking the locator ladder. Tested without a browser.

The behaviour that matters is at the edges: what happens when the recorded
candidate stops working, when a candidate matches more than one control, and
when nothing matches at all. Those three cases are the difference between an
automation that reports a problem and one that acts on the wrong control.
"""

from __future__ import annotations

import pytest

from contract.locator import ASSERTED, DERIVED, POSITIONAL, Locator, LocatorCandidate
from contract.values import Value
from replay.resolve import Resolution, Unresolved, resolve
from surface.model import Node, Observation, TableCell, WebHints


def node(**kw) -> Node:
    base = dict(ref="r1", role="textbox", name="Member Number",
                name_source="adjacent-label", frame_path=("main", "contentFrame"),
                path=("table[1]", "td[2]", "input[1]"),
                hints=WebHints(tag="input", field_name="txtMemberNo"))
    base.update(kw)
    return Node(**base)


def observation(*nodes: Node, frames=(("main",), ("main", "contentFrame"))) -> Observation:
    return Observation(url="http://x/console/search", title="Member Lookup",
                       nodes=nodes, frames=frames)


ASSERTED_ID = LocatorCandidate("asserted_id", ASSERTED, id_kind="field_name",
                               id_value="txtMemberNo")
ROLE_NAME = LocatorCandidate("role_and_name", DERIVED, role="textbox",
                             name="Member Number", name_source="adjacent-label")
STRUCTURAL = LocatorCandidate("structural_path", POSITIONAL,
                              path=("table[1]", "td[2]", "input[1]"))


def ladder(*candidates, frame=None) -> Locator:
    return Locator("the Member Number field", candidates, frame=frame)


# -- the happy case --------------------------------------------------------

def test_the_most_trusted_candidate_wins():
    found = resolve(ladder(ASSERTED_ID, ROLE_NAME, STRUCTURAL), observation(node()))
    assert isinstance(found, Resolution)
    assert found.rung == "asserted_id"
    assert found.drifted is False


def test_resolution_reports_which_candidates_were_tried():
    broken = LocatorCandidate("asserted_id", ASSERTED, id_kind="field_name", id_value="gone")
    found = resolve(ladder(broken, ROLE_NAME), observation(node()))
    assert found.tried == ("asserted_id", "role_and_name")


# -- drift -----------------------------------------------------------------

def test_falling_to_a_lower_rung_is_reported_as_drift():
    """The early warning the ladder exists to produce.

    The run still works. But the candidate the recorder trusted most no longer
    resolves, and saying so now is what makes it possible to fix the artifact
    before the next release breaks it outright.
    """
    renamed = LocatorCandidate("asserted_id", ASSERTED, id_kind="field_name",
                               id_value="txtMemberNo_v5")
    found = resolve(ladder(renamed, ROLE_NAME, STRUCTURAL), observation(node()))
    assert isinstance(found, Resolution)
    assert found.rung == "role_and_name"
    assert found.drifted is True


def test_no_drift_when_the_recorded_candidate_still_works():
    found = resolve(ladder(ASSERTED_ID, ROLE_NAME), observation(node()))
    assert found.drifted is False


# -- ambiguity is not the same as absence ---------------------------------

def test_a_candidate_matching_two_controls_does_not_resolve():
    """Acting on "the first of two" is a coin flip against a banking screen."""
    twin = node(ref="r2", hints=WebHints(tag="input", field_name="txtOther"))
    found = resolve(ladder(ROLE_NAME), observation(node(), twin))
    assert isinstance(found, Unresolved)
    assert found.ambiguous is True
    assert found.kind == "target_ambiguous"


def test_an_ambiguous_candidate_still_lets_a_lower_one_disambiguate():
    twin = node(ref="r2", hints=WebHints(tag="input", field_name="txtOther"),
                path=("table[1]", "td[4]", "input[1]"))
    found = resolve(ladder(ROLE_NAME, STRUCTURAL), observation(node(), twin))
    assert isinstance(found, Resolution)
    assert found.rung == "structural_path"


def test_nothing_matching_is_reported_differently_from_too_many_matching():
    # They need different fixes: one means the control moved or was renamed,
    # the other means the recorded description was never specific enough.
    found = resolve(ladder(ASSERTED_ID), observation())
    assert isinstance(found, Unresolved)
    assert found.ambiguous is False
    assert found.kind == "target_not_found"


def test_the_explanation_names_the_target_and_what_was_tried():
    found = resolve(ladder(ASSERTED_ID, ROLE_NAME), observation())
    assert "Member Number" in found.detail
    assert "asserted_id" in found.detail


# -- provenance disambiguates a label/value panel -------------------------

def test_a_caption_and_its_value_are_told_apart_by_provenance():
    """Both halves of a panel answer to the same name.

    Only where the name came from distinguishes them, which is why the
    recorded candidate carries `name_source` and matching honours it.
    """
    caption = Node(ref="c1", role="cell", name="Date of Birth",
                   name_source="text", text="Date of Birth")
    value = Node(ref="c2", role="cell", name="Date of Birth",
                 name_source="adjacent-label", text="03/11/1974")
    locator = ladder(LocatorCandidate("role_and_name", DERIVED, role="cell",
                                      name="Date of Birth",
                                      name_source="adjacent-label"))
    found = resolve(locator, observation(caption, value))
    assert isinstance(found, Resolution)
    assert found.node.text == "03/11/1974"


# -- frames ----------------------------------------------------------------

def test_a_frame_narrows_the_search_when_that_frame_is_present():
    in_nav = node(ref="n1", frame_path=("main", "navFrame"),
                  hints=WebHints(tag="input", field_name="txtMemberNo"))
    in_content = node(ref="c1", frame_path=("main", "contentFrame"))
    found = resolve(ladder(ASSERTED_ID, frame="contentFrame"),
                    observation(in_nav, in_content))
    assert found.node.ref == "c1"


def test_a_recorded_frame_is_ignored_when_the_page_has_no_frames():
    """The same page is framed through the console and unframed by direct link.

    A capability that only worked one of those ways would be brittle for a
    reason no user could see, so the frame disambiguates when it exists and is
    not a requirement when it does not.
    """
    found = resolve(ladder(ASSERTED_ID, frame="contentFrame"),
                    observation(node(frame_path=("main",)), frames=(("main",),)))
    assert isinstance(found, Resolution)


# -- grids -----------------------------------------------------------------

def grid_observation(*rows: tuple[str, str]) -> Observation:
    nodes = [
        Node(ref="h0", role="columnheader", name="Member No", name_source="text",
             text="Member No",
             table=TableCell(0, 0, 0, "Member No", None, is_header=True)),
        Node(ref="h1", role="columnheader", name="Name", name_source="text", text="Name",
             table=TableCell(0, 0, 1, "Name", None, is_header=True)),
    ]
    for index, (number, name) in enumerate(rows, start=1):
        nodes.append(Node(ref=f"a{index}", role="cell", name="Member No",
                          name_source="column-header", text=number,
                          table=TableCell(0, index, 0, "Member No", number)))
        nodes.append(Node(ref=f"b{index}", role="cell", name="Name",
                          name_source="column-header", text=name,
                          table=TableCell(0, index, 1, "Name", number)))
    return observation(*nodes)


def test_a_grid_cell_is_found_by_a_key_in_another_column():
    candidate = LocatorCandidate("grid_cell", DERIVED, column="Name",
                                 key_column="Member No",
                                 key_value=Value(from_input="member_id"))
    found = resolve(Locator("the matching row", (candidate,)),
                    grid_observation(("12345", "Ashworth, Dolores"),
                                     ("22001", "Vandermeer, Harold")),
                    inputs={"member_id": "22001"})
    assert isinstance(found, Resolution)
    assert found.node.text == "Vandermeer, Harold"


def test_a_key_that_is_not_in_the_grid_does_not_resolve():
    candidate = LocatorCandidate("grid_cell", DERIVED, column="Name",
                                 key_column="Member No",
                                 key_value=Value(from_input="member_id"))
    found = resolve(Locator("the matching row", (candidate,)),
                    grid_observation(("12345", "Ashworth, Dolores")),
                    inputs={"member_id": "99999"})
    assert isinstance(found, Unresolved)


def test_an_invisible_control_is_not_a_match():
    hidden = node(visible=False)
    assert isinstance(resolve(ladder(ASSERTED_ID), observation(hidden)), Unresolved)

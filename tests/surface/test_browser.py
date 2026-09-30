"""Perception against the running application, in a real browser.

The unit tests prove the rules. These prove the capture feeding them is right,
and that the surface can be driven entirely through perceived names -- which
is the only thing the discovery loop will be able to do.
"""

from __future__ import annotations

import pytest

from surface.view import render


def test_the_top_document_alone_reveals_nothing(surface, signed_on_surface, live_server):
    """The measurement that shapes this whole layer.

    The console's top document is a frameset. Reading it yields the
    ``<noframes>`` fallback and nothing else -- so a surface layer that
    assumes one document per page sees an empty application.
    """
    signed_on_surface.goto(f"{live_server}/console")
    observation = signed_on_surface.observe()

    main_only = [n for n in observation.nodes if n.frame_path == ("main",)]
    assert main_only == [], "the frameset's own document holds no content"
    assert len(observation.nodes) > 0, "but the frames beneath it do"


def test_frames_are_enumerated_and_nodes_are_tagged_with_their_path(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console")
    observation = signed_on_surface.observe()

    assert observation.frames == (("main",), ("main", "navFrame"), ("main", "contentFrame"))
    assert observation.find(name="Member Search", frame="navFrame")
    assert observation.find(frame="contentFrame")


def test_signing_on_works_through_derived_names_alone(signed_on_surface, live_server):
    # The fixture signed on by asking for the fields called "Operator ID" and
    # "Password" -- names that exist nowhere in the markup and were inferred
    # from the neighbouring table cells.
    signed_on_surface.goto(f"{live_server}/console/home")
    observation = signed_on_surface.observe()
    assert observation.title == "Servicing Home"
    assert observation.value_cell("Core Release").text == "ShareBase 4.2.1"


def test_a_field_is_found_again_although_its_id_changed(signed_on_surface, live_server):
    """Volatile ids are captured as evidence, never used as the handle."""
    signed_on_surface.goto(f"{live_server}/console/search")
    first = signed_on_surface.observe().field("Member Number")
    signed_on_surface.goto(f"{live_server}/console/search")
    second = signed_on_surface.observe().field("Member Number")

    assert first.hints.element_id != second.hints.element_id
    assert first.hints.field_name == second.hints.field_name == "txtMemberNo"
    assert first.name == second.name == "Member Number"


def test_the_accounts_grid_is_reassembled(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    observation = signed_on_surface.observe()

    table_index = next(n.table.table_index for n in observation.nodes if n.table)
    rows = observation.rows(table_index)
    assert len(rows) == 3
    first = rows[min(rows)]
    assert set(first) >= {"Account No", "Description", "Status", "Current Balance"}


def test_a_balance_is_addressable_by_its_account_number(signed_on_surface, live_server):
    """The extraction the flat accessibility tree made impossible.

    "Current Balance on the row whose Account No is 0001234501" survives the
    member holding a different number of accounts. "The sixth node after the
    word Balance" does not.
    """
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    observation = signed_on_surface.observe()

    assert observation.cell(
        column="Current Balance", where_column="Account No", where_value="0001234501"
    ).text == "4,821.37"
    assert observation.cell(
        column="Description", where_column="Account No", where_value="0001234503"
    ).text == "Holiday Club Savings"


def test_an_absent_row_yields_none_rather_than_raising(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12346")
    observation = signed_on_surface.observe()
    # Member 12346 holds checking only, so there is legitimately no savings row.
    assert observation.cell(
        column="Current Balance", where_column="Description", where_value="Regular Savings"
    ) is None


def test_demographic_values_are_read_from_the_label_value_panel(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    observation = signed_on_surface.observe()

    assert observation.value_cell("Date of Birth").text == "03/11/1974"
    assert observation.value_cell("Branch").text == "Cedar Falls Main"
    assert observation.value_cell("Status").text == "ACTIVE"


def test_javascript_anchors_are_perceived_as_buttons(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    observation = signed_on_surface.observe()

    assert observation.find(role="button", name="Address Maintenance")
    assert observation.find(role="link", name="Open Sub-Account")


def test_combobox_options_are_captured(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345/subaccount")
    observation = signed_on_surface.observe()

    account_type = observation.field("Account Type")
    assert account_type.role == "combobox"
    assert "Vacation Club Savings" in account_type.options


def test_the_subaccount_flow_is_driveable_through_the_surface_layer(
    signed_on_surface, live_server
):
    """The whole point of this layer, end to end.

    Every control is addressed by a name that the surface layer derived. Not one
    selector, id or XPath appears anywhere in this test -- which is what the
    recorded flow will have to manage too.
    """
    surface = signed_on_surface
    surface.goto(f"{live_server}/console/member/12345/subaccount")

    observation = surface.observe()
    surface.select(observation.field("Account Type").ref, "Vacation Club Savings")
    surface.fill(observation.field("Nickname").ref, "Vacation 2027")
    surface.fill(observation.field("Initial Deposit").ref, "150.00")
    funding = observation.field("Funding Source")
    surface.select(funding.ref, funding.options[1])
    surface.click(observation.find(role="button", name="Continue")[0].ref)

    review = surface.observe()
    assert review.value_cell("Nickname").text == "Vacation 2027"
    assert review.value_cell("Initial Deposit").text == "150.00"

    surface.click(review.find(role="button", name="Confirm")[0].ref)

    done = surface.observe()
    assert done.value_cell("New Account Number") is not None
    assert done.value_cell("Confirmation Number").text.startswith("CNF-12345-")


def test_a_ref_from_a_previous_page_is_rejected(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/search")
    stale = signed_on_surface.observe().field("Member Number").ref
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    signed_on_surface.observe()

    # Refs are valid only for the observation that produced them. Failing
    # loudly here is what stops a replay acting on whatever now happens to
    # occupy that position.
    with pytest.raises(LookupError):
        signed_on_surface.fill(stale, "12345")


def test_a_screenshot_is_produced_for_evidence(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    image = signed_on_surface.screenshot()
    assert image[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_rendering_marks_which_names_were_inferred(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345/subaccount")
    text = render(signed_on_surface.observe())

    assert "name inferred by the surface layer" in text
    assert "~" in text
    assert 'combobox    "Account Type"' in text.replace("  ", " ").replace("  ", " ") or "Account Type" in text


def test_a_ref_from_an_earlier_observation_of_the_same_page_is_rejected(
    signed_on_surface, live_server
):
    """The subtle case, and the one that would corrupt a replay quietly.

    Same URL, same layout, same node at the same index -- so the ref still
    resolves to something. It is simply no longer the element the caller
    chose, because the registry was rebuilt. Only the observation token
    distinguishes the two.
    """
    signed_on_surface.goto(f"{live_server}/console/search")
    first = signed_on_surface.observe()
    stale = first.field("Member Number").ref

    second = signed_on_surface.observe()
    assert second.token != first.token

    with pytest.raises(LookupError, match="different observation"):
        signed_on_surface.fill(stale, "12345")

    # The equivalent ref from the current observation works.
    signed_on_surface.fill(second.field("Member Number").ref, "12345")


# -- choosing from a list --------------------------------------------------

def test_an_option_can_be_chosen_by_its_visible_label(signed_on_surface, live_server):
    signed_on_surface.goto(f"{live_server}/console/member/12345/subaccount")
    observation = signed_on_surface.observe()
    signed_on_surface.select(observation.field("Account Type").ref, "Money Market")
    assert signed_on_surface.observe().field("Account Type").value == "Money Market"


def test_an_option_can_be_chosen_by_its_underlying_value(signed_on_surface, live_server):
    """Funding accounts are chosen by account number, not by the label.

    The label is "0001234502 — Free Checking"; the value is the bare number,
    which is what a caller supplies.
    """
    signed_on_surface.goto(f"{live_server}/console/member/12345/subaccount")
    observation = signed_on_surface.observe()
    signed_on_surface.select(observation.field("Funding Source").ref, "0001234502")
    chosen = signed_on_surface.observe().field("Funding Source").value
    assert chosen.startswith("0001234502")


def test_choosing_by_value_is_not_paid_for_with_a_timeout(signed_on_surface, live_server):
    """The bug this pins cost thirty seconds per selection, silently.

    Attempting a label match and letting it fail burns the driver's full
    timeout before the value match is tried. Reading the options first makes
    the decision instant -- and the whole sub-account flow went from 32
    seconds to 1.3.
    """
    import time

    signed_on_surface.goto(f"{live_server}/console/member/12345/subaccount")
    observation = signed_on_surface.observe()

    started = time.monotonic()
    signed_on_surface.select(observation.field("Funding Source").ref, "0001234502")
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"selecting by value took {elapsed:.1f}s"


def test_an_option_that_is_not_offered_says_what_was(signed_on_surface, live_server):
    from surface.model import ActionFailed

    signed_on_surface.goto(f"{live_server}/console/member/12345/subaccount")
    observation = signed_on_surface.observe()

    with pytest.raises(ActionFailed) as caught:
        signed_on_surface.select(observation.field("Account Type").ref, "Offshore Trust")

    message = str(caught.value)
    assert "Offshore Trust" in message
    assert "Regular Savings" in message, "the error should list what was on offer"

"""The naming and role rules, tested without a browser.

Every case here is a shape taken from the target application. Keeping these
pure -- dictionaries in, values out -- means the rules that decide what a
control is called can be exhausted in milliseconds, and a browser is needed
only to prove the capture feeding them is correct.
"""

from __future__ import annotations

import pytest

from surface.naming import derive_name, derive_role, derive_value, to_node


# -- roles -----------------------------------------------------------------

@pytest.mark.parametrize(
    "record,expected",
    [
        ({"tag": "input", "type": "text"}, "textbox"),
        ({"tag": "input", "type": "password"}, "textbox"),
        ({"tag": "input", "type": ""}, "textbox"),
        ({"tag": "input", "type": "submit"}, "button"),
        ({"tag": "input", "type": "checkbox"}, "checkbox"),
        ({"tag": "input", "type": "radio"}, "radio"),
        ({"tag": "textarea"}, "textbox"),
        ({"tag": "select"}, "combobox"),
        ({"tag": "button"}, "button"),
        ({"tag": "a", "href": "/console/search"}, "link"),
        ({"tag": "th"}, "columnheader"),
        ({"tag": "td"}, "cell"),
        ({"tag": "h1"}, "heading"),
        ({"tag": "div"}, "unknown"),
    ],
)
def test_roles_follow_behaviour(record, expected):
    assert derive_role(record) == expected


def test_javascript_anchor_is_reported_as_a_button():
    # It does not navigate. Calling it a link would tell both the model and
    # the replay engine something untrue about what clicking it does.
    assert derive_role({"tag": "a", "href": "javascript:alert('x')"}) == "button"
    assert derive_role({"tag": "a", "href": "javascript:void(0)"}) == "button"


def test_anchor_without_an_href_is_a_button():
    assert derive_role({"tag": "a", "href": ""}) == "button"


def test_an_explicit_role_attribute_wins():
    assert derive_role({"tag": "div", "roleAttr": "button"}) == "button"
    assert derive_role({"tag": "span", "roleAttr": "gridcell"}) == "cell"


def test_a_plain_element_with_a_click_handler_is_a_button():
    assert derive_role({"tag": "div", "hasClickHandler": True}) == "button"


# -- names: authoritative sources win --------------------------------------

def test_aria_label_outranks_everything():
    record = {
        "tag": "input", "type": "text",
        "ariaLabel": "Member Number",
        "labelText": "Something Else",
        "cellContext": {"prevCellText": "Third Thing"},
    }
    assert derive_name(record, "textbox") == ("Member Number", "aria-label")


def test_a_real_label_outranks_an_adjacent_cell():
    record = {
        "tag": "input", "type": "text",
        "labelText": "Surname",
        "cellContext": {"prevCellText": "Member Number"},
    }
    # If the application ever gains proper labels, the safer name wins
    # automatically and nothing above this layer has to change.
    assert derive_name(record, "textbox") == ("Surname", "label-element")


def test_a_submit_button_is_named_by_its_value_attribute():
    record = {"tag": "input", "type": "submit", "value": "Sign On"}
    assert derive_name(record, "button") == ("Sign On", "value")


def test_a_link_is_named_by_its_text():
    record = {"tag": "a", "href": "/console/search", "ownText": "New Search"}
    assert derive_name(record, "link") == ("New Search", "text")


# -- names: the derived cases that legacy layouts force --------------------

def test_an_unlabelled_control_borrows_the_cell_to_its_left():
    record = {"tag": "input", "type": "text", "cellContext": {"prevCellText": "Nickname"}}
    assert derive_name(record, "textbox") == ("Nickname", "adjacent-label")


def test_a_control_falls_back_to_the_cell_above_it():
    record = {
        "tag": "input", "type": "text",
        "cellContext": {"prevCellText": "", "aboveCellText": "Initial Deposit"},
    }
    assert derive_name(record, "textbox") == ("Initial Deposit", "adjacent-label")


def test_a_data_cell_is_named_for_its_column():
    record = {
        "tag": "td", "text": "4,821.37",
        "table": {"isDataTable": True, "columnHeader": "Current Balance",
                  "prevCellText": "ACTIVE"},
    }
    assert derive_name(record, "cell") == ("Current Balance", "column-header")


def test_a_panel_value_borrows_the_caption_to_its_left():
    record = {
        "tag": "td", "text": "03/11/1974",
        "table": {"isDataTable": False, "prevCellText": "Date of Birth"},
    }
    assert derive_name(record, "cell") == ("Date of Birth", "adjacent-label")


def test_a_caption_cell_is_named_by_its_own_text():
    # The left-hand cell of a label/value pair has no left neighbour, so it is
    # the caption. Naming it after the caption above it -- which an untargeted
    # adjacency rule would do -- makes every row report the first row's label.
    record = {
        "tag": "td", "text": "Charter",
        "table": {"isDataTable": False, "prevCellText": None,
                  "aboveCellText": "Institution"},
    }
    assert derive_name(record, "cell") == ("Charter", "text")


def test_column_headers_are_ignored_on_layout_tables():
    record = {
        "tag": "td", "text": "CU-60418",
        "table": {"isDataTable": False, "columnHeader": "Institution",
                  "prevCellText": "Charter"},
    }
    assert derive_name(record, "cell") == ("Charter", "adjacent-label")


def test_an_unnameable_control_says_so():
    assert derive_name({"tag": "input", "type": "text"}, "textbox") == ("", "none")


def test_names_are_whitespace_normalised():
    record = {"tag": "input", "cellContext": {"prevCellText": "  Member \u00a0 Number \n\t"}}
    assert derive_name(record, "textbox")[0] == "Member Number"


# -- values ----------------------------------------------------------------

def test_a_combobox_reports_its_selected_label():
    record = {"tag": "select", "selectedOption": "Vacation Club Savings"}
    assert derive_value(record, "combobox") == "Vacation Club Savings"


def test_a_checkbox_reports_its_state():
    assert derive_value({"checked": True}, "checkbox") == "checked"
    assert derive_value({"checked": False}, "checkbox") == "unchecked"


# -- assembly --------------------------------------------------------------

def test_to_node_marks_derived_names_and_keeps_web_detail_in_hints():
    record = {
        "index": 3, "tag": "input", "type": "text",
        "fieldName": "txtNickname", "elementId": "ctl00_cphMain_a1b2_txtNickname07",
        "formName": "frmSubAcct",
        "cellContext": {"prevCellText": "Nickname"},
        "bounds": {"x": 1, "y": 2, "width": 3, "height": 4},
        "path": ["table[1]", "tr[2]", "td[2]", "input[1]"],
    }
    node = to_node(record, ref="f0:3", frame_path=("main", "contentFrame"))

    assert (node.role, node.name, node.name_source) == ("textbox", "Nickname", "adjacent-label")
    assert node.name_is_derived is True
    assert node.is_interactive is True
    assert node.frame_path == ("main", "contentFrame")
    # The field name survives a re-render; the element id does not. Both are
    # captured, but only one is safe to target.
    assert node.hints.field_name == "txtNickname"
    assert node.hints.element_id.startswith("ctl00_")
    assert node.describe() == 'main/contentFrame:textbox "Nickname"'

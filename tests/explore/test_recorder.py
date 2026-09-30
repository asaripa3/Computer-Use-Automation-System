"""Compiling a run into an artifact. Tested without a browser or a model.

The recorder's job is to turn what happened into a contract, and the
interesting cases are where those two differ: a value that must become a
parameter, a run that did not finish, a control whose name is itself the data.
"""

from __future__ import annotations

import pytest

from explore.recorder import Recorder
from surface.model import Node, TableCell, WebHints


def recorder(**kw) -> Recorder:
    base = dict(capability_id="test.capability", version="0.1.0",
                goal="read a balance", base_url="http://127.0.0.1:8080",
                entry_path="/console/search", recorded_by="scripted")
    base.update(kw)
    return Recorder(**base)


def field_node(**kw) -> Node:
    base: dict = dict(
        ref="r1", role="textbox", name="Member Number",
        name_source="adjacent-label", frame_path=("main", "contentFrame"),
        path=("table[1]", "td[2]", "input[1]"),
        hints=WebHints(tag="input", field_name="txtMemberNo"),
    )
    base.update(kw)
    return Node(**base)


def grid_node(text: str, column: str = "Member No") -> Node:
    return Node(
        ref="c1", role="cell", name=column, name_source="column-header",
        text=text, frame_path=("main", "contentFrame"),
        hints=WebHints(tag="td"),
        table=TableCell(table_index=0, row=1, column=0,
                        column_header=column, row_header=text),
    )


MEMBER_INPUT = dict(name="member_id", type="string",
                    description="The member number.", example="12345",
                    sensitivity="pii")


def complete(rec: Recorder) -> Recorder:
    """Give a recorder the minimum a valid capability needs."""
    rec.noted_outcome({"code": "MEMBER_NOT_FOUND", "kind": "business",
                       "description": "no such member",
                       "text": "No member records match"})
    rec.finished({"success_text": "Share & Deposit Accounts",
                  "success_description": "the grid is on screen",
                  "summary": "read a balance"})
    return rec


# -- parameterisation ------------------------------------------------------

def test_a_declared_input_is_typed_into_the_page_as_its_example():
    # Discovery drives the application with the declared example, so the run
    # exercises the same shape a caller will supply.
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    assert rec.example_for("member_id") == "12345"
    assert rec.example_for("not_declared") is None


def test_a_filled_value_becomes_an_input_reference():
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    rec.acted("fill", field_node(), from_input="member_id")
    step = complete(rec).build().steps[-1]
    assert step.value.from_input == "member_id"
    assert step.value.literal is None


def test_a_control_named_after_a_declared_value_is_recorded_as_a_keyed_row():
    """The canonicalisation that decides whether the capability is reusable.

    Opening a member's record means clicking the row whose number matches.
    That cell's text *is* the parameter, so recording it literally would
    produce a capability that only ever works for one member.
    """
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    rec.acted("click", grid_node("12345"))
    target = complete(rec).build().steps[-1].target

    candidate = target.candidates[0]
    assert candidate.strategy == "grid_cell"
    assert candidate.column == "Member No"
    assert candidate.key_value.from_input == "member_id"
    assert "12345" not in str(target.to_dict())


def test_a_grid_cell_unrelated_to_any_input_is_recorded_normally():
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    rec.acted("click", grid_node("Ashworth, Dolores", column="Name"))
    strategies = [c.strategy for c in complete(rec).build().steps[-1].target.ladder()]
    assert "role_and_name" in strategies


def test_locators_are_derived_from_the_page_not_supplied_by_the_caller():
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    rec.acted("fill", field_node(), from_input="member_id")
    ladder = complete(rec).build().steps[-1].target.ladder()
    assert ladder[0].strategy == "asserted_id"
    assert ladder[0].id_value == "txtMemberNo"


# -- shape of the emitted artifact ----------------------------------------

def test_a_capability_starts_by_establishing_where_it_starts():
    # A recorded run begins wherever the harness left the browser. Replayed
    # without an opening navigate it would act on the caller's current page.
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Search"))
    steps = complete(rec).build().steps
    assert steps[0].action == "navigate"
    assert steps[0].url == "/console/search"
    assert [s.index for s in steps] == [1, 2]


def test_an_explicit_opening_navigation_is_not_duplicated():
    rec = recorder()
    rec.navigated("/console/search")
    rec.acted("click", field_node(role="button", name="Search"))
    steps = complete(rec).build().steps
    assert [s.action for s in steps] == ["navigate", "click"]


def test_a_recorded_capability_is_born_as_a_draft():
    # An irreversible step may not run from an unapproved capability, so
    # nothing a model produced can commit anything until a person reads it.
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Search"))
    assert complete(rec).build().status == "draft"


def test_an_irreversible_click_is_marked_and_given_a_longer_wait():
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Confirm"), irreversible=True)
    step = complete(rec).build().steps[-1]
    assert step.risk == "irreversible"
    assert step.timeout_ms == 30_000


def test_a_checkpoint_attaches_to_the_step_just_taken():
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Search"))
    rec.expected("the results page has loaded", "Search Results")
    step = complete(rec).build().steps[-1]
    assert step.expect.text == "Search Results"


def test_the_provenance_records_the_goal_and_what_produced_it():
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Search"))
    provenance = complete(rec).build().provenance
    assert provenance.goal == "read a balance"
    assert provenance.recorded_by == "scripted"


# -- refusing to emit something unusable ----------------------------------

def test_a_run_that_never_finished_produces_nothing():
    """Half a flow that replays to the wrong screen is worse than nothing.

    It looks like a capability.
    """
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Search"))
    with pytest.raises(ValueError, match="finish was never called"):
        rec.build()


def test_a_run_with_no_declared_outcome_produces_nothing():
    rec = recorder()
    rec.acted("click", field_node(role="button", name="Search"))
    rec.finished({"success_text": "x", "success_description": "y", "summary": "z"})
    with pytest.raises(ValueError, match="non-success outcome"):
        rec.build()


def test_a_step_referencing_an_undeclared_input_is_caught():
    rec = recorder()
    rec.acted("fill", field_node(), from_input="member_id")
    assert any("undeclared input" in p for p in rec.missing())


def test_every_problem_is_listed_at_once():
    problems = recorder().missing()
    assert len(problems) >= 2


# -- declarations ----------------------------------------------------------

def test_an_output_can_be_addressed_by_column_and_key():
    rec = recorder()
    rec.declared_output({
        "name": "savings_balance", "type": "money", "description": "the balance",
        "column": "Current Balance", "key_column": "Description",
        "key_value": "Regular Savings", "optional": True,
    }, None)
    spec = rec.outputs[0]
    candidate = spec.source.candidates[0]
    assert candidate.column == "Current Balance"
    assert candidate.key_value.literal == "Regular Savings"
    assert spec.optional is True


def test_an_output_needs_either_a_control_or_a_column():
    with pytest.raises(ValueError, match="needs either a ref or a column"):
        recorder().declared_output(
            {"name": "x", "type": "string", "description": "d"}, None
        )


def test_redeclaring_an_input_replaces_it_rather_than_duplicating():
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    rec.declared_input({**MEMBER_INPUT, "description": "corrected"})
    assert len(rec.inputs) == 1
    assert rec.inputs[0].description == "corrected"


def test_the_same_outcome_noted_twice_is_recorded_once():
    rec = recorder()
    for _ in range(2):
        rec.noted_outcome({"code": "MEMBER_NOT_FOUND", "kind": "business",
                           "description": "no such member",
                           "text": "No member records match"})
    assert len(rec.outcomes) == 1


def test_an_outcome_code_is_upper_cased_for_the_caller():
    rec = recorder()
    rec.noted_outcome({"code": "member_not_found", "kind": "business",
                       "description": "d", "text": "t"})
    assert rec.outcomes[0].code == "MEMBER_NOT_FOUND"


# -- refusing to bake an invocation's values into the contract -------------

def test_a_checkpoint_containing_a_parameter_value_is_detected():
    """A real model produced exactly this, and it would break on every
    invocation but the one that was recorded."""
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    assert rec.bakes_in_a_parameter(
        "1 record(s) returned for member number 12345."
    ) == "member_id"
    assert rec.bakes_in_a_parameter("Member 12345 — Ashworth, Dolores") == "member_id"


def test_a_checkpoint_that_holds_for_any_invocation_is_accepted():
    rec = recorder()
    rec.declared_input(MEMBER_INPUT)
    assert rec.bakes_in_a_parameter("Search Results") is None
    assert rec.bakes_in_a_parameter("Share & Deposit Accounts") is None


def test_a_very_short_parameter_value_is_not_hunted_for():
    # A two-character value appears coincidentally everywhere; a member
    # number does not.
    rec = recorder()
    rec.declared_input({**MEMBER_INPUT, "example": "12"})
    assert rec.bakes_in_a_parameter("12 records returned") is None

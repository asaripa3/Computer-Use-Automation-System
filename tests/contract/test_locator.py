"""The targeting ladder: ordering, validation, and what it refuses to record."""

from __future__ import annotations

import pytest

from contract.errors import ContractError
from contract.locator import ASSERTED, DERIVED, POSITIONAL, Locator, LocatorCandidate
from contract.values import Value
from surface.model import Node, TableCell, WebHints
from contract.derive import locator_for


def candidate(**kw) -> LocatorCandidate:
    base = dict(strategy="role_and_name", robustness=DERIVED, role="textbox", name="x")
    base.update(kw)
    return LocatorCandidate(**base)


# -- ordering --------------------------------------------------------------

def test_the_ladder_puts_asserted_before_derived_before_positional():
    locator = Locator(
        "a field",
        (
            candidate(strategy="structural_path", robustness=POSITIONAL, role=None,
                      name=None, path=("td[1]",)),
            candidate(),
            candidate(strategy="asserted_id", robustness=ASSERTED, role=None, name=None,
                      id_kind="field_name", id_value="txtX"),
        ),
    )
    assert [c.robustness for c in locator.ladder()] == [ASSERTED, DERIVED, POSITIONAL]


def test_the_recorders_own_order_is_kept_within_a_tier():
    first = candidate(name="first")
    second = candidate(name="second")
    locator = Locator("a field", (first, second))
    assert [c.name for c in locator.ladder()] == ["first", "second"]


def test_a_locator_knows_when_it_has_nothing_but_a_position():
    weak = Locator("a field", (candidate(strategy="structural_path", robustness=POSITIONAL,
                                         role=None, name=None, path=("td[1]",)),))
    assert weak.is_positional_only is True
    assert weak.best_robustness == POSITIONAL


# -- validation ------------------------------------------------------------

def test_a_locator_needs_at_least_one_candidate():
    with pytest.raises(ContractError, match="at least one candidate"):
        Locator("a field", ())


@pytest.mark.parametrize(
    "kw,message",
    [
        (dict(strategy="role_and_name", role=None), "needs a role"),
        (dict(strategy="role_and_name", name=None), "needs a name"),
        (dict(strategy="asserted_id", robustness=ASSERTED, role=None, name=None,
              id_value="x"), "needs an id_kind"),
        (dict(strategy="grid_cell", role=None, name=None, column=None), "needs a column"),
    ],
)
def test_each_strategy_requires_its_own_fields(kw, message):
    with pytest.raises(ContractError, match=message):
        candidate(**kw)


def test_an_asserted_id_may_not_claim_to_be_anything_but_asserted():
    # The robustness tier is not a free-text opinion: an identifier the
    # application states about itself is asserted by definition, and letting a
    # recorder downgrade it would corrupt the ordering the ladder depends on.
    with pytest.raises(ContractError, match="by definition asserted"):
        LocatorCandidate("asserted_id", DERIVED, id_kind="field_name", id_value="x")


def test_a_structural_path_may_not_claim_to_be_robust():
    with pytest.raises(ContractError, match="positional by definition"):
        LocatorCandidate("role_and_name", POSITIONAL, role="textbox", name="x")
    with pytest.raises(ContractError, match="positional by definition"):
        LocatorCandidate("structural_path", ASSERTED, path=("td[1]",))


def test_a_grid_cell_needs_its_key_column_and_key_value_together():
    with pytest.raises(ContractError, match="together, or neither"):
        candidate(strategy="grid_cell", role=None, name=None,
                  column="Balance", key_column="Account No")


def test_an_unknown_strategy_is_refused():
    with pytest.raises(ContractError, match="not one of"):
        candidate(strategy="xpath")


# -- parameterisation ------------------------------------------------------

def test_a_locator_reports_the_inputs_it_depends_on():
    locator = Locator("a row", (candidate(
        strategy="grid_cell", role=None, name=None, column="Member No",
        key_column="Member No", key_value=Value(from_input="member_id"),
    ),))
    assert locator.inputs_used == {"member_id"}


def test_a_literal_key_depends_on_no_input():
    locator = Locator("a row", (candidate(
        strategy="grid_cell", role=None, name=None, column="Current Balance",
        key_column="Description", key_value=Value(literal="Regular Savings"),
    ),))
    assert locator.inputs_used == frozenset()


# -- deriving from an observation -----------------------------------------

def node(**kw) -> Node:
    base = dict(ref="r", role="textbox", name="Member Number",
                name_source="adjacent-label", frame_path=("main", "contentFrame"),
                path=("table[1]", "td[2]", "input[1]"),
                hints=WebHints(tag="input", field_name="txtMemberNo",
                               element_id="ctl00_cphMain_a1b2_txtMemberNo07"))
    base.update(kw)
    return Node(**base)


def test_deriving_records_every_viable_candidate_in_order():
    locator = locator_for(node(), description="the member number field")
    assert [c.strategy for c in locator.ladder()] == [
        "asserted_id", "role_and_name", "structural_path"
    ]
    assert locator.frame == "contentFrame"


def test_the_volatile_element_id_is_never_proposed():
    # Ids are regenerated on every render in this application. Capturing one
    # as evidence is useful; targeting one is a guaranteed future failure.
    locator = locator_for(node(), description="the member number field")
    assert "ctl00" not in str(locator.to_dict())


def test_an_authoritative_name_is_recorded_as_asserted():
    locator = locator_for(node(name_source="label-element"), description="d")
    role_and_name = next(c for c in locator.candidates if c.strategy == "role_and_name")
    assert role_and_name.robustness == ASSERTED


def test_an_inferred_name_is_recorded_as_derived_and_says_why():
    locator = locator_for(node(), description="d")
    role_and_name = next(c for c in locator.candidates if c.strategy == "role_and_name")
    assert role_and_name.robustness == DERIVED
    assert "inferred" in role_and_name.rationale
    assert "rebrands" in role_and_name.rationale


def test_a_grid_cell_yields_a_column_addressed_candidate():
    cell = node(role="cell", name="Current Balance", name_source="column-header",
                hints=WebHints(tag="td"),
                table=TableCell(table_index=0, row=1, column=5,
                                column_header="Current Balance", row_header="0001234501"))
    locator = locator_for(cell, description="the balance",
                          key_column="Account No", key_value=Value(literal="0001234501"))
    grid = next(c for c in locator.candidates if c.strategy == "grid_cell")
    assert (grid.column, grid.key_column) == ("Current Balance", "Account No")


def test_a_node_nothing_identifies_is_refused_rather_than_recorded_weakly():
    with pytest.raises(ValueError, match="nothing identifies"):
        locator_for(
            Node(ref="r", role="unknown", name="", name_source="none", path=()),
            description="a mystery",
        )

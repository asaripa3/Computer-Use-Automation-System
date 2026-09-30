"""Validating what comes in, and shaping what goes out."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from contract.binding import (
    BindingError, as_step_values, bind_inputs, coerce_output, coerce_outputs, jsonable,
)
from contract.capability import InputSpec, OutputSpec, Step
from contract.values import Value
from helpers_contract import FIELD, capability


def with_inputs(*specs, **kw):
    steps = kw.pop("steps", (Step(1, "fill", "f", target=FIELD,
                                  value=Value(from_input=specs[0].name)),))
    return capability(inputs=specs, steps=steps, **kw)


MEMBER = InputSpec("member_id", "string", "the member", pattern=r"^[0-9]{4,10}$",
                   sensitivity="pii")


# -- inbound: strict -------------------------------------------------------

def test_a_valid_parameter_is_accepted():
    assert bind_inputs(with_inputs(MEMBER), {"member_id": "12345"}).values == {"member_id": "12345"}


def test_a_parameter_failing_its_pattern_is_refused_before_anything_runs():
    """The only point at which a bad parameter is free.

    Once a flow has started the cheapest outcome is a confusing failure and
    the worst is a transaction against the wrong record.
    """
    with pytest.raises(BindingError, match="does not match"):
        bind_inputs(with_inputs(MEMBER), {"member_id": "'; DROP TABLE members--"})


def test_a_missing_required_parameter_is_refused():
    with pytest.raises(BindingError, match="required, but not supplied"):
        bind_inputs(with_inputs(MEMBER), {})


def test_an_undeclared_parameter_is_refused():
    # Silently ignoring it would let a typo mean the caller thinks it passed a
    # value it did not.
    with pytest.raises(BindingError, match="not declared by this capability"):
        bind_inputs(with_inputs(MEMBER), {"member_id": "12345", "membr_id": "9"})


def test_every_problem_is_reported_at_once():
    built = with_inputs(MEMBER, InputSpec("amount", "money", "d"))
    with pytest.raises(BindingError) as caught:
        bind_inputs(built, {"member_id": "abc", "amount": "not-a-number", "extra": 1})
    message = str(caught.value)
    assert "member_id" in message and "amount" in message and "extra" in message


def test_an_optional_parameter_may_be_omitted():
    built = with_inputs(MEMBER, InputSpec("note", "string", "d", required=False))
    assert bind_inputs(built, {"member_id": "12345"}).values == {"member_id": "12345"}


@pytest.mark.parametrize(
    "type_name,raw,expected",
    [
        ("integer", "1,234", 1234),
        ("money", "4,821.37", Decimal("4821.37")),
        ("money", "150", Decimal("150")),
        ("date", "03/11/1974", date(1974, 3, 11)),
        ("date", "1974-03-11", date(1974, 3, 11)),
        ("boolean", "yes", True),
        ("boolean", "unchecked", False),
    ],
)
def test_values_are_coerced_to_their_declared_type(type_name, raw, expected):
    built = with_inputs(InputSpec("v", type_name, "d"))
    assert bind_inputs(built, {"v": raw}).values["v"] == expected


@pytest.mark.parametrize("type_name,raw", [
    ("integer", "twelve"), ("money", "free"), ("date", "sometime"), ("boolean", "maybe"),
])
def test_an_uncoercible_value_is_refused(type_name, raw):
    built = with_inputs(InputSpec("v", type_name, "d"))
    with pytest.raises(BindingError):
        bind_inputs(built, {"v": raw})


def test_an_enum_must_be_one_of_its_choices():
    built = with_inputs(InputSpec("product", "enum", "d",
                                  choices=("Regular Savings", "Money Market")))
    assert bind_inputs(built, {"product": "Money Market"}).values["product"] == "Money Market"
    with pytest.raises(BindingError, match="is not one of"):
        bind_inputs(built, {"product": "Christmas Club"})


def test_coerced_values_render_back_to_what_the_form_expects():
    """Coercion gives the caller something to compute on; the form needs text.

    The rendering happens in one place rather than leaving each step to guess,
    which is how a deposit of 150 ends up typed as "150.00" and clears the
    application's minimum-deposit check.
    """
    built = with_inputs(InputSpec("deposit", "money", "d"),
                        InputSpec("opened", "date", "d", required=False),
                        steps=(Step(1, "fill", "f", target=FIELD,
                                    value=Value(from_input="deposit")),))
    rendered = as_step_values(bind_inputs(built, {"deposit": "150", "opened": "1974-03-11"}))
    assert rendered["deposit"] == "150.00"
    assert rendered["opened"] == "03/11/1974"


# -- outbound: forgiving, then exact ---------------------------------------

def test_an_extracted_display_value_is_given_its_declared_shape():
    spec = OutputSpec("balance", "money", "d", source=FIELD)
    assert coerce_output(spec, "4,821.37") == Decimal("4821.37")


def test_a_missing_optional_output_is_none():
    spec = OutputSpec("balance", "money", "d", source=FIELD, optional=True)
    assert coerce_output(spec, None) is None
    assert coerce_output(spec, "  ") is None


def test_a_missing_required_output_is_an_error_not_an_empty_string():
    # A required output silently coming back empty is how a caller ends up
    # acting on a balance it never actually read.
    spec = OutputSpec("balance", "money", "d", source=FIELD)
    with pytest.raises(BindingError, match="was not found on the page"):
        coerce_output(spec, None)


def test_outputs_are_coerced_together():
    built = capability(outputs=(
        OutputSpec("name", "string", "d", source=FIELD),
        OutputSpec("balance", "money", "d", source=FIELD, optional=True),
    ))
    out = coerce_outputs(built, {"name": "Ashworth, Dolores", "balance": None})
    assert out == {"name": "Ashworth, Dolores", "balance": None}


def test_coerced_values_serialise_without_losing_precision():
    assert jsonable(Decimal("4821.37")) == "4821.37"
    assert jsonable(date(1974, 3, 11)) == "1974-03-11"

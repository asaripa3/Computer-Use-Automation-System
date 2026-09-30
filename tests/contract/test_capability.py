"""The artifact as a whole: cross-field coherence and round-tripping.

Per-field rules live with each dataclass. What is tested here is the
consistency a reviewer would otherwise have to hold in their head -- and every
incoherence caught at load time is one that would otherwise surface as a
confusing failure partway through a run against member records.
"""

from __future__ import annotations

import pytest

from contract import io
from contract.capability import (
    Capability, Condition, InputSpec, OutcomeSpec, OutputSpec, RecoverySpec, Step,
)
from contract.errors import ContractError
from contract.values import Value
from helpers_contract import BUTTON, FIELD, NOT_FOUND, OK, capability


# -- coherence -------------------------------------------------------------

def test_a_valid_capability_reports_its_reference():
    assert capability().ref == "member.savings_balance@1.0.0"


def test_step_indices_must_be_a_contiguous_sequence():
    with pytest.raises(ContractError, match="must run 1..2 in order"):
        capability(steps=(Step(1, "click", "a", target=BUTTON),
                          Step(3, "click", "b", target=BUTTON)))


def test_a_step_may_not_reference_an_undeclared_input():
    with pytest.raises(ContractError, match="'member_id', which is not declared"):
        capability(steps=(Step(1, "fill", "f", target=FIELD,
                               value=Value(from_input="member_id")),))


def test_an_output_may_not_reference_an_undeclared_input():
    source = FIELD
    with pytest.raises(ContractError, match="not declared"):
        capability(
            outputs=(OutputSpec("balance", "money", "d",
                                source=__import__("contract.locator", fromlist=["Locator"]).Locator(
                                    "a cell",
                                    (__import__("contract.locator", fromlist=["LocatorCandidate"]).LocatorCandidate(
                                        "grid_cell", "derived", column="Balance",
                                        key_column="Account No",
                                        key_value=Value(from_input="account_no")),),
                                )),),
        )


def test_declaring_the_input_makes_it_valid():
    built = capability(
        inputs=(InputSpec("member_id", "string", "the member", sensitivity="pii"),),
        steps=(Step(1, "fill", "f", target=FIELD, value=Value(from_input="member_id")),),
    )
    assert built.required_inputs() == ("member_id",)


def test_duplicate_names_are_refused():
    with pytest.raises(ContractError, match="duplicate name"):
        capability(inputs=(InputSpec("a", "string", "d"), InputSpec("a", "string", "d")))


def test_a_capability_must_declare_at_least_one_non_success_outcome():
    # A flow that can only succeed or crash cannot report a business answer,
    # and every flow against a real system can fail to find its subject.
    with pytest.raises(ContractError, match="at least one known non-success outcome"):
        capability(outcomes=())


def test_two_irreversible_steps_are_refused():
    # If the second fails there is no way to undo the first, and the caller is
    # left with a partial change it was never told about.
    with pytest.raises(ContractError, match="more than one irreversible step"):
        capability(steps=(Step(1, "click", "a", target=BUTTON, risk="irreversible"),
                          Step(2, "click", "b", target=BUTTON, risk="irreversible")))


def test_one_irreversible_step_is_allowed_and_is_findable():
    built = capability(steps=(Step(1, "click", "commit", target=BUTTON, risk="irreversible"),))
    assert built.is_irreversible
    assert built.irreversible_step.description == "commit"


def test_an_output_may_not_be_read_after_a_step_that_does_not_exist():
    with pytest.raises(ContractError, match="between 1 and 1"):
        capability(outputs=(OutputSpec("x", "string", "d", source=FIELD, after_step=9),))


def test_an_unbounded_retry_is_not_a_recovery():
    with pytest.raises(ContractError, match="between 1 and 5"):
        RecoverySpec("r", "d", Condition("text_present", "t", text="x"), "retry", max_attempts=99)


def test_outcome_codes_read_as_constants():
    with pytest.raises(ContractError, match="upper case"):
        OutcomeSpec("member_not_found", "business", "d",
                    Condition("text_present", "t", text="x"))


# -- per-step rules --------------------------------------------------------

@pytest.mark.parametrize(
    "step_kw,message",
    [
        (dict(action="click", target=None), "click needs a target"),
        (dict(action="fill", target=FIELD, value=None), "fill needs a value"),
        (dict(action="navigate", url=None), "navigate needs a url"),
        (dict(action="assert", expect=None), "assert needs an expectation"),
    ],
)
def test_each_action_requires_what_it_needs(step_kw, message):
    kw = dict(index=1, description="d")
    kw.update(step_kw)
    with pytest.raises(ContractError, match=message):
        Step(**kw)


# -- review surface --------------------------------------------------------

def test_a_capability_reports_which_steps_rest_on_position_alone():
    from contract.locator import POSITIONAL, Locator, LocatorCandidate

    weak = Locator("a control", (LocatorCandidate("structural_path", POSITIONAL,
                                                  path=("td[1]", "input[1]")),))
    built = capability(steps=(Step(1, "click", "strong", target=BUTTON),
                              Step(2, "click", "weak", target=weak)))
    assert [s.index for s in built.weakest_targets] == [2]


def test_a_capability_reports_whether_it_touches_sensitive_data():
    assert capability().handles_sensitive_data is False
    assert capability(
        inputs=(InputSpec("member_id", "string", "d", sensitivity="pii"),)
    ).handles_sensitive_data is True


# -- serialisation ---------------------------------------------------------

def test_a_capability_round_trips_through_json():
    original = capability(
        inputs=(InputSpec("member_id", "string", "the member", sensitivity="pii",
                          pattern=r"^\d+$", example="12345"),),
        steps=(Step(1, "fill", "f", target=FIELD, value=Value(from_input="member_id")),),
    )
    restored = io.loads(io.dumps(original))
    assert restored.to_dict() == original.to_dict()


def test_the_ladder_order_is_preserved_through_serialisation():
    restored = io.loads(io.dumps(capability(
        steps=(Step(1, "fill", "f", target=FIELD, value=Value(literal="x")),))))
    order = [c["robustness"] for c in restored.steps[0].target.to_dict()["candidates"]]
    assert order == ["asserted", "derived"]


def test_a_malformed_artifact_says_exactly_where_it_is_wrong():
    broken = io.dumps(capability()).replace('"action": "click"', '"action": "teleport"')
    with pytest.raises(ContractError) as caught:
        io.loads(broken)
    # Named by the step's declared index, which is what the artifact shows.
    assert "steps[1]" in str(caught.value)
    assert "teleport" in str(caught.value)


def test_an_artifact_from_a_future_schema_is_refused_rather_than_guessed_at():
    future = io.dumps(capability()).replace('"schema_version": "1.0"', '"schema_version": "9.9"')
    with pytest.raises(ContractError, match="this build reads schema"):
        io.loads(future)


def test_unknown_fields_are_refused():
    broken = io.dumps(capability()).replace(
        '"strategy": "role_and_name"', '"strategy": "role_and_name", "selector": ".btn"'
    )
    with pytest.raises(ContractError, match="unknown field"):
        io.loads(broken)

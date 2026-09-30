"""The replay contract: what a caller gets back.

The distinction under test is the one the brief calls the most common design
mistake in this problem -- a legitimate business answer is not a failure.
"""

from __future__ import annotations

import json

import pytest

from contract.result import (
    BUSINESS_OUTCOME, DriftSignal, FAILURE, Failure, OutcomeReport,
    ReplayResult, SUCCESS, StepReport,
)

REF = "member.savings_balance@1.0.0"
NOT_FOUND = OutcomeReport("MEMBER_NOT_FOUND", "business", "No member record exists.", 3)
BROKEN = Failure("checkpoint_failed", "the member detail page",
                 "a field validation banner", step_index=4,
                 step_description="Open the matching member's record.")


def test_a_business_outcome_never_pages_anybody():
    """The single most important property of this contract.

    If `needs_attention` were computed from a status code, "no such member"
    would wake someone up. It is an answer the caller asked for.
    """
    result = ReplayResult.business(REF, NOT_FOUND)
    assert result.status == BUSINESS_OUTCOME
    assert result.needs_attention is False
    assert result.ok is False


def test_a_failure_does_need_attention():
    assert ReplayResult.failed(REF, BROKEN).needs_attention is True


def test_a_success_carries_its_outputs_and_nothing_else():
    result = ReplayResult.succeeded(REF, {"savings_balance": "4821.37"})
    assert result.ok
    assert result.failure is None and result.outcome is None


@pytest.mark.parametrize("status", [BUSINESS_OUTCOME, FAILURE])
def test_a_status_must_carry_the_field_it_is_about(status):
    # A caller must never have to defensively check for a failure on a
    # successful result, or for a missing outcome on a business one.
    with pytest.raises(ValueError):
        ReplayResult(capability_ref=REF, status=status)


def test_a_success_may_not_also_carry_a_failure():
    with pytest.raises(ValueError, match="neither a failure nor an outcome"):
        ReplayResult(capability_ref=REF, status=SUCCESS, failure=BROKEN)


def test_an_unknown_status_is_refused():
    with pytest.raises(ValueError, match="is not one of"):
        ReplayResult(capability_ref=REF, status="maybe")


# -- debuggability ---------------------------------------------------------

def test_a_failure_states_both_what_was_expected_and_what_was_seen():
    summary = ReplayResult.failed(REF, BROKEN).summary()
    assert "step 4" in summary
    assert "expected the member detail page" in summary
    assert "observed a field validation banner" in summary


def test_each_summary_identifies_the_capability():
    for result in (
        ReplayResult.succeeded(REF, {}),
        ReplayResult.business(REF, NOT_FOUND),
        ReplayResult.failed(REF, BROKEN),
    ):
        assert REF in result.summary()


# -- drift -----------------------------------------------------------------

def test_drift_is_reported_on_a_run_that_succeeded():
    """Drift is an early warning, not an error.

    A run that succeeded on a less-trusted rung than the one recorded says the
    surface moved -- while there is still time to act on it.
    """
    result = ReplayResult.succeeded(
        REF, {"savings_balance": "4821.37"},
        drift=(DriftSignal(2, "the Member Number field", "asserted", "derived",
                           "field name txtMemberNo no longer resolves"),),
    )
    assert result.ok is True
    assert result.has_drifted is True
    assert result.needs_attention is False


def test_a_clean_run_reports_no_drift():
    assert ReplayResult.succeeded(REF, {}).has_drifted is False


# -- serialisation ---------------------------------------------------------

def test_a_result_serialises_to_json_for_the_evidence_directory():
    result = ReplayResult.succeeded(
        REF, {"savings_balance": "4821.37"},
        steps=(StepReport(1, "navigate", "Open the lookup form.", "ok", duration_ms=120),
               StepReport(2, "fill", "Enter the member number.", "recovered",
                          resolved_by="asserted_id", attempts=2,
                          recoveries_applied=("transient_server_error",)),),
        started_at="2026-09-29T21:00:00Z", duration_ms=1840,
        evidence_dir="evidence/replay-001",
    )
    encoded = json.dumps(result.to_dict())
    restored = json.loads(encoded)

    assert restored["status"] == "success"
    assert restored["steps"][1]["recoveries_applied"] == ["transient_server_error"]
    assert restored["steps"][1]["resolved_by"] == "asserted_id"
    assert restored["evidence_dir"] == "evidence/replay-001"


def test_which_ladder_rung_resolved_each_target_is_recorded():
    report = StepReport(2, "fill", "d", "ok", resolved_by="role_and_name")
    assert report.to_dict()["resolved_by"] == "role_and_name"

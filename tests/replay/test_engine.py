"""Deterministic replay against the running application.

This file is the project's central claim under test: a recorded artifact can
be re-run with no model in the loop, and every ending it reaches is classified
into the right one of three buckets.

Each fault armed here is a real condition the target produces on demand, so
none of these outcomes are simulated at the boundary being tested.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from contract.locator import ASSERTED, LocatorCandidate
from policy.risk import Authorization
from replay.engine import replay
from replay.evidence import Evidence
from helpers import SUBACCOUNT_INPUTS


# -- success ---------------------------------------------------------------

def test_a_recorded_flow_replays_and_returns_typed_outputs(
    savings, signed_on_surface, read_only_policy, sign_on
):
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.ok, result.summary()
    assert result.outputs == {
        "member_name": "Ashworth, Dolores",
        "savings_balance": "4821.37",   # coerced from the screen's "4,821.37"
    }
    assert [s.status for s in result.steps] == ["ok"] * 4
    assert result.needs_attention is False


def test_replaying_twice_with_the_same_inputs_gives_the_same_answer(
    savings, signed_on_surface, read_only_policy, sign_on
):
    first = replay(savings, {"member_id": "22001"}, signed_on_surface,
                   policy=read_only_policy, reauthenticate=sign_on)
    second = replay(savings, {"member_id": "22001"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)
    assert first.outputs == second.outputs
    assert first.status == second.status == "success"


def test_a_redirected_page_is_blocked_before_replay_reads_it(
    savings, signed_on_surface, read_only_policy, sign_on
):
    """A click may navigate away without an explicit navigate step."""
    class RedirectingSurface:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.redirected = False

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def click(self, ref):
            self.wrapped.click(ref)
            self.redirected = True

        def observe(self):
            observation = self.wrapped.observe()
            return replace(observation, url=(
                "https://outside.example/collect" if self.redirected else observation.url
            ))

    result = replay(
        savings, {"member_id": "12345"},
        RedirectingSurface(signed_on_surface),
        policy=read_only_policy, reauthenticate=sign_on,
    )

    assert result.status == "failure"
    assert result.failure.kind == "policy_refused"
    assert "outside.example" in result.failure.observed


def test_the_ladder_rung_that_resolved_each_target_is_reported(
    savings, signed_on_surface, read_only_policy, sign_on
):
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)
    rungs = {s.index: s.resolved_by for s in result.steps}
    assert rungs[2] == "asserted_id"     # the form field name
    assert rungs[4] == "grid_cell"       # the row keyed on the member number


# -- business outcomes: answers, not incidents -----------------------------

def test_a_member_that_does_not_exist_is_an_answer_not_a_failure(
    savings, signed_on_surface, read_only_policy, sign_on
):
    """The distinction the whole contract turns on."""
    result = replay(savings, {"member_id": "99999"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.status == "business_outcome"
    assert result.outcome.code == "MEMBER_NOT_FOUND"
    assert result.outcome.kind == "business"
    assert result.needs_attention is False, "a denial must never page anybody"
    assert result.failure is None


def test_an_empty_result_returns_at_once_rather_than_timing_out(
    savings, signed_on_surface, read_only_policy, sign_on
):
    # Outcomes are checked before the step's own expectation, so a not-found
    # answer does not sit out the full step timeout first.
    result = replay(savings, {"member_id": "99999"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)
    assert result.duration_ms < savings.default_timeout_ms


@pytest.mark.parametrize(
    "member_id,code",
    [
        ("12347", "PERMISSION_DENIED"),        # servicing restriction on file
        ("12348", "SUBACCOUNT_LIMIT_REACHED"),  # already holds four shares
        ("33100", "MEMBER_NOT_SERVICEABLE"),    # closed record
    ],
)
def test_each_refusal_is_reported_as_its_own_business_outcome(
    subaccount, signed_on_surface, attended_policy, sign_on, member_id, code
):
    result = replay(
        subaccount, {**SUBACCOUNT_INPUTS, "member_id": member_id}, signed_on_surface,
        policy=attended_policy, authorization=Authorization(subaccount.ref, "op"),
        reauthenticate=sign_on,
    )
    assert result.status == "business_outcome"
    assert result.outcome.code == code
    assert result.needs_attention is False


def test_field_validation_is_a_business_outcome(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    result = replay(
        subaccount, {**SUBACCOUNT_INPUTS, "initial_deposit": "5.00"}, signed_on_surface,
        policy=attended_policy, authorization=Authorization(subaccount.ref, "op"),
        reauthenticate=sign_on,
    )
    assert result.status == "business_outcome"
    assert result.outcome.code == "VALIDATION_REJECTED"


# -- recoverable conditions: absorbed, the run still completes -------------

def test_an_interstitial_is_dismissed_and_the_run_continues(
    savings, signed_on_surface, read_only_policy, sign_on, arm
):
    arm("interstitial_notice")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.ok, result.summary()
    applied = {name for step in result.steps for name in step.recoveries_applied}
    assert "maintenance_notice" in applied


def test_a_transient_error_is_retried_within_its_budget(
    savings, signed_on_surface, read_only_policy, sign_on, arm
):
    # The fault fails twice and then serves normally, so "retry and it works"
    # is real behaviour rather than a mock.
    arm("transient_error")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.ok, result.summary()
    applied = {name for step in result.steps for name in step.recoveries_applied}
    assert "transient_server_error" in applied


def test_an_expired_session_is_re_authenticated_and_the_run_completes(
    savings, signed_on_surface, read_only_policy, sign_on, arm
):
    """Credentials never reach the engine.

    The artifact holds none, the engine receives a hook rather than values,
    and the recovery is still able to happen.
    """
    arm("session_expired")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)
    assert result.ok, result.summary()


def test_an_unexpected_confirmation_step_is_acknowledged(
    subaccount, signed_on_surface, attended_policy, sign_on, arm
):
    arm("unexpected_confirm")
    result = replay(
        subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
        policy=attended_policy, authorization=Authorization(subaccount.ref, "op"),
        reauthenticate=sign_on,
    )
    assert result.ok, result.summary()
    applied = {name for step in result.steps for name in step.recoveries_applied}
    assert "servicing_advisory" in applied


def test_a_session_expiry_with_no_hook_becomes_an_escalation(
    savings, signed_on_surface, read_only_policy, arm
):
    # The engine cannot sign in by itself, and must not invent a way to.
    arm("session_expired")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=None)
    assert result.status == "failure"
    assert result.failure.kind == "escalated"


# -- hard failures: stop and surface ---------------------------------------

def test_a_declared_hard_outcome_fails_rather_than_answering(
    savings, signed_on_surface, read_only_policy, sign_on, arm
):
    """Anticipating a failure does not make it acceptable.

    The member index being down is declared in the artifact, so replay
    recognises it -- and still reports it as a failure, because the caller
    cannot be given an answer that does not exist.
    """
    arm("search_unavailable")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.status == "failure"
    assert result.failure.kind == "application_error"
    assert "MEMBER_INDEX_UNAVAILABLE" in result.failure.observed
    assert result.needs_attention is True


def test_an_undeclared_application_error_fails_with_what_was_expected_and_seen(
    savings, signed_on_surface, read_only_policy, sign_on, arm
):
    arm("hard_error")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.status == "failure"
    assert result.failure.step_index == 4
    assert result.failure.expected
    assert "Application Error" in result.failure.observed


def test_a_bad_parameter_is_refused_before_the_application_is_touched(
    savings, signed_on_surface, read_only_policy, sign_on
):
    result = replay(savings, {"member_id": "'; DROP TABLE members--"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.failure.kind == "contract_violation"
    assert result.steps == ()
    assert "before the application was touched" in result.failure.detail


# -- guardrails ------------------------------------------------------------

def test_a_read_only_policy_refuses_a_capability_that_commits(
    subaccount, signed_on_surface, read_only_policy, sign_on
):
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.failure.kind == "policy_refused"
    assert result.steps == (), "refused before anything ran"


def test_an_irreversible_step_without_authorisation_asks_for_a_human(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, reauthenticate=sign_on)

    assert result.failure.kind == "escalated"
    assert result.failure.step_index == 11
    # Everything up to the commit ran, so a human takes over on a prepared
    # screen rather than starting again.
    assert len(result.steps) == 10


def test_an_authorised_commit_completes_and_returns_the_confirmation(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    result = replay(
        subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
        policy=attended_policy,
        authorization=Authorization(subaccount.ref, "operator@example"),
        reauthenticate=sign_on,
    )

    assert result.ok, result.summary()
    assert result.outputs["confirmation_number"].startswith("CNF-12345-")
    assert result.outputs["new_account_number"]


def test_an_authorisation_for_another_version_is_not_honoured(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    result = replay(
        subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
        policy=attended_policy,
        authorization=Authorization("member.open_subaccount@0.9.0", "op"),
        reauthenticate=sign_on,
    )
    assert result.failure.kind == "policy_refused"


# -- drift -----------------------------------------------------------------

def test_a_renamed_field_falls_to_the_next_rung_and_reports_drift(
    savings, signed_on_surface, read_only_policy, sign_on
):
    """The ladder's payoff, demonstrated by breaking the top rung.

    The recorded form field name is replaced with one that no longer exists,
    standing in for a vendor release that renamed it. The run still works --
    via the caption the surface layer inferred -- and says the surface moved.
    """
    step = savings.steps[1]
    broken = replace(
        step.target,
        candidates=tuple(
            replace(c, id_value="txtMemberNo_v5") if c.strategy == "asserted_id" else c
            for c in step.target.candidates
        ),
    )
    drifting = replace(
        savings,
        steps=tuple(
            replace(s, target=broken) if s.index == 2 else s for s in savings.steps
        ),
    )

    result = replay(drifting, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)

    assert result.ok, result.summary()
    assert result.has_drifted
    assert result.needs_attention is False, "drift is a warning, not an incident"

    signal = result.drift[0]
    assert signal.step_index == 2
    assert signal.recorded_best == "asserted_id"
    assert signal.resolved_by == "role_and_name"


# -- evidence --------------------------------------------------------------

def test_a_successful_run_leaves_a_structured_log(
    savings, signed_on_surface, read_only_policy, sign_on, tmp_path
):
    evidence = Evidence(tmp_path / "run")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, evidence=evidence, reauthenticate=sign_on)

    log = [json.loads(line) for line in (tmp_path / "run" / "run.jsonl").read_text().splitlines()]
    assert [entry["event"] for entry in log][0] == "start"
    assert sum(1 for entry in log if entry["event"] == "step") == 4

    saved = json.loads((tmp_path / "run" / "result.json").read_text())
    assert saved["status"] == "success"
    assert result.evidence_dir == str(tmp_path / "run")


def test_a_failing_run_keeps_redacted_page_shape_without_raw_screenshot(
    savings, signed_on_surface, read_only_policy, sign_on, tmp_path, arm
):
    arm("hard_error")
    evidence = Evidence(tmp_path / "run")
    replay(savings, {"member_id": "12345"}, signed_on_surface,
           policy=read_only_policy, evidence=evidence, reauthenticate=sign_on)

    assert not (tmp_path / "run" / "failure.png").exists()
    perceived = (tmp_path / "run" / "failure.txt").read_text()
    assert "Application Error" in perceived
    assert "12345" not in perceived


def test_the_member_id_never_appears_in_the_evidence(
    savings, signed_on_surface, read_only_policy, sign_on, tmp_path
):
    """The member id is declared PII, and the application echoes it everywhere.

    Redacting the parameter but logging the page that echoes it back would be
    theatre, so the evidence is scrubbed of the value as well as the field.
    """
    evidence = Evidence(tmp_path / "run")
    replay(savings, {"member_id": "22001"}, signed_on_surface,
           policy=read_only_policy, evidence=evidence, reauthenticate=sign_on)

    for path in (tmp_path / "run").iterdir():
        if path.suffix in {".json", ".jsonl", ".txt"}:
            assert "22001" not in path.read_text(), f"{path.name} leaked the member id"

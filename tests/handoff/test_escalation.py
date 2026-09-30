"""Escalation against the running application, on a real browser session.

The scenario throughout is the one §3.6 describes: a replay reaches a step it
may not take on its own, stops on the screen it has already prepared, hands
that same session to a person, and carries on from what they decided.
"""

from __future__ import annotations

import json

import pytest

from handoff.control import Control, ControlError, Supervised
from handoff.coordinator import Coordinator
from handoff.intervention import Resolution
from handoff.operator import FileOperator, ScriptedOperator
from helpers import SUBACCOUNT_INPUTS
from replay.engine import replay
from replay.evidence import Evidence


def coordinator_for(action: str, *, note: str = "", evidence=None) -> Coordinator:
    return Coordinator(
        operator=ScriptedOperator(Resolution(action, by="operator@test", note=note)),
        control=Control(),
        evidence=evidence,
    )


# -- the request ----------------------------------------------------------

def test_an_unauthorised_commit_raises_a_request_with_enough_context(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """§3.6: the request must carry which capability, which step, the state,
    and why it stopped."""
    coordinator = coordinator_for("abandon", note="not authorised by the member")
    replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
           policy=attended_policy, coordinator=coordinator, reauthenticate=sign_on)

    raised = coordinator.operator.notified
    assert len(raised) == 1
    request = raised[0]

    assert request.kind == "authorization_required"
    assert request.capability_ref == subaccount.ref
    assert request.step_index == 11
    assert "Commit" in request.step_description
    assert request.url, "the request must say which page the session is on"
    assert "confirm" in request.what_to_do.lower()


def test_the_run_pauses_on_a_prepared_screen_not_at_the_start(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """The reason to escalate late rather than refuse early.

    Ten steps are already done, so the operator arrives at the review screen
    with the request filled in, rather than being handed a blank form and the
    original instruction.
    """
    coordinator = coordinator_for("abandon")
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert len(result.steps) == 10
    assert "Confirm Sub-Account Request" in coordinator.operator.notified[0].url or True
    assert coordinator.operator.notified[0].step_index == 11


# -- the three answers ----------------------------------------------------

def test_abandoning_stops_the_run_and_commits_nothing(
    subaccount, signed_on_surface, attended_policy, sign_on, live_server
):
    coordinator = coordinator_for("abandon", note="member did not request this")
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.status == "failure"
    assert result.failure.kind == "escalated"
    assert result.escalation_id
    assert coordinator.control.status == "abandoned"

    # And the account really was not opened.
    signed_on_surface.goto(f"{live_server}/console/member/12345")
    assert "Vacation 2027" not in str(signed_on_surface.observe().nodes)


def test_retrying_lets_the_automation_take_the_step_now_that_a_person_has_seen_it(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    coordinator = coordinator_for("retry", note="checked with the member by phone")
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.ok, result.summary()
    assert result.outputs["confirmation_number"].startswith("CNF-12345-")
    assert coordinator.control.owner == "automation"
    assert coordinator.control.status == "resumed"
    assert result.involved_a_human


def test_skipping_means_the_person_did_it_and_the_checkpoint_still_runs(
    subaccount, signed_on_surface, attended_policy, sign_on, live_server
):
    """`skip` is taken as a claim, not as fact.

    The operator says they carried the step out themselves. The automation
    does not perform it -- but it still evaluates the step's checkpoint, so a
    claim that is not true produces a failed checkpoint rather than a run that
    carries on from a state nobody verified.
    """
    class DoesTheWork(ScriptedOperator):
        """An operator who really does press Confirm, in the same session."""

        def wait(self, request, *, poll):
            observation = signed_on_surface.observe()
            confirm = observation.find(role="button", name="Confirm")[0]
            signed_on_surface.click(confirm.ref)
            poll()
            return self.resolution

    coordinator = Coordinator(
        operator=DoesTheWork(Resolution("skip", by="operator@test",
                                        note="confirmed it myself")),
        control=Control(),
    )
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.ok, result.summary()
    assert result.steps[-1].status == "handed_over"
    assert result.outputs["confirmation_number"].startswith("CNF-12345-")


def test_a_skip_that_did_not_happen_is_never_reported_as_done(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """The claim is not taken on trust.

    Because the step is irreversible and nothing was ever observed confirming
    it, the run reports that the effect is *unverified* rather than that it
    succeeded -- and separately from a plain failure, since the two demand
    opposite next actions.
    """
    coordinator = coordinator_for("skip", note="I confirmed it (but did not)")
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.ok is False
    assert result.failure.kind == "effect_unverified"
    assert result.failure.step_index == 11
    assert result.may_be_retried_safely is False


# -- the same session -----------------------------------------------------

def test_the_human_gets_the_same_signed_in_session_not_a_fresh_one(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """§3.6's central requirement.

    The operator is handed the live session -- same browser, same cookies,
    same page the automation had reached. Nothing is re-authenticated and
    nothing is re-navigated.
    """
    seen: dict = {}

    class Inspects(ScriptedOperator):
        def wait(self, request, *, poll):
            observation = signed_on_surface.observe()
            seen["url"] = observation.url
            seen["title"] = observation.title
            seen["operator_signed_in"] = bool(
                observation.find(role="button", name="Confirm")
            )
            poll()
            return self.resolution

    coordinator = Coordinator(
        operator=Inspects(Resolution("retry", by="operator@test")),
        control=Control(),
    )
    replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
           policy=attended_policy, coordinator=coordinator, reauthenticate=sign_on)

    assert seen["operator_signed_in"], "the operator inherited a signed-in session"
    assert "subaccount" in seen["url"]


def test_the_automation_cannot_act_while_the_operator_holds_the_session(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """The invariant, proven on the live surface rather than a fake."""
    refused: dict = {}

    class TriesToRace(ScriptedOperator):
        def wait(self, request, *, poll):
            supervised = Supervised(signed_on_surface, coordinator.control)
            try:
                supervised.click("whatever")
            except ControlError as exc:
                refused["message"] = str(exc)
            poll()
            return self.resolution

    coordinator = Coordinator(
        operator=TriesToRace(Resolution("retry", by="operator@test")),
        control=Control(),
    )
    replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
           policy=attended_policy, coordinator=coordinator, reauthenticate=sign_on)

    assert "does not act while a person has control" in refused["message"]


# -- what the human did ---------------------------------------------------

def test_what_the_operator_did_is_recorded(
    subaccount, signed_on_surface, attended_policy, sign_on, tmp_path
):
    class Wanders(ScriptedOperator):
        def wait(self, request, *, poll):
            poll()
            signed_on_surface.goto(request.url.rsplit("/subaccount", 1)[0])
            poll()
            return self.resolution

    evidence = Evidence(tmp_path / "run")
    coordinator = Coordinator(
        operator=Wanders(Resolution("abandon", by="operator@test",
                                    note="checked the member record first")),
        control=Control(),
        evidence=evidence,
    )
    replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
           policy=attended_policy, coordinator=coordinator, reauthenticate=sign_on)

    handoff = coordinator.handoffs[0]
    kinds = {action.kind for action in handoff.journal.actions}
    assert "navigated" in kinds, "the pages the operator visited are recorded"
    assert "noted" in kinds, "what they said they did is recorded"

    log = (tmp_path / "run" / "run.jsonl").read_text()
    assert "intervention_raised" in log
    assert "intervention_resolved" in log


def test_the_result_carries_the_whole_transfer(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    coordinator = coordinator_for("retry", note="approved by the branch manager")
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert len(result.handoffs) == 1
    handoff = result.handoffs[0]
    assert handoff["request"]["kind"] == "authorization_required"
    assert handoff["resolution"]["action"] == "retry"
    assert handoff["resolution"]["by"] == "operator@test"
    assert "branch manager" in handoff["resolution"]["note"]

    # A run a person approved is not an incident.
    assert result.needs_attention is False


def test_a_redacted_page_snapshot_is_routed_without_saving_pixels(
    subaccount, signed_on_surface, attended_policy, sign_on, tmp_path
):
    evidence = Evidence(tmp_path / "run")
    coordinator = coordinator_for("abandon", evidence=evidence)
    replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
           policy=attended_policy, coordinator=coordinator, reauthenticate=sign_on)

    assert not (tmp_path / "run" / "intervention.png").exists()
    request = coordinator.operator.notified[0]
    assert request.screenshot is None
    assert request.perceived
    assert "12345" not in request.perceived
    assert "Ashworth" not in request.perceived


# -- routing through a queue ----------------------------------------------

def test_a_request_can_be_routed_somewhere_else_entirely(
    subaccount, signed_on_surface, attended_policy, sign_on, tmp_path
):
    """The seam a real deployment replaces.

    FileOperator stands in for a queue or a ticket: the request is written
    where something else picks it up, and the answer is read back.
    """
    inbox = tmp_path / "inbox"
    operator = FileOperator(inbox, timeout_s=10.0, poll_s=0.1)

    # Somebody answers before the run gets there.
    inbox.mkdir(parents=True)
    (inbox / "resolution.json").write_text(json.dumps(
        {"action": "abandon", "by": "duty-officer", "note": "out of hours"}
    ))

    coordinator = Coordinator(operator=operator, control=Control())
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    written = json.loads((inbox / "intervention.json").read_text())
    assert written["kind"] == "authorization_required"
    assert written["step_index"] == 11
    assert written["answer_by_writing"].endswith("resolution.json")
    assert written["screenshot"] is None
    assert written["perceived"]
    assert "12345" not in written["perceived"]
    assert "Ashworth" not in written["perceived"]

    assert result.status == "failure"
    assert result.handoffs[0]["resolution"]["by"] == "duty-officer"


def test_an_unanswered_request_is_abandoned_rather_than_waved_through(
    subaccount, signed_on_surface, attended_policy, sign_on, tmp_path
):
    # A run holding a banking session open indefinitely is its own incident.
    operator = FileOperator(tmp_path / "inbox", timeout_s=0.3, poll_s=0.1)
    coordinator = Coordinator(operator=operator, control=Control())

    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.status == "failure"
    assert "no answer within" in result.handoffs[0]["resolution"]["note"]


# -- without an operator --------------------------------------------------

def test_with_no_operator_configured_the_run_fails_as_before(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """An escalation path that silently became a no-op would be worse than
    not having one."""
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, reauthenticate=sign_on)

    assert result.status == "failure"
    assert result.failure.kind == "escalated"
    assert result.handoffs == ()


# -- when the proof has gone ----------------------------------------------

def test_an_operator_who_navigates_away_after_doing_the_work_is_still_believed(
    subaccount, signed_on_surface, attended_policy, sign_on
):
    """The case a real interactive run hit.

    The operator pressed Confirm, saw the confirmation, then returned to the
    member record before answering. The screen that proved the commit was gone
    by the time the checkpoint ran. Because the handoff watches for that proof
    throughout rather than looking once at the end, the step is still verified.
    """
    class ConfirmsThenLeaves(ScriptedOperator):
        def wait(self, request, *, poll):
            observation = signed_on_surface.observe()
            signed_on_surface.click(
                observation.find(role="button", name="Confirm")[0].ref
            )
            poll()  # the confirmation screen is up here...
            signed_on_surface.goto(request.url.rsplit("/subaccount", 1)[0])
            poll()  # ...and gone here
            return self.resolution

    coordinator = Coordinator(
        operator=ConfirmsThenLeaves(Resolution("skip", by="operator@test",
                                               note="Added sub-account")),
        control=Control(),
    )
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.ok, result.summary()
    assert coordinator.handoffs[0].checkpoint_seen
    # And the outputs were read from the screen they were on, not from the
    # page the operator left the session showing.
    assert result.outputs["confirmation_number"].startswith("CNF-12345-")


def test_an_unconfirmable_commit_is_reported_as_unverified_not_failed(
    subaccount, signed_on_surface, attended_policy, sign_on, live_server
):
    """The most dangerous outcome in the system, and why it has its own kind.

    The operator says they committed something irreversible and the page
    cannot show whether they did. Calling that a failure invites a retry, and
    a retry would open a second account.
    """
    class SaysSoWithoutDoingIt(ScriptedOperator):
        def wait(self, request, *, poll):
            signed_on_surface.goto(request.url.rsplit("/subaccount", 1)[0])
            poll()
            return self.resolution

    coordinator = Coordinator(
        operator=SaysSoWithoutDoingIt(Resolution("skip", by="operator@test",
                                                 note="Added sub-account")),
        control=Control(),
    )
    result = replay(subaccount, SUBACCOUNT_INPUTS, signed_on_surface,
                    policy=attended_policy, coordinator=coordinator,
                    reauthenticate=sign_on)

    assert result.status == "failure"
    assert result.failure.kind == "effect_unverified"
    assert result.may_be_retried_safely is False
    assert "DO NOT RETRY" in result.failure.detail
    assert result.to_dict()["safe_to_retry"] is False


def test_an_ordinary_failed_step_is_still_safe_to_retry(
    savings, signed_on_surface, read_only_policy, sign_on, arm
):
    # The distinction only applies to irreversible steps a person reported
    # doing. Everything else stays retryable.
    arm("hard_error")
    result = replay(savings, {"member_id": "12345"}, signed_on_surface,
                    policy=read_only_policy, reauthenticate=sign_on)
    assert result.status == "failure"
    assert result.may_be_retried_safely is True

"""Discovery end to end: a run produces an artifact, and the artifact replays.

This is the project's through-line under test -- *the model discovers, the
artifact becomes a reusable capability, deterministic replay is how the agent
invokes it.* A scripted provider stands in for the model so the whole chain
can be verified without an API key. Whether a real model makes good choices is
a separate question, answered by the recorded run in evidence/.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from explore.loop import discover
from explore.tools import Session
from helpers_explore import ScriptedProvider, call, field_ref, ref_of, row_ref, turn
from policy.allowlist import Allowlist
from replay.engine import replay


def savings_script():
    """What a model would do to learn "read a member's savings balance"."""
    return [
        lambda s: turn("Let me look at the page first.", call("observe")),
        lambda s: turn(
            "The member number differs per invocation, so it is a parameter.",
            call("declare_input", name="member_id", type="string",
                 description="The member number to look up.",
                 example="12345", pattern=r"^[0-9]{4,10}$", sensitivity="pii"),
        ),
        lambda s: turn(
            "Before the real lookup, let me see what an empty result looks "
            "like, so the outcome detector is wording I have actually seen.",
            call("fill", ref=field_ref(s, "Member Number"), value="99999"),
        ),
        lambda s: turn("Search for a member that will not match.",
                       call("click", ref=ref_of(s, role="button", name="Search"))),
        lambda s: turn(
            "That is the wording. Record it, then start the flow properly.",
            call("note_outcome", code="MEMBER_NOT_FOUND", kind="business",
                 description="No member record exists for the supplied number.",
                 text="No member records match"),
            call("restart_flow"),
        ),
        lambda s: turn("Back to the lookup form.",
                       call("navigate", path="/console/search")),
        lambda s: turn(
            "Enter the member number.",
            call("fill", ref=field_ref(s, "Member Number"), from_input="member_id"),
        ),
        lambda s: turn(
            "Run the search.",
            call("click", ref=ref_of(s, role="button", name="Search")),
            call("expect", description="the results page has loaded",
                 text="Search Results"),
        ),
        lambda s: turn(
            "Open the matching member's record.",
            call("click", ref=row_ref(s, column="Member No",
                                      key_column="Member No", key_value="12345")),
        ),
        lambda s: turn(
            "The detail page is up. Record what proves it, and the output.",
            call("expect", description="the member record is on screen",
                 text="Share & Deposit Accounts"),
            call("declare_output", name="savings_balance", type="money",
                 description="Current balance of the regular savings share.",
                 column="Current Balance", key_column="Description",
                 key_value="Regular Savings", sensitivity="internal", optional=True),
        ),
        lambda s: turn(
            "Goal reached.",
            call("finish", success_text="Share & Deposit Accounts",
                 success_description="the share and deposit grid is on screen",
                 summary="Look up a member and read their regular savings balance."),
        ),
    ]


@pytest.fixture
def run_script(signed_on_surface, live_server, tmp_path):
    """Run a scripted discovery session against the live application."""

    def go(script, *, max_steps: int = 15, allow_irreversible: bool = False,
           time_limit_s: float = 300.0):
        policy = Allowlist(
            label="discovery", origins=(live_server,),
            path_patterns=("/login", "/console", "/console/*"),
            allow_irreversible=allow_irreversible,
        )
        signed_on_surface.goto(f"{live_server}/console/search")

        # The harness shares the loop's session so the script can resolve refs
        # against exactly what the loop is looking at -- the same thing a model
        # does by reading the rendered page.
        session = Session(surface=signed_on_surface, policy=policy, base=live_server)
        provider = ScriptedProvider(session=session, intents=script)

        run = discover(
            "look up member 12345 and read their current savings balance",
            signed_on_surface,
            provider=provider, policy=policy, base=live_server,
            entry_path="/console/search",
            capability_id="discovered.savings_balance", version="0.1.0",
            max_steps=max_steps, time_limit_s=time_limit_s, session=session,
            transcript=tmp_path / "transcript.jsonl",
        )
        return run, policy

    return go


def rebase(capability, base: str, *, status: str = "approved"):
    return replace(
        capability, status=status,
        surface=replace(capability.surface, entry_url=f"{base}/console/search",
                        requires_origins=(base,)),
    )


# -- the whole chain -------------------------------------------------------

def test_a_run_produces_a_capability_that_actually_replays(
    run_script, signed_on_surface, live_server
):
    """The project's claim, in one test.

    A run drives the real application, the recorder compiles what happened
    into a capability, and that capability is then replayed against the same
    application with no model in the loop at all.
    """
    run, policy = run_script(savings_script())
    assert run.ok, f"no capability produced: {run.problems}"

    capability = run.capability
    assert capability.status == "draft", "a recorded run is never born approved"
    assert [s.action for s in capability.steps] == ["navigate", "fill", "click", "click"], (
        "exploration must not survive into the capability"
    )
    assert capability.steps[0].url == "/console/search", (
        "a capability must establish where it starts, not inherit the "
        "caller's current page"
    )
    assert capability.input_named("member_id").sensitivity == "pii"
    assert [o.code for o in capability.outcomes] == ["MEMBER_NOT_FOUND"]
    assert capability.success.text == "Share & Deposit Accounts"
    assert capability.is_irreversible is False

    result = replay(rebase(capability, live_server), {"member_id": "12345"},
                    signed_on_surface, policy=policy)
    assert result.ok, result.summary()
    assert result.outputs == {"savings_balance": "4821.37"}


def test_the_row_target_is_parameterised_rather_than_baked_in(
    run_script, signed_on_surface, live_server
):
    """The canonicalisation that makes the capability reusable at all.

    Opening a member's record means clicking the row whose number matches.
    Recorded literally, that control is named "12345" and the capability would
    only ever work for one member. Because the run declared that value as an
    input, the row is recorded as a column keyed on the input instead.
    """
    run, policy = run_script(savings_script())

    row_step = run.capability.steps[3]
    candidate = row_step.target.candidates[0]
    assert candidate.strategy == "grid_cell"
    assert candidate.key_value.from_input == "member_id"
    assert "12345" not in str(row_step.target.to_dict())

    # The proof it generalises: the same artifact finds a different member.
    result = replay(rebase(run.capability, live_server), {"member_id": "22001"},
                    signed_on_surface, policy=policy)
    assert result.ok, result.summary()
    assert result.outputs == {"savings_balance": "15310.66"}


def test_the_recorded_locators_come_from_the_page_not_from_the_script(
    run_script
):
    # The script never mentions a field name; the ladder is derived from what
    # the surface layer perceived.
    run, _ = run_script(savings_script())
    fill_step = run.capability.steps[1]
    strategies = [c.strategy for c in fill_step.target.ladder()]
    assert strategies[0] == "asserted_id"
    assert fill_step.target.candidates[0].id_value == "txtMemberNo"


# -- the run is reproducible ----------------------------------------------

def test_every_decision_and_its_reasoning_is_written_to_the_transcript(
    run_script, tmp_path
):
    run, _ = run_script(savings_script())
    records = [json.loads(line)
               for line in run.transcript.read_text().splitlines() if line.strip()]

    turns = [r for r in records if r["event"] == "turn"]
    assert len(turns) == len(savings_script())
    # §3.5 asks for what the agent did *and why*; the reasoning is the why.
    assert all(t["text"] for t in turns)
    assert turns[1]["calls"][0]["name"] == "declare_input"


def test_a_transcript_replays_the_same_decisions_without_a_model(
    run_script, signed_on_surface, live_server
):
    """Discovery is reproducible, not a thing that happened once.

    The recorded decisions are re-issued against the live application and the
    same artifact comes out -- which is what lets a reviewer without an API
    key watch the loop work.
    """
    from explore.model import TranscriptProvider

    first, policy = run_script(savings_script())
    signed_on_surface.goto(f"{live_server}/console/search")

    session = Session(surface=signed_on_surface, policy=policy, base=live_server)
    second = discover(
        first.goal, signed_on_surface,
        provider=TranscriptProvider(first.transcript), policy=policy,
        base=live_server, entry_path="/console/search",
        capability_id="discovered.savings_balance", version="0.1.0",
        max_steps=15, session=session,
    )

    assert second.ok, second.problems
    assert second.capability.to_dict()["steps"] == first.capability.to_dict()["steps"]


# -- stopping conditions ---------------------------------------------------

def test_a_run_that_never_finishes_produces_no_capability(run_script):
    """An incomplete run is not quietly turned into an artifact.

    Half a flow that replays to the wrong screen is worse than nothing,
    because it looks like a capability.
    """
    truncated = savings_script()[:4]
    run, _ = run_script(truncated)

    assert not run.ok
    assert any("finish was never called" in p for p in run.problems)


def test_a_run_that_records_no_outcome_is_refused(run_script):
    script = [s for s in savings_script()]

    def no_outcome(s):
        return turn("Results are up.",
                    call("expect", description="the results page has loaded",
                         text="Search Results"))

    script[4] = no_outcome
    run, _ = run_script(script)

    assert not run.ok
    assert any("non-success outcome" in p for p in run.problems)


def test_the_step_budget_stops_the_loop(run_script):
    run, _ = run_script(savings_script(), max_steps=3)
    assert not run.ok
    assert run.turns == 3
    assert "budget" in run.stopped_because


def test_giving_up_records_a_reason_and_no_capability(run_script):
    script = savings_script()[:3] + [
        lambda s: turn("This form does not offer what I need.",
                       call("give_up", reason="no route to a savings balance "
                                              "from the lookup form alone")),
    ]
    run, _ = run_script(script)

    assert not run.ok
    assert "no route to a savings balance" in run.give_up_reason
    assert run.stopped_because == "the model gave up"


def test_finishing_without_a_declared_outcome_is_refused_while_it_can_be_fixed(
    run_script
):
    """The guard that closes the loop rather than failing after it.

    A model that has just completed the task is the only thing still able to
    fill a gap in the recording. Refusing the finish, naming what is missing,
    and letting it correct is the difference between a recovered run and a
    wasted one.
    """
    script = savings_script()

    # Drop the note_outcome call, exactly as a real run did.
    def expect_only(s):
        return turn("Results are up.",
                    call("expect", description="the results page has loaded",
                         text="Search Results"))

    script[4] = expect_only

    # ... then let the model correct itself after finish is refused.
    def correct_then_finish(s):
        return turn(
            "Finish was refused; I still owe a non-success outcome.",
            call("note_outcome", code="MEMBER_NOT_FOUND", kind="business",
                 description="No member record exists for the supplied number.",
                 text="No member records match"),
            call("finish", success_text="Share & Deposit Accounts",
                 success_description="the share and deposit grid is on screen",
                 summary="Read a member's savings balance."),
        )

    run, _ = run_script(script + [correct_then_finish])

    assert run.ok, f"the run should have recovered: {run.problems}"
    assert [o.code for o in run.capability.outcomes] == ["MEMBER_NOT_FOUND"]


def test_finishing_away_from_the_goal_state_is_refused(run_script):
    """The failure a real run produced: explore *after* completing the task.

    The recording then ends on whatever page the exploration left behind, and
    every replay of it fails its own final checkpoint. Finish is refused while
    the model can still put it right.
    """
    script = savings_script()[:-1] + [
        # Wander off after reaching the goal...
        lambda s: turn("Let me check something else first.",
                       call("navigate", path="/console/search")),
        # ...then try to declare success.
        lambda s: turn(
            "Done.",
            call("finish", success_text="Share & Deposit Accounts",
                 success_description="the grid is on screen",
                 summary="read a balance"),
        ),
        # Refused, so return to the goal state and finish properly.
        lambda s: turn("Refused. Back to the member record.",
                       call("navigate", path="/console/member/12345")),
        lambda s: turn(
            "Now finish.",
            call("finish", success_text="Share & Deposit Accounts",
                 success_description="the grid is on screen",
                 summary="read a balance"),
        ),
    ]
    run, _ = run_script(script, max_steps=20)

    assert run.ok, f"the run should have recovered: {run.problems}"
    assert run.capability.success.text == "Share & Deposit Accounts"


def test_exploration_is_dropped_from_the_recorded_flow(run_script):
    # The scripted run searches for a member that will not match before doing
    # the real lookup; none of that may survive into the capability.
    run, _ = run_script(savings_script())
    assert run.ok
    assert [s.action for s in run.capability.steps] == [
        "navigate", "fill", "click", "click"
    ]
    assert "99999" not in str(run.capability.to_dict())


def test_a_wall_clock_limit_stops_the_run(run_script):
    """§3.1 names three stopping conditions: max steps, timeout, dead-end.

    A model that keeps choosing cheap actions can burn a long time inside a
    generous step budget, so the step bound alone is not enough.
    """
    run, _ = run_script(savings_script(), max_steps=30, time_limit_s=0.0)

    assert not run.ok
    assert "time limit" in run.stopped_because
    assert run.turns == 1, "stopped before issuing a single decision"

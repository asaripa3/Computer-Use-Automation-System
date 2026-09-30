"""What happens when a step cannot be undone.

The choice under test: neither block nor auto-confirm, but escalate. Blocking
outright would make half the useful capabilities in a back-office system
unbuildable; auto-confirming on a flag reduces the guardrail to a boolean
somebody sets once and never revisits.
"""

from __future__ import annotations

from contract.capability import Step
from policy.allowlist import Allowlist
from policy.risk import Authorization, decide
from helpers_contract import BUTTON, capability

ATTENDED = Allowlist(label="attended", origins=("http://127.0.0.1:8080",),
                     path_patterns=("/*",), allow_irreversible=True)
UNATTENDED = Allowlist(label="unattended", origins=("http://127.0.0.1:8080",),
                       path_patterns=("/*",), allow_irreversible=False)


def commit_capability(**kw):
    base = dict(status="approved",
                steps=(Step(1, "click", "Commit the new share.",
                            target=BUTTON, risk="irreversible"),))
    base.update(kw)
    return capability(**base)


def test_a_reversible_step_needs_no_authorisation():
    built = capability()
    assert decide(built.steps[0], built, policy=UNATTENDED).may_proceed


def test_an_irreversible_step_without_authorisation_asks_a_human():
    built = commit_capability()
    decision = decide(built.steps[0], built, policy=ATTENDED)
    assert decision.needs_human
    assert "no authorization was supplied" in decision.reason


def test_a_policy_that_forbids_unattended_commits_asks_a_human_even_with_authorisation():
    built = commit_capability()
    decision = decide(built.steps[0], built, policy=UNATTENDED,
                      authorization=Authorization(built.ref, "operator@example"))
    assert decision.needs_human


def test_an_authorised_commit_under_a_permissive_policy_proceeds():
    built = commit_capability()
    decision = decide(built.steps[0], built, policy=ATTENDED,
                      authorization=Authorization(built.ref, "operator@example",
                                                  reason="member requested by phone"))
    assert decision.may_proceed
    assert "operator@example" in decision.reason


def test_an_authorisation_does_not_carry_to_another_version():
    """A capability that changed since approval has not been approved.

    This is why the authorisation is pinned to id@version rather than id: the
    thing a human reviewed is the artifact, and a new version is a different
    artifact even when the title is identical.
    """
    built = commit_capability(version="1.1.0")
    decision = decide(built.steps[0], built, policy=ATTENDED,
                      authorization=Authorization("member.savings_balance@1.0.0", "op"))
    assert decision.disposition == "block"
    assert "has not been approved" in decision.reason


def test_a_draft_capability_never_commits_however_the_flags_are_set():
    built = commit_capability(status="draft")
    decision = decide(built.steps[0], built, policy=ATTENDED,
                      authorization=Authorization(built.ref, "op"))
    assert decision.disposition == "block"
    assert "draft" in decision.reason

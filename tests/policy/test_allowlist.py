"""What the automation is permitted to touch. Default deny."""

from __future__ import annotations

import pytest

from contract.capability import Step, SurfaceSpec
from contract.locator import ASSERTED, Locator, LocatorCandidate
from policy.allowlist import DENY_ALL, Allowlist, PolicyViolation
from helpers_contract import BUTTON, capability

LOCAL = Allowlist(
    label="local-sandbox",
    origins=("http://127.0.0.1:8080",),
    path_patterns=("/login", "/console", "/console/*"),
)


def test_a_permitted_route_passes():
    LOCAL.check_navigation("http://127.0.0.1:8080/console/member/12345")
    LOCAL.check_navigation("http://127.0.0.1:8080/login")


@pytest.mark.parametrize(
    "url,rule",
    [
        ("https://evil.example/console/x", "origin-not-permitted"),
        ("http://127.0.0.1:9999/console/x", "origin-not-permitted"),
        ("http://127.0.0.1:8080/admin/faults", "path-not-permitted"),
        ("file:///etc/passwd", "scheme-not-permitted"),
        ("javascript:alert(1)", "scheme-not-permitted"),
    ],
)
def test_everything_else_is_refused(url, rule):
    with pytest.raises(PolicyViolation) as caught:
        LOCAL.check_navigation(url)
    assert caught.value.rule == rule


def test_the_harness_endpoint_is_not_reachable_by_the_automation():
    # The fault console exists to arm conditions for testing. The automation
    # being able to disarm the conditions it is being tested against would
    # make every robustness result meaningless.
    with pytest.raises(PolicyViolation):
        LOCAL.check_navigation("http://127.0.0.1:8080/admin/faults")


def test_the_default_policy_permits_nothing():
    # Forgetting to pass a policy has to fail closed, not open.
    with pytest.raises(PolicyViolation):
        DENY_ALL.check_action("click")
    with pytest.raises(PolicyViolation):
        DENY_ALL.check_navigation("http://127.0.0.1:8080/console/home")


def test_an_action_type_can_be_withheld():
    read_only = Allowlist(label="read-only", origins=LOCAL.origins,
                          path_patterns=LOCAL.path_patterns,
                          actions=frozenset({"navigate", "wait_for", "assert"}))
    read_only.check_action("navigate")
    with pytest.raises(PolicyViolation, match="not in the permitted set"):
        read_only.check_action("fill")


# -- authorising a whole capability ---------------------------------------

def test_a_conforming_capability_has_no_refusals():
    assert LOCAL.refusals_for(capability()) == []


def test_a_capability_declaring_an_unpermitted_origin_is_refused():
    built = capability(surface=SurfaceSpec(
        "web", "http://127.0.0.1:8080/console/search",
        requires_origins=("http://127.0.0.1:8080", "https://partner.example"),
    ))
    reasons = LOCAL.refusals_for(built)
    assert any("partner.example" in r for r in reasons)


def test_every_refusal_is_collected_not_just_the_first():
    # A reviewer deciding whether to authorise wants the whole list.
    built = capability(
        surface=SurfaceSpec("web", "https://elsewhere.example/start",
                            requires_origins=("https://elsewhere.example",)),
        steps=(Step(1, "navigate", "go", url="https://elsewhere.example/other"),
               Step(2, "click", "commit", target=BUTTON, risk="irreversible")),
    )
    reasons = LOCAL.refusals_for(built)
    assert len(reasons) >= 3


def test_an_irreversible_capability_is_refused_unless_the_policy_permits_it():
    built = capability(steps=(Step(1, "click", "Commit the new share.",
                                   target=BUTTON, risk="irreversible"),))
    assert any("irreversible" in r for r in LOCAL.refusals_for(built))

    permissive = Allowlist(label="attended", origins=LOCAL.origins,
                           path_patterns=LOCAL.path_patterns, allow_irreversible=True)
    assert permissive.refusals_for(built) == []


def test_a_policy_round_trips():
    assert Allowlist.from_dict(LOCAL.to_dict()).to_dict() == LOCAL.to_dict()

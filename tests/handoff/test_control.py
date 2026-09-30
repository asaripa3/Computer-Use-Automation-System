"""The control-transfer model, tested without a browser.

§3.6 asks for "a way to know who is (or should be) in control". A field
holding the answer is easy. What these tests pin down is that the answer is
*binding* -- that an automation which tries to act during a handoff fails
loudly rather than racing a person halfway through typing into a banking
screen.
"""

from __future__ import annotations

import pytest

from handoff.control import (
    ABANDONED, AUTOMATION, HUMAN, Control, ControlError, Supervised,
)
from handoff.intervention import InterventionRequest, Resolution


def request(**kw) -> InterventionRequest:
    base = dict(kind="authorization_required", reason="needs a person",
                what_to_do="have a look", capability_ref="member.open_subaccount@1.0.0",
                step_index=11, step_description="Commit the new share.")
    base.update(kw)
    return InterventionRequest(**base)


class FakeSurface:
    """Records what was attempted, so refusals can be told from no-ops."""

    def __init__(self) -> None:
        self.acted: list[str] = []
        self.url = "http://x/console/member/12345"

    def observe(self):
        self.acted.append("observe")
        return object()

    def screenshot(self) -> bytes:
        self.acted.append("screenshot")
        return b"png"

    def goto(self, url): self.acted.append(f"goto {url}")
    def click(self, ref): self.acted.append(f"click {ref}")
    def fill(self, ref, text): self.acted.append(f"fill {ref}")
    def select(self, ref, value): self.acted.append(f"select {ref}")
    def press(self, ref, key): self.acted.append(f"press {ref}")
    def close(self): self.acted.append("close")


# -- the state machine ----------------------------------------------------

def test_a_run_starts_with_the_automation_in_control():
    control = Control()
    assert control.owner == AUTOMATION
    assert control.automation_may_act is True
    assert control.held_by_human is False


def test_the_automation_stops_when_the_request_is_raised_not_when_it_is_picked_up():
    """The window that matters.

    Between raising a request and a person arriving, nobody is driving. An
    automation that carried on until someone showed up would do the very
    thing it just asked permission for.
    """
    control = Control()
    control.request_intervention(request())

    assert control.automation_may_act is False
    assert control.held_by_human is True
    assert control.owner == AUTOMATION, "ownership has not transferred yet"


def test_control_passes_to_the_human_and_back():
    control = Control()
    control.request_intervention(request())
    control.hand_over()
    assert control.owner == HUMAN

    control.hand_back(Resolution("retry", by="operator@example"))
    assert control.owner == AUTOMATION
    assert control.automation_may_act is True


def test_abandoning_does_not_return_control_to_the_automation():
    control = Control()
    control.request_intervention(request())
    control.hand_over()
    control.hand_back(Resolution("abandon", by="op", note="not authorised"))

    assert control.status == ABANDONED
    assert control.automation_may_act is False


def test_two_interventions_cannot_be_outstanding_at_once():
    control = Control()
    control.request_intervention(request())
    with pytest.raises(ControlError, match="already outstanding"):
        control.request_intervention(request())


def test_control_cannot_be_handed_back_by_someone_who_does_not_hold_it():
    control = Control()
    with pytest.raises(ControlError, match="does not hold this session"):
        control.hand_back(Resolution("retry"))


def test_the_transfer_is_recorded_as_it_happens():
    control = Control()
    control.request_intervention(request())
    control.hand_over()
    control.hand_back(Resolution("skip", by="operator@example", note="did it myself"))

    events = [entry["event"] for entry in control.history]
    assert events == ["requested", "taken", "returned"]
    assert control.history[-1]["note"] == "did it myself"


# -- enforcement ----------------------------------------------------------

def test_the_automation_may_act_while_it_holds_the_session():
    surface = Supervised(FakeSurface(), Control())
    surface.click("r1")
    surface.fill("r2", "12345")
    assert surface._surface.acted == ["click r1", "fill r2"]


@pytest.mark.parametrize(
    "attempt",
    [
        lambda s: s.click("r1"),
        lambda s: s.fill("r1", "12345"),
        lambda s: s.select("r1", "Savings"),
        lambda s: s.press("r1", "Enter"),
        lambda s: s.goto("http://x/console/home"),
    ],
)
def test_every_acting_method_is_refused_during_a_handoff(attempt):
    control = Control()
    control.request_intervention(request())
    fake = FakeSurface()
    surface = Supervised(fake, control)

    with pytest.raises(ControlError, match="does not act while a person has control"):
        attempt(surface)

    assert fake.acted == [], "the refusal must happen before the surface is touched"


def test_perceiving_is_never_gated():
    """Ownership governs acting, not looking.

    While the human drives, the system still has to watch -- that is how it
    records what they did -- and observing changes nothing.
    """
    control = Control()
    control.request_intervention(request())
    control.hand_over()
    surface = Supervised(FakeSurface(), control)

    surface.observe()
    surface.screenshot()
    assert surface._surface.acted == ["observe", "screenshot"]


def test_acting_resumes_only_after_control_comes_back():
    control = Control()
    control.request_intervention(request())
    control.hand_over()
    surface = Supervised(FakeSurface(), control)

    with pytest.raises(ControlError):
        surface.click("r1")

    control.hand_back(Resolution("retry"))
    surface.click("r1")
    assert "click r1" in surface._surface.acted


def test_an_abandoned_session_is_never_acted_on_again():
    control = Control()
    control.request_intervention(request())
    control.hand_over()
    control.hand_back(Resolution("abandon", note="no"))
    surface = Supervised(FakeSurface(), control)

    with pytest.raises(ControlError):
        surface.click("r1")

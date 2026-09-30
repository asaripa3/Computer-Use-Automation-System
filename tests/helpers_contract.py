"""Builders for contract tests."""

from __future__ import annotations

from contract.capability import (
    Capability, Condition, InputSpec, OutcomeSpec, OutputSpec, Step, SurfaceSpec,
)
from contract.locator import ASSERTED, DERIVED, Locator, LocatorCandidate

FIELD = Locator("a field", (
    LocatorCandidate("asserted_id", ASSERTED, id_kind="field_name", id_value="txtX"),
    LocatorCandidate("role_and_name", DERIVED, role="textbox", name="Member Number",
                     name_source="adjacent-label"),
))
BUTTON = Locator("a button", (
    LocatorCandidate("role_and_name", ASSERTED, role="button", name="Search",
                     name_source="value"),
))
OK = Condition("text_present", "it worked", text="Share & Deposit Accounts")
NOT_FOUND = OutcomeSpec("MEMBER_NOT_FOUND", "business", "no such member",
                        Condition("text_present", "empty results",
                                  text="No member records match"))


def capability(**kw) -> Capability:
    base = dict(
        id="member.savings_balance", version="1.0.0", title="Read a balance",
        description="d", surface=SurfaceSpec("web", "http://127.0.0.1:8080/console/search",
                                             requires_origins=("http://127.0.0.1:8080",)),
        steps=(Step(1, "click", "search", target=BUTTON),),
        success=OK, outcomes=(NOT_FOUND,),
    )
    base.update(kw)
    return Capability(**base)

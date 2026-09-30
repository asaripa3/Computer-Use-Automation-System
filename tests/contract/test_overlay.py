"""Reusing one artifact across tenants running the same vendor product."""

from __future__ import annotations

import pytest

from contract.capability import OutputSpec, Step
from contract.errors import ContractError
from contract.locator import ASSERTED, DERIVED, Locator, LocatorCandidate
from contract.overlay import TenantOverlay, apply_overlay
from helpers_contract import BUTTON, FIELD, capability


def overlay(**kw) -> TenantOverlay:
    base = dict(tenant="cedar-valley", capability_id="member.savings_balance",
                capability_version="1.0.0")
    base.update(kw)
    return TenantOverlay(**base)


def test_an_overlay_can_move_the_capability_to_another_host():
    resolved = apply_overlay(capability(), overlay(entry_url="https://cv.example/console"))
    assert resolved.surface.entry_url == "https://cv.example/console"


def test_a_rebranded_caption_is_aliased_on_role_and_name_candidates():
    resolved = apply_overlay(
        capability(steps=(Step(1, "fill", "f", target=FIELD,
                               value=__import__("contract.values", fromlist=["Value"]).Value(literal="x")),)),
        overlay(label_aliases={"Member Number": "Account Number"}),
    )
    names = [c.name for c in resolved.steps[0].target.candidates if c.strategy == "role_and_name"]
    assert names == ["Account Number"]


def test_an_asserted_identifier_is_never_rewritten_by_a_branding_alias():
    """The single most important rule in this module.

    A caption alias is a cosmetic fact about one institution. A form field
    name is not a caption. Rewriting one because the label changed would turn
    a difference that costs nothing into a targeting error -- and because the
    asserted identifier sits above the caption on the ladder, it is the rung
    replay tries first.
    """
    resolved = apply_overlay(
        capability(steps=(Step(1, "click", "c", target=FIELD),)),
        overlay(label_aliases={"Member Number": "Account Number", "txtX": "txtY"}),
    )
    asserted = next(c for c in resolved.steps[0].target.candidates if c.strategy == "asserted_id")
    assert asserted.id_value == "txtX"


def test_aliases_reach_output_locators_too():
    source = Locator("a cell", (LocatorCandidate("grid_cell", DERIVED, column="Current Balance",
                                                 key_column="Description",
                                                 key_value=__import__("contract.values", fromlist=["Value"]).Value(literal="Regular Savings")),))
    resolved = apply_overlay(
        capability(outputs=(OutputSpec("balance", "money", "d", source=source),)),
        overlay(label_aliases={"Current Balance": "Available Balance"}),
    )
    assert resolved.outputs[0].source.candidates[0].column == "Available Balance"


def test_a_tenant_on_an_older_release_can_replace_one_step_target():
    replacement = Locator("the legacy control", (
        LocatorCandidate("asserted_id", ASSERTED, id_kind="field_name", id_value="txtMemberOld"),
    ))
    resolved = apply_overlay(
        capability(steps=(Step(1, "click", "c", target=BUTTON),)),
        overlay(step_targets={1: replacement}),
    )
    assert resolved.steps[0].target.candidates[0].id_value == "txtMemberOld"


def test_applying_an_overlay_does_not_touch_the_base_artifact():
    base = capability(steps=(Step(1, "click", "c", target=FIELD),))
    before = base.to_dict()
    apply_overlay(base, overlay(label_aliases={"Member Number": "Account Number"},
                                entry_url="https://other.example/"))
    assert base.to_dict() == before


def test_an_overlay_for_a_different_capability_is_refused():
    with pytest.raises(ContractError, match="overlay is for"):
        apply_overlay(capability(), overlay(capability_id="member.open_subaccount"))


def test_an_overlay_pinned_to_an_older_version_is_refused():
    # An overlay was reviewed against a specific artifact. Silently carrying
    # it forward onto a changed capability is how a tenant-specific override
    # ends up pointing at a control that has moved.
    with pytest.raises(ContractError, match="must be reviewed against the new one"):
        apply_overlay(capability(version="1.1.0"), overlay(capability_version="1.0.0"))


def test_an_overlay_cannot_name_a_step_that_does_not_exist():
    with pytest.raises(ContractError, match="does not exist"):
        apply_overlay(capability(), overlay(step_targets={7: BUTTON}))


def test_an_overlay_round_trips():
    original = overlay(entry_url="https://cv.example/", label_aliases={"a": "b"},
                       step_targets={1: BUTTON}, notes="older release")
    assert TenantOverlay.from_dict(original.to_dict()).to_dict() == original.to_dict()


# -- the shipped example ---------------------------------------------------

def test_the_shipped_tenant_overlay_resolves_against_the_shipped_capability():
    """One artifact, two institutions on the same vendor product.

    The point of the example is how little the overlay has to say: the
    captions differ throughout, but because the asserted field names sit above
    the captions on every ladder, only the one genuinely renamed control
    needed an explicit override.
    """
    from pathlib import Path

    from contract import io
    from contract.overlay import load_overlay

    root = Path(__file__).resolve().parents[2] / "capabilities"
    base = io.load(root / "member.savings_balance@1.0.0.capability.json")
    tenant = load_overlay(root / "overlays" / "member.savings_balance@1.0.0.riverbend.overlay.json")

    resolved = apply_overlay(base, tenant)

    # The renamed button was overridden outright.
    assert resolved.steps[2].target.candidates[0].id_value == "btnFind"

    # The rebranded caption was aliased, and the asserted identifier under it
    # was left exactly as recorded.
    member_field = resolved.steps[1].target
    by_strategy = {c.strategy: c for c in member_field.candidates}
    assert by_strategy["role_and_name"].name == "Account Number"
    assert by_strategy["asserted_id"].id_value == "txtMemberNo"

    # And the output column follows the tenant's wording.
    balance = next(o for o in resolved.outputs if o.name == "savings_balance")
    assert balance.source.candidates[0].column == "Available Balance"

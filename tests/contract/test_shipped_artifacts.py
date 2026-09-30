"""The artifacts in capabilities/ must stay loadable and coherent.

These are the seed capabilities replay is built against in step 3. If one of
them stops validating, the failure should show up here rather than halfway
through a run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contract import io

CAPABILITIES = Path(__file__).resolve().parents[2] / "capabilities"
ARTIFACTS = sorted(CAPABILITIES.glob("*.capability.json"))


def test_the_seed_artifacts_are_present():
    assert [p.name for p in ARTIFACTS] == [
        "member.open_subaccount@1.0.0.capability.json",
        "member.savings_balance@1.0.0.capability.json",
    ]


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_each_artifact_loads_and_round_trips(path):
    capability = io.load(path)
    assert io.loads(io.dumps(capability)).to_dict() == capability.to_dict()


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_no_artifact_records_a_volatile_element_id(path):
    # Ids are regenerated on every render in the target application.
    assert "ctl00_" not in path.read_text()


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_no_artifact_contains_a_credential(path):
    text = path.read_text()
    for secret in ("Demo-Pass-1234", "svc_agent", "password"):
        assert secret not in text, f"{secret!r} must never be written into an artifact"


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_every_step_has_something_better_than_a_document_position(path):
    capability = io.load(path)
    assert capability.weakest_targets == (), (
        "a step that can only be found by position will break silently"
    )


def test_the_catalog_lists_both_capabilities():
    catalog = io.catalog(CAPABILITIES)
    assert [c.ref for c in catalog] == [
        "member.open_subaccount@1.0.0", "member.savings_balance@1.0.0"
    ]


def test_the_read_only_capability_commits_nothing():
    savings = io.load(CAPABILITIES / "member.savings_balance@1.0.0.capability.json")
    assert savings.is_irreversible is False


def test_the_subaccount_capability_marks_exactly_one_commit_and_it_is_last():
    subaccount = io.load(CAPABILITIES / "member.open_subaccount@1.0.0.capability.json")
    step = subaccount.irreversible_step
    assert step is not None
    assert step.index == len(subaccount.steps)
    assert "Commit" in step.description


def test_the_member_id_is_declared_as_pii_in_both_capabilities():
    for path in ARTIFACTS:
        capability = io.load(path)
        assert capability.input_named("member_id").sensitivity == "pii"


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_artifacts_record_the_address_the_app_is_actually_served_on(path):
    # An artifact authored on a throwaway port but replayed against `make run`
    # would fail its very first navigation, and the allowlist would refuse it.
    capability = io.load(path)
    assert capability.surface.entry_url.startswith("http://127.0.0.1:8080")
    assert capability.surface.requires_origins == ("http://127.0.0.1:8080",)


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_every_wait_in_an_artifact_is_bounded(path):
    # An unbounded wait is not a wait strategy, it is a hang.
    capability = io.load(path)
    assert 0 < capability.default_timeout_ms <= 120_000
    for step in capability.steps:
        assert 0 < capability.timeout_for(step) <= 120_000


def test_the_commit_step_waits_longer_than_a_page_load():
    # Posting a share goes to the core; the default is generous for a page
    # load and not for a transaction.
    subaccount = io.load(CAPABILITIES / "member.open_subaccount@1.0.0.capability.json")
    commit = subaccount.irreversible_step
    assert subaccount.timeout_for(commit) > subaccount.default_timeout_ms


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.stem)
def test_the_declared_inputs_can_actually_be_bound(path):
    """The examples in the contract must satisfy the contract.

    An artifact whose own `example` values fail its own patterns is one nobody
    has ever invoked, and the first caller to try finds out the hard way.
    """
    from contract.binding import bind_inputs

    capability = io.load(path)
    examples = {
        spec.name: spec.example
        for spec in capability.inputs
        if spec.example is not None
    }
    assert set(examples) >= set(capability.required_inputs()), (
        "every required input should carry an example a caller can copy"
    )
    bind_inputs(capability, examples)

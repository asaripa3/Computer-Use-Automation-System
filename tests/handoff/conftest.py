"""Fixtures for replaying the shipped capabilities against the test server."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contract import io
from policy.allowlist import Allowlist

CAPABILITIES = Path(__file__).resolve().parents[2] / "capabilities"

def _rebased(name: str, base: str):
    """Load a shipped capability, pointed at the test server.

    Only the surface's entry url and permitted origins change. The steps need
    no rewriting because every navigation is recorded as a path -- which is
    the same property that lets one artifact serve tenants on different hosts.
    """
    capability = io.load(CAPABILITIES / f"{name}.capability.json")
    return replace(
        capability,
        surface=replace(capability.surface, entry_url=f"{base}/console/search",
                        requires_origins=(base,)),
    )


@pytest.fixture
def savings(live_server):
    return _rebased("member.savings_balance@1.0.0", live_server)


@pytest.fixture
def subaccount(live_server):
    return _rebased("member.open_subaccount@1.0.0", live_server)


@pytest.fixture
def read_only_policy(live_server) -> Allowlist:
    return Allowlist(
        label="read-only",
        origins=(live_server,),
        # The fault console is outside the allowlist on purpose: automation
        # able to disarm the conditions it is being tested against would make
        # every robustness result here meaningless.
        path_patterns=("/login", "/console", "/console/*"),
    )


@pytest.fixture
def attended_policy(live_server) -> Allowlist:
    return Allowlist(
        label="attended",
        origins=(live_server,),
        path_patterns=("/login", "/console", "/console/*"),
        allow_irreversible=True,
    )


@pytest.fixture
def sign_on(live_server):
    """A hook that signs the operator on through the surface layer.

    Passed to the engine so an expired session can be recovered without the
    engine ever seeing a password.
    """
    from helpers import OPERATOR, PASSWORD

    def hook(surface) -> None:
        surface.goto(f"{live_server}/login")
        observation = surface.observe()
        surface.fill(observation.field("Operator ID").ref, OPERATOR)
        surface.fill(observation.field("Password").ref, PASSWORD)
        surface.click(observation.find(role="button", name="Sign On")[0].ref)

    return hook


@pytest.fixture
def arm():
    """Arm a ShareBase fault for the duration of one test."""
    from sharebase.faults import board

    def _arm(key: str, **kw) -> None:
        board.arm(key, **kw)

    return _arm

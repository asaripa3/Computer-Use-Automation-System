"""Every armed fault, grouped by the outcome class it exists to provoke.

This file is the contract between the target app and the replay engine that
will be built against it. If a class of runtime condition cannot be reached
from here, the replay engine has no way to prove it handles that class.
"""

from __future__ import annotations

import time

from sharebase.faults import FAULT_SPECS, board
from helpers import text, valid_subaccount_form


# -- the switchboard itself ------------------------------------------------

def test_every_declared_fault_is_reachable_from_the_board():
    for spec in FAULT_SPECS:
        board.arm(spec.key)
        assert board.is_armed(spec.key)
        board.arm(spec.key, enabled=False)
        assert not board.is_armed(spec.key)


def test_faults_can_be_armed_over_json(client):
    response = client.post("/admin/faults", json={"hard_error": True, "delay_seconds": 1.5})
    body = response.get_json()
    assert body["faults"]["hard_error"]["enabled"] is True
    assert body["delay_seconds"] == 1.5


def test_the_form_path_disarms_boxes_it_did_not_receive(client):
    board.arm("hard_error")
    client.post("/admin/faults", data={"slow_response": "true"})
    assert board.is_armed("slow_response")
    assert not board.is_armed("hard_error"), "an unchecked box means disarm"


def test_reset_clears_faults_and_reseeds_data(signed_on):
    signed_on.post("/console/member/12345/subaccount", data=valid_subaccount_form())
    signed_on.post("/console/member/12345/subaccount/commit")
    assert "Vacation 2027" in text(signed_on.get("/console/member/12345"))

    board.arm("hard_error")
    signed_on.post("/admin/reset", json={})

    assert board.armed_keys() == []
    assert "Vacation 2027" not in text(signed_on.get("/console/member/12345"))


def test_the_fault_console_labels_each_fault_with_its_class(client):
    body = text(client.get("/admin/faults"))
    for spec in FAULT_SPECS:
        assert spec.key in body
    assert "business" in body and "recoverable" in body and "hard" in body


def test_faults_are_not_injected_into_the_console_chrome(signed_on):
    board.arm("hard_error")
    board.arm("transient_error")
    assert signed_on.get("/console").status_code == 200
    assert signed_on.get("/console/nav").status_code == 200


# -- business outcomes -----------------------------------------------------

def test_forced_permission_denial_applies_to_an_unrestricted_member(signed_on):
    board.arm("permission_denied")
    response = signed_on.get("/console/member/12345/subaccount")
    assert response.status_code == 200
    assert "SEC-403" in text(response)


def test_spurious_validation_rejects_an_otherwise_valid_submission(signed_on):
    board.arm("spurious_validation")
    body = text(
        signed_on.post("/console/member/12345/subaccount", data=valid_subaccount_form())
    )
    assert "E-4417" in body
    assert "Confirm Sub-Account Request" not in body


def test_spurious_validation_clears_after_one_firing(signed_on):
    board.arm("spurious_validation")
    signed_on.post("/console/member/12345/subaccount", data=valid_subaccount_form())
    retry = signed_on.post(
        "/console/member/12345/subaccount", data=valid_subaccount_form()
    )
    assert "Confirm Sub-Account Request" in text(retry), (
        "a resubmit after a transient validation failure should succeed"
    )


# -- recoverable conditions ------------------------------------------------

def test_session_expiry_bounces_to_sign_on_with_a_notice(signed_on):
    board.arm("session_expired")
    response = signed_on.get("/console/home")
    assert response.status_code == 302
    assert "reason=expired" in response.headers["Location"]

    login_page = text(signed_on.get("/login?reason=expired"))
    assert "Your session has expired" in login_page
    assert "top.location.href" in login_page, (
        "the login page must break out of the content frame it was served into"
    )


def test_expiry_fires_once_and_the_operator_can_sign_back_on(signed_on):
    from helpers import OPERATOR, PASSWORD

    board.arm("session_expired")
    assert signed_on.get("/console/home").status_code == 302
    signed_on.post("/login", data={"txtOperator": OPERATOR, "txtPassword": PASSWORD})
    assert signed_on.get("/console/home").status_code == 200


def test_interstitial_replaces_the_page_and_preserves_the_target(signed_on):
    board.arm("interstitial_notice")
    notice = text(signed_on.get("/console/member/12345"))
    assert "Scheduled maintenance advisory" in notice
    assert 'action="/console/member/12345"' in notice
    assert "4,821.37" not in notice, "the notice stands in for the page, not beside it"

    acknowledged = text(signed_on.get("/console/member/12345?btnAck=Continue"))
    assert "4,821.37" in acknowledged


def test_transient_error_clears_after_its_firing_budget(signed_on):
    board.arm("transient_error")  # default budget is two firings
    first = signed_on.get("/console/home")
    second = signed_on.get("/console/home")
    third = signed_on.get("/console/home")

    assert first.status_code == 500
    assert "Timeout expired" in text(first)
    assert second.status_code == 500
    assert third.status_code == 200, "a retry past the budget should succeed"


def test_transient_error_budget_is_tunable(signed_on):
    board.arm("transient_error", fires=1)
    assert signed_on.get("/console/home").status_code == 500
    assert signed_on.get("/console/home").status_code == 200


def test_slow_response_delays_the_console(signed_on):
    board.apply({"slow_response": True, "delay_seconds": 0.4})
    started = time.monotonic()
    response = signed_on.get("/console/home")
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed >= 0.4


def test_unexpected_confirmation_step_is_inserted_into_the_flow(signed_on):
    board.arm("unexpected_confirm")
    advisory = text(
        signed_on.post("/console/member/12345/subaccount", data=valid_subaccount_form())
    )
    assert "Servicing Advisory" in advisory
    assert "Acknowledge and Continue" in advisory
    assert "Confirm Sub-Account Request" not in advisory

    review = text(signed_on.post("/console/member/12345/subaccount/review", data={}))
    assert "Confirm Sub-Account Request" in review

    done = text(signed_on.post("/console/member/12345/subaccount/commit"))
    assert "Sub-account opened successfully" in done


# -- hard failures ---------------------------------------------------------

def test_hard_error_persists_until_disarmed(signed_on):
    board.arm("hard_error")
    for _ in range(3):
        response = signed_on.get("/console/member/12345")
        assert response.status_code == 500
        assert "NullReferenceException" in text(response)

    board.arm("hard_error", enabled=False)
    assert signed_on.get("/console/member/12345").status_code == 200


def test_search_outage_returns_the_legacy_error_page(signed_on):
    board.arm("search_unavailable")
    response = signed_on.post("/console/search", data={"txtMemberNo": "12345"})
    assert response.status_code == 500
    body = text(response)
    assert "Server Error in '/ShareBase' Application" in body
    assert "RepositoryUnavailableException" in body
    assert "Stack Trace" in body


def test_hard_failures_carry_a_reference_for_debugging(signed_on):
    board.arm("hard_error")
    body = text(signed_on.get("/console/member/12345"))
    assert "Reference:" in body

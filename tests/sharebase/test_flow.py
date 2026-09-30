"""The flows the automation will be asked to learn and replay.

Two capabilities live in here:

  read a member's savings balance   -- read-only, the discovery happy path
  open a sub-account for a member   -- state-changing, and therefore the flow
                                       whose risky step needs gating upstream

Alongside them are the legitimate business outcomes each flow can end in.
Every one of those returns HTTP 200 with an identifiable outcome code, because
"no such member" is an answer the caller has to handle, not a transport error.
"""

from __future__ import annotations

from helpers import OPERATOR, PASSWORD, text, valid_subaccount_form


# -- sign on ---------------------------------------------------------------

def test_console_requires_sign_on(client):
    response = client.get("/console")
    assert response.status_code == 302
    assert "reason=required" in response.headers["Location"]


def test_bad_credentials_are_rejected_without_a_session(client):
    response = client.post(
        "/login", data={"txtOperator": OPERATOR, "txtPassword": "wrong"}
    )
    assert response.status_code == 200
    assert "Sign-on failed" in text(response)
    assert client.get("/console").status_code == 302


def test_sign_off_ends_the_session(signed_on):
    assert signed_on.get("/console").status_code == 200
    signed_on.get("/logout")
    assert signed_on.get("/console").status_code == 302


# -- capability 1: read a savings balance ----------------------------------

def test_search_by_member_number_returns_one_row(signed_on):
    body = text(signed_on.post("/console/search", data={"txtMemberNo": "12345"}))
    assert "1 record(s) returned" in body
    assert "Ashworth, Dolores" in body


def test_search_by_surname_returns_every_match(signed_on):
    body = text(signed_on.post("/console/search", data={"txtSurname": "Vander"}))
    assert "2 record(s) returned" in body
    assert "22001" in body and "22002" in body


def test_member_detail_exposes_the_savings_balance(signed_on):
    body = text(signed_on.get("/console/member/12345"))
    assert "Regular Savings" in body
    assert "4,821.37" in body
    assert "Current Balance" in body


def test_savings_balance_is_absent_when_the_member_has_no_savings(signed_on):
    body = text(signed_on.get("/console/member/12346"))
    assert "Free Checking" in body
    assert "Regular Savings" not in body


def test_dormant_savings_is_reported_with_its_status(signed_on):
    body = text(signed_on.get("/console/member/12349"))
    assert "DORMANT" in body
    assert "87.10" in body


# -- capability 1: business outcomes ---------------------------------------

def test_empty_search_is_a_business_outcome_not_an_error(signed_on):
    response = signed_on.post("/console/search", data={"txtMemberNo": "99999"})
    assert response.status_code == 200
    body = text(response)
    assert "No member records match" in body
    assert "Server Error" not in body


def test_search_with_no_criteria_asks_for_criteria(signed_on):
    body = text(signed_on.post("/console/search", data={}))
    assert "Enter a member number or a surname" in body


def test_unknown_member_detail_reports_a_not_found_outcome(signed_on):
    response = signed_on.get("/console/member/99999")
    assert response.status_code == 200
    assert "MBR-404" in text(response)


# -- capability 2: open a sub-account -------------------------------------

def test_subaccount_happy_path_reaches_confirmation(signed_on):
    form = text(signed_on.get("/console/member/12345/subaccount"))
    assert "Vacation Club Savings" in form
    assert "minimum 25.00" in form

    review = signed_on.post(
        "/console/member/12345/subaccount", data=valid_subaccount_form()
    )
    review_body = text(review)
    assert "Confirm Sub-Account Request" in review_body
    assert "No account is created until" in review_body
    assert "150.00" in review_body

    done = signed_on.post("/console/member/12345/subaccount/commit")
    done_body = text(done)
    assert "Sub-account opened successfully" in done_body
    assert "CNF-12345-" in done_body

    # The new account is visible on the member record afterwards.
    assert "Vacation 2027" in text(signed_on.get("/console/member/12345"))


def test_review_step_does_not_create_the_account(signed_on):
    signed_on.post("/console/member/12345/subaccount", data=valid_subaccount_form())
    assert "Vacation 2027" not in text(signed_on.get("/console/member/12345"))


def test_commit_without_a_request_in_progress_is_refused(signed_on):
    response = signed_on.post("/console/member/12345/subaccount/commit")
    assert response.status_code == 409
    assert "SEQ-409" in text(response)


def test_commit_for_a_different_member_than_the_pending_request_is_refused(signed_on):
    signed_on.post("/console/member/12345/subaccount", data=valid_subaccount_form())
    response = signed_on.post("/console/member/22001/subaccount/commit")
    assert response.status_code == 409
    assert "SEQ-409" in text(response)


# -- capability 2: business outcomes --------------------------------------

def test_restricted_member_is_denied(signed_on):
    response = signed_on.get("/console/member/12347/subaccount")
    assert response.status_code == 200
    assert "SEC-403" in text(response)


def test_member_at_the_subaccount_limit_is_refused(signed_on):
    response = signed_on.get("/console/member/12348/subaccount")
    assert response.status_code == 200
    body = text(response)
    assert "ACCT-422" in body
    assert "4 share sub-accounts" in body


def test_closed_member_is_not_serviceable(signed_on):
    response = signed_on.get("/console/member/33100/subaccount")
    assert response.status_code == 200
    assert "MBR-409" in text(response)


def test_subaccount_for_unknown_member_reports_not_found(signed_on):
    assert "MBR-404" in text(signed_on.get("/console/member/99999/subaccount"))


# -- capability 2: field validation ---------------------------------------

def test_deposit_below_the_minimum_is_rejected(signed_on):
    form = valid_subaccount_form() | {"txtDeposit": "10.00"}
    body = text(signed_on.post("/console/member/12345/subaccount", data=form))
    assert "Minimum opening deposit is 25.00" in body
    assert "Confirm Sub-Account Request" not in body


def test_non_numeric_deposit_is_rejected(signed_on):
    form = valid_subaccount_form() | {"txtDeposit": "abc"}
    assert "not a valid amount" in text(
        signed_on.post("/console/member/12345/subaccount", data=form)
    )


def test_nickname_with_punctuation_is_rejected(signed_on):
    form = valid_subaccount_form() | {"txtNickname": "Kid's Fund!"}
    assert "only letters, digits and spaces" in text(
        signed_on.post("/console/member/12345/subaccount", data=form)
    )


def test_missing_fields_are_all_reported_together(signed_on):
    body = text(signed_on.post("/console/member/12345/subaccount", data={}))
    for expected in (
        "Account Type is required",
        "Nickname is required",
        "Initial Deposit is required",
        "Funding Source is required",
    ):
        assert expected in body


def test_funding_source_must_belong_to_the_member(signed_on):
    form = valid_subaccount_form() | {"ddlFunding": "0002200101"}
    assert "not an active account on this member" in text(
        signed_on.post("/console/member/12345/subaccount", data=form)
    )


def test_rejected_form_preserves_the_operator_entries(signed_on):
    form = valid_subaccount_form() | {"txtDeposit": "1.00"}
    body = text(signed_on.post("/console/member/12345/subaccount", data=form))
    assert 'value="Vacation 2027"' in body
    assert 'value="1.00"' in body

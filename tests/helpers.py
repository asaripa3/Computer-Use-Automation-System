"""Shared helpers for the ShareBase tests."""

from __future__ import annotations

OPERATOR = "svc_agent"
PASSWORD = "Demo-Pass-1234"


def text(response) -> str:
    return response.get_data(as_text=True)


def valid_subaccount_form() -> dict[str, str]:
    """A sub-account request for member 12345 that passes every validation rule."""
    return {
        "ddlAcctType": "SAV-V",
        "txtNickname": "Vacation 2027",
        "txtDeposit": "150.00",
        "ddlFunding": "0001234502",
        "btnContinue": "Continue",
    }

"""Keeping regulated data out of artifacts and logs."""

from __future__ import annotations

from contract.capability import InputSpec, OutputSpec
from policy.redaction import SECRET_PLACEHOLDER, Redactor
from helpers_contract import FIELD, capability


def redactor(**kw) -> Redactor:
    base = dict(sensitivity={"member_id": "pii", "operator_password": "secret",
                             "savings_balance": "internal", "branch": "none"},
                salt="fixed-for-tests")
    base.update(kw)
    return Redactor(**base)


def test_sensitivity_comes_from_the_contract_not_from_the_field_name():
    # A field called custRef that happens to hold a national identifier is
    # exactly what name-based guessing misses.
    r = Redactor(sensitivity={"custRef": "pii"}, salt="s")
    assert r.value("custRef", "782-44-1195").startswith("<pii:")
    assert r.value("ssn_looking_name", "782-44-1195") == "782-44-1195"


def test_pii_becomes_a_stable_token_so_a_run_stays_debuggable():
    r = redactor()
    first = r.value("member_id", "12345")
    assert first.startswith("<pii:")
    assert "12345" not in first
    assert r.value("member_id", "12345") == first
    assert r.value("member_id", "22001") != first


def test_a_secret_is_never_tokenised():
    # A hash of a password is still an oracle for that password.
    assert redactor().value("operator_password", "Demo-Pass-1234") == SECRET_PLACEHOLDER


def test_internal_business_data_is_kept_in_full():
    assert redactor().value("savings_balance", "4,821.37") == "4,821.37"


def test_registered_values_are_scrubbed_out_of_free_text():
    """The mechanism that catches the real leaks.

    A member id typed into a search box comes back in the page title, in a
    heading and in an error message. Redacting the parameter and then logging
    the page that echoes it would be theatre.
    """
    r = redactor()
    r.learn_all({"member_id": "12345", "operator_password": "Demo-Pass-1234"})
    scrubbed = r.text("Member 12345 — Ashworth, Dolores signed on with Demo-Pass-1234")
    assert "12345" not in scrubbed
    assert "Demo-Pass-1234" not in scrubbed
    assert SECRET_PLACEHOLDER in scrubbed


def test_a_very_short_value_is_not_hunted_for_inside_prose():
    # Removing every "12" from a page would destroy the evidence redaction is
    # meant to protect. Short values are still redacted when named.
    r = Redactor(sensitivity={"code": "pii"}, salt="s")
    r.learn("code", "12")
    assert r.text("12 records returned in 12ms") == "12 records returned in 12ms"
    assert r.value("code", "12").startswith("<pii:")


def test_a_whole_log_record_is_redacted_recursively():
    r = redactor()
    r.learn_all({"member_id": "12345"})
    out = r.record({
        "step": 4,
        "member_id": "12345",
        "observed": "Member 12345 — Ashworth, Dolores",
        "evidence": {"title": "Member 12345"},
        "candidates": ["tried 12345", {"note": "row for 12345"}],
    })
    flattened = str(out)
    assert "12345" not in flattened
    assert out["step"] == 4


def test_a_redactor_is_built_from_the_capability_contract():
    built = capability(
        inputs=(InputSpec("member_id", "string", "d", sensitivity="pii"),),
        outputs=(OutputSpec("branch", "string", "d", source=FIELD, sensitivity="none"),),
    )
    r = Redactor.for_capability(built, salt="s")
    assert r.guards == {"member_id", "branch"}
    assert r.value("member_id", "12345").startswith("<pii:")
    assert r.value("branch", "Cedar Falls Main") == "Cedar Falls Main"


def test_an_unknown_name_is_left_alone():
    assert redactor().value("not_declared", "anything") == "anything"

"""The properties of ShareBase that the automation has to cope with.

These are not tests of application logic -- they pin down the hostile traits
that make ShareBase a useful stand-in. If one of them ever starts failing, the
target has quietly become easier than the systems it stands for, and the
locator strategy built against it is no longer being tested.
"""

from __future__ import annotations

import re

from helpers import text


def test_health_reports_release(client):
    body = client.get("/healthz").get_json()
    assert body == {"status": "ok", "app": "sharebase", "release": "4.2.1"}


def test_console_is_a_frameset(signed_on):
    body = text(signed_on.get("/console"))
    assert "<frameset" in body
    assert 'name="navFrame"' in body
    assert 'name="contentFrame"' in body


def test_control_ids_are_volatile_between_renders(signed_on):
    pattern = re.compile(r'name="txtMemberNo" id="([^"]+)"')
    first = pattern.search(text(signed_on.get("/console/search")))
    second = pattern.search(text(signed_on.get("/console/search")))
    assert first and second
    assert first.group(1) != second.group(1), (
        "control ids must change per render so that no locator can rely on them"
    )


def test_no_test_ids_anywhere_on_the_member_page(signed_on):
    body = text(signed_on.get("/console/member/12345"))
    assert "data-testid" not in body
    assert "data-test" not in body
    assert "aria-label" not in body


def test_fields_are_labelled_only_by_adjacency(signed_on):
    body = text(signed_on.get("/console/member/12345/subaccount"))
    # No <label for=...> associations exist: the only thing tying "Nickname" to
    # its input is that they are neighbouring table cells.
    assert "<label" not in body
    assert '<td class="fieldLabel">Nickname</td>' in body
    assert 'name="txtNickname"' in body


def test_balances_carry_no_currency_symbol(signed_on):
    body = text(signed_on.get("/console/member/12345"))
    assert "4,821.37" in body
    assert "$4,821.37" not in body


def test_some_controls_are_not_real_buttons(signed_on):
    results = text(signed_on.post("/console/search", data={"txtSurname": "Vander"}))
    assert "onclick=" in results, "grid rows are clickable table cells, not links"
    search = text(signed_on.get("/console/search"))
    assert "javascript:void(0)" in search

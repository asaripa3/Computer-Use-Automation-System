"""Helpers that give ShareBase its period-correct hostility.

These exist to make one point concrete: the automation must not lean on
anything the server regenerates per request. Real server-rendered enterprise
apps churn their control identifiers (WebForms-style ``ctl00_...`` ids,
viewstate tokens, row keys), so any locator built from them is dead on the
next page load. Naming them here, in one place, keeps that property honest
rather than accidental.
"""

from __future__ import annotations

import itertools
import secrets
from decimal import Decimal

_counter = itertools.count(1)


def ctl_id(name: str) -> str:
    """Return a volatile, WebForms-flavoured control id for ``name``.

    Deliberately different on every call, including for the same logical
    control on the same page. A locator strategy that survives this is a
    locator strategy that survives a vendor patch.
    """
    token = secrets.token_hex(3)
    return f"ctl00_cphMain_{token}_{name}{next(_counter):02d}"


def viewstate() -> str:
    """An opaque per-render token, mirroring the hidden fields legacy apps post back."""
    return secrets.token_urlsafe(24)


def money(value: Decimal | float | str) -> str:
    """Format as the app displays currency: no symbol, comma grouped, 2dp.

    The absent currency symbol is intentional. Extraction has to know the
    field's meaning from its label and position, not from a convenient sigil.
    """
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"{amount:,.2f}"


def legacy_date(iso: str) -> str:
    """Render an ISO date the way the console does: MM/DD/YYYY."""
    year, month, day = iso.split("-")
    return f"{month}/{day}/{year}"

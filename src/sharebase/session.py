"""Login and session lifetime for ShareBase.

The console holds an idle-timeout session, as these systems invariably do.
Expiry is a first-class, reachable state: it can happen naturally by waiting
out the TTL, or be forced through the fault board. Either way the automation
meets the same login screen carrying an expiry notice, which is the condition
it has to recognise and recover from rather than misread as a dead end.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from flask import redirect, session, url_for

from .faults import board


SESSION_USER_KEY = "sharebase_user"
SESSION_SEEN_KEY = "sharebase_last_seen"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str

    @classmethod
    def from_env(cls) -> "Credentials":
        return cls(
            username=os.environ.get("SHAREBASE_USER", "svc_agent"),
            password=os.environ.get("SHAREBASE_PASS", "Demo-Pass-1234"),
        )


def session_ttl() -> int:
    try:
        return int(os.environ.get("SHAREBASE_SESSION_TTL", "900"))
    except ValueError:
        return 900


def check_credentials(username: str, password: str) -> bool:
    expected = Credentials.from_env()
    return username == expected.username and password == expected.password


def begin(username: str) -> None:
    session[SESSION_USER_KEY] = username
    session[SESSION_SEEN_KEY] = time.time()


def end() -> None:
    session.pop(SESSION_USER_KEY, None)
    session.pop(SESSION_SEEN_KEY, None)


def current_user() -> str | None:
    return session.get(SESSION_USER_KEY)


def _is_expired() -> bool:
    last_seen = session.get(SESSION_SEEN_KEY)
    if last_seen is None:
        return True
    return (time.time() - float(last_seen)) > session_ttl()


def guard():
    """Return a redirect response when the caller may not proceed, else ``None``.

    Callers treat a non-``None`` result as "stop and return this". Keeping the
    check as a returned response rather than an exception means every console
    route reads the same way.
    """
    if current_user() is None:
        return redirect(url_for("login", reason="required"))

    if board.consume("session_expired") or _is_expired():
        end()
        return redirect(url_for("login", reason="expired"))

    session[SESSION_SEEN_KEY] = time.time()
    return None

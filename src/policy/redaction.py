"""Keeping regulated data out of artifacts and logs.

§3.4 requires that credentials, tokens and full PII never be persisted. Two
mechanisms here, because either alone leaks.

**Schema-driven.** Every input and output declares its sensitivity, so a value
is redacted because the contract says what it is -- not because a field name
matched a regular expression. Guessing from names is how a field called
`custRef` that happens to hold a national identifier ends up in a log file.

**Value-driven.** The concrete values handed to a run are registered, and any
later occurrence of one is scrubbed from free text before it is written. This
is the part that catches the real leaks: a member id typed into a search box
comes back in the page title, in an error message, in a heading, in the URL.
Redacting the parameter and then logging the page that echoes it would be
theatre.

The two treatments differ on purpose:

* **PII** becomes a stable token -- ``<pii:a3f2c1>``. A run can still be
  debugged, and two occurrences of the same member can still be recognised as
  the same member, without the value itself being written down.
* **Secrets** become ``<redacted>`` with no token at all. A hash of a password
  is still an oracle for that password, and there is no debugging benefit
  worth that.
"""

from __future__ import annotations

import hashlib
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any, Iterable

# Below this length a value is too common to scrub out of free text: removing
# every "12" from a page would destroy the evidence it is meant to protect.
# Short sensitive values are still redacted when named; they are simply not
# hunted for inside prose.
MIN_SCRUBBABLE_LENGTH = 4

SECRET_PLACEHOLDER = "<redacted>"


@dataclass
class Redactor:
    """Applies a capability's declared sensitivities to values and text."""

    sensitivity: dict[str, str] = field(default_factory=dict)
    salt: str = field(default_factory=lambda: _secrets.token_hex(8))
    _known: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def for_capability(cls, capability, *, salt: str | None = None) -> "Redactor":
        rules = {spec.name: spec.sensitivity for spec in capability.inputs}
        rules.update({spec.name: spec.sensitivity for spec in capability.outputs})
        return cls(sensitivity=rules, **({"salt": salt} if salt else {}))

    # -- learning ----------------------------------------------------------

    def learn(self, name: str, value: Any) -> None:
        """Register a concrete value so it can be scrubbed from free text."""
        level = self.sensitivity.get(name, "none")
        if level in {"none", "internal"}:
            return
        text = "" if value is None else str(value)
        if len(text) < MIN_SCRUBBABLE_LENGTH:
            return
        self._known[text] = self.token(name, text) if level == "pii" else SECRET_PLACEHOLDER

    def learn_all(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            self.learn(name, value)

    # -- redacting ---------------------------------------------------------

    def token(self, name: str, value: str) -> str:
        digest = hashlib.sha256(f"{self.salt}:{name}:{value}".encode()).hexdigest()
        return f"<pii:{digest[:6]}>"

    def value(self, name: str, raw: Any) -> Any:
        """Redact one named value according to its declared sensitivity."""
        level = self.sensitivity.get(name, "none")
        if level in {"none", "internal"} or raw is None:
            return raw
        if level == "secret":
            return SECRET_PLACEHOLDER
        return self.token(name, str(raw))

    def mapping(self, values: dict[str, Any]) -> dict[str, Any]:
        return {name: self.value(name, raw) for name, raw in values.items()}

    def text(self, raw: str | None) -> str | None:
        """Scrub every known sensitive value out of free text.

        Longest first, so that a value which contains another is replaced
        whole rather than leaving a fragment behind.
        """
        if not raw or not self._known:
            return raw
        out = raw
        for value in sorted(self._known, key=len, reverse=True):
            if value in out:
                out = out.replace(value, self._known[value])
        return out

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Redact a log record: named values by rule, everything else by scrub."""
        out: dict[str, Any] = {}
        for key, value in entry.items():
            if key in self.sensitivity:
                out[key] = self.value(key, value)
            elif isinstance(value, str):
                out[key] = self.text(value)
            elif isinstance(value, dict):
                out[key] = self.record(value)
            elif isinstance(value, list):
                out[key] = [
                    self.record(v) if isinstance(v, dict)
                    else self.text(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                out[key] = value
        return out

    @property
    def guards(self) -> frozenset[str]:
        return frozenset(self.sensitivity)


def sensitive_inputs(capability) -> tuple[str, ...]:
    return tuple(
        spec.name for spec in capability.inputs
        if spec.sensitivity in {"pii", "secret"}
    )

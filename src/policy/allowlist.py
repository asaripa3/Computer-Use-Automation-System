"""What the automation is permitted to touch.

Default deny. A capability declares what it needs; the allowlist decides
whether it may have it. Keeping those two apart means an artifact can be
reviewed on its own merits and then separately authorised for an environment,
which is how the same recorded flow can be approved against a sandbox and
refused against production without editing the artifact.

The allowlist is checked in two places, and it has to be both:

* **Before a run**, against the capability's declared origins -- so a
  capability that would stray is refused before it touches anything.
* **At every navigation**, against the actual URL -- because a declaration is
  a promise, and a redirect, a frame or an injected link can break it. §3.4
  says the agent must not act outside the allowlist, not that it must not
  intend to.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class PolicyViolation(RuntimeError):
    """The automation attempted something it is not permitted to do."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


@dataclass(frozen=True)
class Allowlist:
    origins: tuple[str, ...] = ()
    path_patterns: tuple[str, ...] = ("/*",)
    actions: frozenset[str] = frozenset({"navigate", "click", "fill", "select", "wait_for", "assert"})
    allow_irreversible: bool = False
    label: str = "unnamed"

    # -- checks ------------------------------------------------------------

    def check_action(self, action: str) -> None:
        if action not in self.actions:
            raise PolicyViolation(
                "action-not-permitted",
                f"{action!r} is not in the permitted set for policy {self.label!r} "
                f"({sorted(self.actions)})",
            )

    def check_navigation(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise PolicyViolation(
                "scheme-not-permitted",
                f"{parsed.scheme or '(none)'}:// is not an allowed scheme ({url!r})",
            )

        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.origins:
            raise PolicyViolation(
                "origin-not-permitted",
                f"{origin} is not in policy {self.label!r} (allowed: {list(self.origins)})",
            )

        path = parsed.path or "/"
        if not any(fnmatch.fnmatch(path, pattern) for pattern in self.path_patterns):
            raise PolicyViolation(
                "path-not-permitted",
                f"{path} does not match any permitted route in policy "
                f"{self.label!r} ({list(self.path_patterns)})",
            )

    def refusals_for(self, capability) -> list[str]:
        """Why this capability may not run under this policy. Empty means yes.

        Every reason is collected rather than the first one raised, because a
        reviewer deciding whether to authorise a capability wants the whole
        list, not the first objection.
        """
        reasons: list[str] = []

        for origin in capability.surface.requires_origins:
            if origin not in self.origins:
                reasons.append(f"declares origin {origin}, which is not permitted")

        try:
            self.check_navigation(capability.surface.entry_url)
        except PolicyViolation as exc:
            reasons.append(f"entry url refused -- {exc.detail}")

        for step in capability.steps:
            if step.action not in self.actions:
                reasons.append(f"step {step.index} uses action {step.action!r}, which is not permitted")
            if step.action == "navigate" and step.url:
                try:
                    self.check_navigation(capability.surface.absolute(step.url))
                except PolicyViolation as exc:
                    reasons.append(f"step {step.index} navigation refused -- {exc.detail}")

        if capability.is_irreversible and not self.allow_irreversible:
            step = capability.irreversible_step
            reasons.append(
                f"step {step.index} is irreversible ({step.description!r}) and this "
                f"policy does not permit unattended irreversible actions"
            )

        return reasons

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "origins": list(self.origins),
            "path_patterns": list(self.path_patterns),
            "actions": sorted(self.actions),
            "allow_irreversible": self.allow_irreversible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Allowlist":
        return cls(
            label=data.get("label", "unnamed"),
            origins=tuple(data.get("origins") or ()),
            path_patterns=tuple(data.get("path_patterns") or ("/*",)),
            actions=frozenset(data.get("actions") or ()) or cls.actions,
            allow_irreversible=bool(data.get("allow_irreversible", False)),
        )

    @classmethod
    def load(cls, path: Path) -> "Allowlist":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# A policy with nothing permitted. Used as the default everywhere, so that
# forgetting to pass a policy fails closed rather than open.
DENY_ALL = Allowlist(origins=(), path_patterns=(), actions=frozenset(), label="deny-all")

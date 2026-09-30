"""Reusing one capability across tenants running the same vendor product.

The environment in §1 is hundreds of institutions running roughly twenty
applications each, and many of them are the *same* vendor product, branded and
versioned differently. Re-recording a flow per tenant does not scale, and
forking the artifact per tenant means a fix has to be applied hundreds of
times.

So the artifact stays at the vendor level and a tenant contributes an overlay:
a small, reviewable document of differences, resolved at load time. Three
kinds of difference cover nearly everything, in increasing order of how much
they worry us:

**A different address.** The same product at a different host. One field.

**Different wording.** By far the most common: the vendor ships a product
where captions are configurable, and one institution's "Member Number" is
another's "Account Number". Because a locator ladder already carries the form
field name above the caption, a rebranded label usually costs nothing at all
-- the asserted identifier still resolves and the alias is never needed. The
alias map is what handles the case where it does.

**A different control.** A tenant on an older release where a step genuinely
targets something else. This replaces one step's locator, and it is the kind
of divergence worth reviewing, because enough of them means the tenant is
really running a different product and deserves its own artifact.

An overlay may not add, remove or reorder steps. A tenant that needs a
different *flow* does not have an overlay, it has a different capability, and
letting the two blur is how a "shared" artifact quietly becomes hundreds of
incompatible ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .capability import Capability, Step
from .errors import ContractError, require, require_str
from .locator import Locator, LocatorCandidate

OVERLAY_SUFFIX = ".overlay.json"


@dataclass(frozen=True)
class TenantOverlay:
    tenant: str
    capability_id: str
    capability_version: str
    entry_url: str | None = None
    label_aliases: dict[str, str] = field(default_factory=dict)
    step_targets: dict[int, Locator] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tenant": self.tenant,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
        }
        if self.entry_url:
            out["entry_url"] = self.entry_url
        if self.label_aliases:
            out["label_aliases"] = dict(self.label_aliases)
        if self.step_targets:
            out["step_targets"] = {
                str(k): v.to_dict() for k, v in sorted(self.step_targets.items())
            }
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_dict(cls, data: Any, path: str = "overlay") -> "TenantOverlay":
        if not isinstance(data, dict):
            raise ContractError(path, "expected an object")
        raw_targets = data.get("step_targets") or {}
        if not isinstance(raw_targets, dict):
            raise ContractError(f"{path}.step_targets", "expected an object keyed by step index")
        return cls(
            tenant=require_str(data, "tenant", path),
            capability_id=require_str(data, "capability_id", path),
            capability_version=require_str(data, "capability_version", path),
            entry_url=data.get("entry_url"),
            label_aliases=dict(data.get("label_aliases") or {}),
            step_targets={
                int(k): Locator.from_dict(v, f"{path}.step_targets[{k}]")
                for k, v in raw_targets.items()
            },
            notes=data.get("notes", ""),
        )


def _alias_locator(locator: Locator, aliases: dict[str, str]) -> Locator:
    """Rewrite caption-based candidates for this tenant's wording.

    Only `role_and_name` candidates are touched. An asserted identifier is not
    a caption and must never be rewritten by a branding alias -- doing so
    would turn a cosmetic difference into a targeting error.
    """
    if not aliases:
        return locator

    changed = False
    rewritten: list[LocatorCandidate] = []
    for candidate in locator.candidates:
        if candidate.strategy == "role_and_name" and candidate.name in aliases:
            rewritten.append(replace(candidate, name=aliases[candidate.name]))
            changed = True
        elif candidate.strategy == "grid_cell" and candidate.column in aliases:
            rewritten.append(replace(candidate, column=aliases[candidate.column]))
            changed = True
        else:
            rewritten.append(candidate)

    return replace(locator, candidates=tuple(rewritten)) if changed else locator


def apply_overlay(capability: Capability, overlay: TenantOverlay) -> Capability:
    """Resolve a capability for one tenant. The base artifact is untouched."""
    require(
        overlay.capability_id == capability.id,
        "overlay.capability_id",
        f"overlay is for {overlay.capability_id!r}, not {capability.id!r}",
    )
    require(
        overlay.capability_version == capability.version,
        "overlay.capability_version",
        f"overlay targets version {overlay.capability_version!r}, "
        f"capability is {capability.version!r}. An overlay pinned to an old "
        f"version must be reviewed against the new one before it is used.",
    )
    for index in overlay.step_targets:
        require(
            1 <= index <= len(capability.steps),
            "overlay.step_targets",
            f"step {index} does not exist in this capability",
        )

    aliases = overlay.label_aliases

    steps = tuple(
        replace(
            step,
            target=(
                overlay.step_targets.get(step.index)
                or (_alias_locator(step.target, aliases) if step.target else None)
            ),
        )
        for step in capability.steps
    )

    outputs = tuple(
        replace(output, source=_alias_locator(output.source, aliases))
        for output in capability.outputs
    )

    surface = capability.surface
    if overlay.entry_url:
        surface = replace(surface, entry_url=overlay.entry_url)

    return replace(capability, steps=steps, outputs=outputs, surface=surface)


def load_overlay(path: Path) -> TenantOverlay:
    return TenantOverlay.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def save_overlay(overlay: TenantOverlay, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (
        f"{overlay.capability_id}@{overlay.capability_version}"
        f".{overlay.tenant}{OVERLAY_SUFFIX}"
    )
    path.write_text(
        json.dumps(overlay.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path

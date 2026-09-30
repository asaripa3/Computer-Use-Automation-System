"""Print a capability for human review.

    python -m contract.cli member.savings_balance@1.0.0

§3.2 asks for an artifact both a reviewer and a calling agent can understand.
The JSON serves the agent. This serves the reviewer, and it is deliberately
opinionated about what a reviewer needs to see first: what the capability
will commit, what data it touches, and which of its targets are weakest.
Those three questions decide whether it should be approved, and none of them
are easy to answer by reading a hundred lines of JSON.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .capability import Capability
from .io import catalog, load
from .locator import Locator

CAPABILITIES = Path(__file__).resolve().parents[2] / "capabilities"


def _condition(condition) -> str:
    if condition.kind == "text_present":
        return f'text present: "{condition.text}"'
    if condition.kind == "url_matches":
        return f"url matches: {condition.pattern}"
    if condition.locator is not None:
        return f"{condition.kind.replace('_', ' ')}: {condition.locator.description}"
    return condition.kind


def _locator_lines(locator: Locator, indent: str) -> list[str]:
    frame = f"  [{locator.frame}]" if locator.frame else ""
    lines = [f"{indent}target: {locator.description}{frame}"]
    for candidate in locator.ladder():
        lines.append(f"{indent}  {candidate.robustness:<11} {candidate.describe()}")
    return lines


def render(capability: Capability, *, verbose: bool = False) -> str:
    out: list[str] = []
    add = out.append

    add(f"{capability.ref}   [{capability.status}]")
    add(capability.title)
    add("")
    add(f"  {capability.description}")
    add("")

    surface = capability.surface
    add(f"SURFACE   {surface.kind} · {surface.entry_url}")
    if surface.app_fingerprint:
        add(f"          recorded against {surface.app_fingerprint}")
    if surface.requires_origins:
        add(f"          origins: {', '.join(surface.requires_origins)}")

    if capability.inputs:
        add("")
        add("INPUTS")
        for spec in capability.inputs:
            flags = [spec.type, "required" if spec.required else "optional"]
            if spec.sensitivity != "none":
                flags.append(spec.sensitivity.upper())
            add(f"  {spec.name:<18} {' · '.join(flags)}")
            add(f"  {'':<18} {spec.description}")
            if spec.pattern:
                add(f"  {'':<18} must match {spec.pattern}")
            if spec.choices:
                add(f"  {'':<18} one of: {', '.join(spec.choices)}")

    if capability.outputs:
        add("")
        add("OUTPUTS")
        for spec in capability.outputs:
            flags = [spec.type] + (["optional"] if spec.optional else [])
            if spec.sensitivity != "none":
                flags.append(spec.sensitivity.upper())
            add(f"  {spec.name:<18} {' · '.join(flags)}")
            add(f"  {'':<18} {spec.description}")
            add(f"  {'':<18} from {spec.source.description}")

    add("")
    add("STEPS")
    for step in capability.steps:
        marker = "  !! " if step.is_irreversible else "  "
        value = ""
        if step.value is not None:
            value = (
                f"  <- input {step.value.from_input}"
                if step.value.is_parameterised
                else f"  <- {step.value.literal!r}"
            )
        add(f"{marker}{step.index:>2}  {step.action:<9} {step.description}{value}")
        if step.url:
            add(f"        url: {step.url}")
        if verbose and step.target is not None:
            out.extend(_locator_lines(step.target, "        "))
        if step.expect is not None:
            add(f"        expect: {_condition(step.expect)} "
                f"(waits up to {capability.timeout_for(step) / 1000:g}s)")

    add("")
    add(f"SUCCESS   {_condition(capability.success)}")

    add("")
    add("OUTCOMES  (declared endings that are not success)")
    for outcome in capability.outcomes:
        add(f"  {outcome.code:<26} {outcome.kind:<9} {_condition(outcome.detect)}")
        add(f"  {'':<26} {outcome.description}")

    if capability.recoveries:
        add("")
        add("RECOVERIES  (runtime conditions replay may absorb)")
        for recovery in capability.recoveries:
            add(f"  {recovery.name:<26} {recovery.action:<9} "
                f"up to {recovery.max_attempts}x · {_condition(recovery.detect)}")

    add("")
    add("REVIEW")
    if capability.is_irreversible:
        step = capability.irreversible_step
        add(f"  commits      step {step.index}: {step.description}")
    else:
        add("  commits      nothing; this capability is read-only")

    sensitive = [
        s.name for s in (*capability.inputs, *capability.outputs)
        if s.sensitivity in {"pii", "secret"}
    ]
    add(f"  sensitive    {', '.join(sensitive) if sensitive else 'no PII or secrets declared'}")

    weak = capability.weakest_targets
    if weak:
        add(f"  weak targets step(s) {', '.join(str(s.index) for s in weak)} can only be "
            f"found by document position")
    else:
        add("  weak targets none; every step has a named or asserted target")

    if capability.provenance.recorded_by:
        add("")
        add(f"RECORDED  {capability.provenance.recorded_by}")
        if capability.provenance.goal:
            add(f"          goal: {capability.provenance.goal}")
        if capability.provenance.recorded_at:
            add(f"          at:   {capability.provenance.recorded_at}")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contract.cli", description=__doc__)
    parser.add_argument("ref", nargs="?", help="capability reference, id@version")
    parser.add_argument("--dir", default=str(CAPABILITIES), help="capability directory")
    parser.add_argument("--quiet", action="store_true", help="hide the locator ladders")
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    available = catalog(directory)

    if not args.ref:
        if not available:
            print(f"no capabilities in {directory}", file=sys.stderr)
            return 1
        print(f"{len(available)} capabilit{'y' if len(available) == 1 else 'ies'} "
              f"in {directory}:\n")
        for capability in available:
            commits = "commits" if capability.is_irreversible else "read-only"
            print(f"  {capability.ref:<34} [{capability.status}] {commits:<10} {capability.title}")
        print("\npass a reference to review one in full")
        return 0

    match = next((c for c in available if c.ref == args.ref or c.id == args.ref), None)
    if match is None:
        print(f"no capability {args.ref!r} in {directory}", file=sys.stderr)
        print(f"available: {', '.join(c.ref for c in available)}", file=sys.stderr)
        return 1

    print(render(match, verbose=not args.quiet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

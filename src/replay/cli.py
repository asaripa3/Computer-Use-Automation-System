"""Invoke a saved capability. This is the production execution path.

    python -m replay.cli member.savings_balance@1.0.0 --input member_id=12345

No model is involved. Everything the run does is read from the artifact.

Credentials are read from the environment here and nowhere else. They are
never written into an artifact, never passed through the engine, and never
logged -- the engine receives a *hook* it can call to sign in again, not the
values themselves, which is what lets §3.4's rule about never persisting
secrets hold all the way down.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import envfile
from contract import io
from contract.overlay import apply_overlay, load_overlay
from policy.allowlist import Allowlist
from policy.risk import Authorization
from surface.browser import browser_session

from .engine import replay
from .evidence import Evidence

ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES = ROOT / "capabilities"
EVIDENCE = ROOT / "evidence"


def sign_on(base: str):
    """Return a hook that signs the operator on, driven through the surface layer.

    Handed to the engine so that an expired session can be recovered without
    the engine ever seeing a password.
    """

    def hook(surface) -> None:
        surface.goto(f"{base}/login")
        observation = surface.observe()
        operator = observation.field("Operator ID")
        if operator is None:
            raise RuntimeError(f"no sign-on form at {base}/login -- is ShareBase running?")
        surface.fill(operator.ref, os.environ.get("SHAREBASE_USER", "svc_agent"))
        surface.fill(
            observation.field("Password").ref,
            os.environ.get("SHAREBASE_PASS", "Demo-Pass-1234"),
        )
        surface.click(observation.find(role="button", name="Sign On")[0].ref)

    return hook


def parse_inputs(pairs: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--input expects name=value, got {pair!r}")
        name, _, value = pair.partition("=")
        values[name.strip()] = value
    return values


def render(result) -> str:
    lines = [result.summary(), ""]
    lines.append(f"  status          {result.status}")
    lines.append(f"  needs attention {result.needs_attention}")
    lines.append(f"  duration        {result.duration_ms} ms")

    if result.outputs:
        lines.append("")
        lines.append("  outputs")
        for name, value in result.outputs.items():
            lines.append(f"    {name:<22} {value!r}")

    if result.outcome:
        lines.append("")
        lines.append(f"  outcome         {result.outcome.code} ({result.outcome.kind})")
        lines.append(f"                  {result.outcome.description}")

    if result.failure:
        failure = result.failure
        lines.append("")
        lines.append(f"  failure         {failure.kind}")
        if failure.step_index is not None:
            lines.append(f"    at step       {failure.step_index} -- {failure.step_description}")
        lines.append(f"    expected      {failure.expected}")
        lines.append(f"    observed      {failure.observed}")
        if failure.detail:
            lines.append(f"    detail        {failure.detail}")

    if result.steps:
        lines.append("")
        lines.append("  steps")
        for step in result.steps:
            via = f" via {step.resolved_by}" if step.resolved_by else ""
            recovered = (
                f"  recovered: {', '.join(step.recoveries_applied)}"
                if step.recoveries_applied else ""
            )
            lines.append(
                f"    {step.index:>2}  {step.status:<9} {step.action:<9} "
                f"{step.duration_ms:>5}ms{via}{recovered}"
            )

    if result.drift:
        lines.append("")
        lines.append("  drift  (the run worked, but the surface has moved)")
        for signal in result.drift:
            lines.append(
                f"    step {signal.step_index}: {signal.target} -- recorded "
                f"{signal.recorded_best}, resolved by {signal.resolved_by}"
            )

    if result.evidence_dir:
        lines.append("")
        lines.append(f"  evidence        {result.evidence_dir}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    envfile.load()
    parser = argparse.ArgumentParser(prog="replay.cli", description=__doc__)
    parser.add_argument("ref", help="capability reference, id@version")
    parser.add_argument("--input", action="append", metavar="NAME=VALUE",
                        help="an invocation parameter; repeatable")
    parser.add_argument("--base", default=os.environ.get("SHAREBASE_BASE",
                                                         "http://127.0.0.1:8080"))
    parser.add_argument("--tenant", help="apply a tenant overlay by name")
    parser.add_argument("--authorize", metavar="WHO",
                        help="authorise this capability's irreversible step")
    parser.add_argument("--allow-irreversible", action="store_true",
                        help="use a policy that permits unattended commits")
    parser.add_argument("--evidence", metavar="DIR",
                        help="write the run record here (default: a timestamped "
                             "directory under evidence/)")
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--headed", action="store_true", help="watch the browser work")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    available = io.catalog(CAPABILITIES)
    capability = next((c for c in available if c.ref == args.ref or c.id == args.ref), None)
    if capability is None:
        print(f"no capability {args.ref!r}", file=sys.stderr)
        print(f"available: {', '.join(c.ref for c in available)}", file=sys.stderr)
        return 2

    if args.tenant:
        path = (CAPABILITIES / "overlays" /
                f"{capability.id}@{capability.version}.{args.tenant}.overlay.json")
        if not path.exists():
            print(f"no overlay for tenant {args.tenant!r} at {path}", file=sys.stderr)
            return 2
        capability = apply_overlay(capability, load_overlay(path))

    base = args.base.rstrip("/")
    policy = Allowlist(
        label="attended" if args.allow_irreversible else "read-only",
        origins=(base,),
        # The fault console is deliberately outside the allowlist: automation
        # able to disarm the conditions it is being tested against would make
        # every robustness result meaningless.
        path_patterns=("/login", "/console/*"),
        allow_irreversible=args.allow_irreversible,
    )

    authorization = (
        Authorization(capability.ref, args.authorize, reason="supplied on the command line")
        if args.authorize else None
    )

    evidence = None
    if not args.no_evidence:
        directory = Path(args.evidence) if args.evidence else (
            EVIDENCE / f"replay-{capability.id}-"
                       f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        )
        evidence = Evidence(directory)

    hook = sign_on(base)
    with browser_session(headless=not args.headed) as surface:
        hook(surface)
        result = replay(
            capability, parse_inputs(args.input), surface,
            policy=policy, authorization=authorization,
            tenant=args.tenant, evidence=evidence, reauthenticate=hook,
        )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(render(result))

    # A business outcome is an answer, so it exits 0. Only a failure is a
    # non-zero exit, because that is what a caller's automation will branch on.
    return 1 if result.needs_attention else 0


if __name__ == "__main__":
    raise SystemExit(main())

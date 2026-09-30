"""Run a discovery session against the target application.

    python -m explore.cli "look up member 12345 and read their savings balance" \\
        --id member.savings_balance

Needs OPENAI_API_KEY. Without one, `--transcript` replays a previously
recorded run's decisions against the live application -- the same tools
execute and the same artifact comes out, so the loop can be seen working
without an API call.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import envfile
from contract import io
from contract.capability import Condition, RecoverySpec
from contract.locator import Locator, LocatorCandidate
from policy.allowlist import Allowlist
from replay.evidence import Evidence
from surface.browser import browser_session

from .loop import DEFAULT_MAX_STEPS, DEFAULT_TIME_LIMIT_S, discover
from .model import NoModelAvailable, default_model, provider_for

ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES = ROOT / "capabilities"
EVIDENCE = ROOT / "evidence"


def house_recoveries() -> tuple[RecoverySpec, ...]:
    """Runtime conditions every capability against this application inherits.

    Deliberately not left to the model. An interstitial or an expired session
    is a property of the *application*, not of the flow being recorded, and
    asking a model to rediscover them on every run would make each artifact's
    robustness depend on whether it happened to trip over them.
    """
    return (
        RecoverySpec(
            name="maintenance_notice",
            description="A system notice is served in place of the requested page.",
            detect=Condition("text_present", "the maintenance advisory banner",
                             text="Scheduled maintenance advisory"),
            action="dismiss",
            target=Locator(
                description="the Continue button on the notice",
                frame="contentFrame",
                candidates=(LocatorCandidate(
                    "role_and_name", "derived", role="button", name="Continue",
                    name_source="value", rationale="the notice's only control"),),
            ),
            max_attempts=1,
        ),
        RecoverySpec(
            name="transient_server_error",
            description="A timeout from the data tier that clears on retry.",
            detect=Condition("text_present", "the timeout error page",
                             text="Timeout expired"),
            action="retry",
            max_attempts=2,
        ),
        RecoverySpec(
            name="session_expired",
            description="The console session timed out and bounced to sign-on.",
            detect=Condition("text_present", "the expiry notice",
                             text="Your session has expired"),
            action="reauthenticate",
            max_attempts=1,
        ),
    )


def sign_on(base: str):
    def hook(surface) -> None:
        surface.goto(f"{base}/login")
        observation = surface.observe()
        operator = observation.field("Operator ID")
        if operator is None:
            raise SystemExit(f"no sign-on form at {base}/login -- is ShareBase running?")
        surface.fill(operator.ref, os.environ.get("SHAREBASE_USER", "svc_agent"))
        surface.fill(observation.field("Password").ref,
                     os.environ.get("SHAREBASE_PASS", "Demo-Pass-1234"))
        surface.click(observation.find(role="button", name="Sign On")[0].ref)

    return hook


def main(argv: list[str] | None = None) -> int:
    envfile.load()
    parser = argparse.ArgumentParser(prog="explore.cli", description=__doc__)
    parser.add_argument("goal", help="what to accomplish, in plain language")
    parser.add_argument("--id", default="recorded.capability",
                        help="capability id for the artifact")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--base", default=os.environ.get("SHAREBASE_BASE",
                                                         "http://127.0.0.1:8080"))
    parser.add_argument("--entry", default="/console/search",
                        help="path to start from")
    parser.add_argument("--model", default=None,
                        help="override OPENAI_MODEL for this run")
    parser.add_argument("--transcript", type=Path,
                        help="replay a recorded run's decisions instead of "
                             "calling a model")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT_S,
                        help="wall-clock seconds before the run is stopped")
    parser.add_argument("--allow-irreversible", action="store_true",
                        help="permit the model to commit something during discovery")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--out", type=Path, default=CAPABILITIES,
                        help="where to write the artifact")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    try:
        provider = provider_for(transcript=args.transcript, model=args.model)
    except NoModelAvailable as exc:
        print(exc, file=sys.stderr)
        return 2

    policy = Allowlist(
        label="discovery",
        origins=(base,),
        # The fault console stays out of reach during discovery too: a model
        # that could disarm the conditions it is meant to learn about would
        # record a capability that only works on a good day.
        path_patterns=("/login", "/console/*"),
        allow_irreversible=args.allow_irreversible,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = EVIDENCE / f"discovery-{args.id}-{stamp}"
    evidence = Evidence(directory)

    print(f"goal:  {args.goal}")
    print(f"model: {provider.name}")
    print(f"base:  {base}{args.entry}\n")

    hook = sign_on(base)
    with browser_session(headless=not args.headed) as surface:
        hook(surface)
        surface.goto(f"{base}{args.entry}")
        run = discover(
            args.goal, surface,
            provider=provider, policy=policy, base=base, entry_path=args.entry,
            capability_id=args.id, version=args.version,
            app_fingerprint="ShareBase 4.2.1",
            recoveries=house_recoveries(),
            max_steps=args.max_steps, time_limit_s=args.time_limit,
            transcript=directory / "transcript.jsonl",
            evidence=evidence,
        )

    print(f"stopped after {run.turns} turns ({run.elapsed_s:g}s): {run.stopped_because}")
    if run.summary:
        print(f"summary: {run.summary}")
    if run.give_up_reason:
        print(f"gave up: {run.give_up_reason}")

    if not run.ok:
        print("\nno capability was produced:", file=sys.stderr)
        for problem in run.problems:
            print(f"  - {problem}", file=sys.stderr)
        print(f"\nevidence: {directory}", file=sys.stderr)
        return 1

    path = io.save(run.capability, args.out)
    print(f"\nwrote {path}")
    print(f"evidence: {directory}")
    print(f"\nReview it, then replay it:\n"
          f"  make review CAP={run.capability.ref}\n"
          f"  make replay CAP={run.capability.ref} "
          f"IN=\"--input {' --input '.join(f'{i.name}={i.example}' for i in run.capability.inputs)}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

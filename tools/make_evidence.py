"""Produce the replay evidence runs in evidence/.

§6 asks for logs from a replay run, and ideally one replay that hits an error
or exceptional state so the classification can be seen working. Three runs are
produced, one for each branch of the taxonomy:

    replay-success       the flow completes and returns typed outputs
    replay-not-found     a legitimate business answer -- no such member
    replay-hard-failure  the application breaks mid-flow

The discovery evidence arrives in step 4, when there is a model to record.

    PYTHONPATH=src python3 tools/make_evidence.py
"""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract import io  # noqa: E402
from policy.allowlist import Allowlist  # noqa: E402
from handoff.control import Control  # noqa: E402
from handoff.coordinator import Coordinator  # noqa: E402
from handoff.intervention import Resolution  # noqa: E402
from handoff.operator import ScriptedOperator  # noqa: E402
from policy.risk import Authorization  # noqa: E402
from replay.engine import replay  # noqa: E402
from replay.evidence import Evidence  # noqa: E402
from sharebase.data import store  # noqa: E402
from sharebase.faults import board  # noqa: E402
from surface.browser import browser_session  # noqa: E402

EVIDENCE = ROOT / "evidence"
PORT = 8097
BASE = f"http://127.0.0.1:{PORT}"


def sign_on(surface) -> None:
    surface.goto(f"{BASE}/login")
    observation = surface.observe()
    surface.fill(observation.field("Operator ID").ref, "svc_agent")
    surface.fill(observation.field("Password").ref, "Demo-Pass-1234")
    surface.click(observation.find(role="button", name="Sign On")[0].ref)


def run(name: str, inputs: dict, *, fault: str | None, note: str) -> None:
    from dataclasses import replace as _replace

    board.reset()
    store.reset()
    if fault:
        board.arm(fault)

    capability = io.load(EVIDENCE.parent / "capabilities" /
                         "member.savings_balance@1.0.0.capability.json")
    capability = _replace(
        capability,
        surface=_replace(capability.surface, entry_url=f"{BASE}/console/search",
                         requires_origins=(BASE,)),
    )
    policy = Allowlist(label="read-only", origins=(BASE,),
                       path_patterns=("/login", "/console", "/console/*"))

    directory = EVIDENCE / name
    if directory.exists():
        shutil.rmtree(directory)
    evidence = Evidence(directory)

    with browser_session() as surface:
        sign_on(surface)
        result = replay(capability, inputs, surface, policy=policy,
                        evidence=evidence, reauthenticate=sign_on)

    (directory / "README.md").write_text(
        f"# {name}\n\n{note}\n\n"
        f"    {result.summary()}\n\n"
        f"Reproduce with ShareBase running (`make run`):\n\n"
        f"```bash\n"
        + (f"make fault F={fault}\n" if fault else "")
        + f"make replay CAP=member.savings_balance@1.0.0 "
          f"IN=\"--input member_id={inputs['member_id']}\"\n"
          f"```\n",
        encoding="utf-8",
    )

    print(f"  {name:<22} {result.status:<17} {result.summary()[:70]}")


def escalation_run() -> None:
    """A replay that stops at an irreversible step and asks a person.

    The operator in this recording confirms the share themselves in the same
    live session and answers "skip". The automation does not take their word
    for it: the step's checkpoint is still evaluated afterwards.
    """
    from dataclasses import replace as _replace

    board.reset()
    store.reset()

    capability = io.load(EVIDENCE.parent / "capabilities" /
                         "member.open_subaccount@1.0.0.capability.json")
    capability = _replace(
        capability,
        surface=_replace(capability.surface, entry_url=f"{BASE}/console/search",
                         requires_origins=(BASE,)),
    )
    policy = Allowlist(label="attended", origins=(BASE,),
                       path_patterns=("/login", "/console", "/console/*"),
                       allow_irreversible=True)

    directory = EVIDENCE / "replay-escalation"
    if directory.exists():
        shutil.rmtree(directory)
    evidence = Evidence(directory)

    with browser_session() as surface:
        sign_on(surface)

        class ConfirmsItThemselves(ScriptedOperator):
            def wait(self, request, *, poll):
                poll()
                observation = surface.observe()
                confirm = observation.find(role="button", name="Confirm")
                if confirm:
                    surface.click(confirm[0].ref)
                poll()
                return self.resolution

        coordinator = Coordinator(
            operator=ConfirmsItThemselves(Resolution(
                "skip", by="operator@branch",
                note="member confirmed by phone; I pressed Confirm myself",
            )),
            control=Control(),
            evidence=evidence,
        )

        result = replay(
            capability,
            {"member_id": "12345", "account_type": "Vacation Club Savings",
             "nickname": "Vacation 2027", "initial_deposit": "150.00",
             "funding_account": "0001234502"},
            surface, policy=policy, evidence=evidence,
            coordinator=coordinator, reauthenticate=sign_on,
        )

    (directory / "README.md").write_text(
        "# replay-escalation\n\n"
        "A replay that reaches an irreversible step it may not take on its "
        "own, hands the live session to a person, and carries on from what "
        "they decided.\n\n"
        "The run completes ten steps first, so the operator arrives at the "
        "review screen with the request already filled in rather than being "
        "handed a blank form. They confirm it themselves in that same signed-in "
        "session and answer `skip`.\n\n"
        "The automation does not take that on trust: the step's checkpoint is "
        "still evaluated, so an operator who said they did something they did "
        "not would produce a failed checkpoint rather than a run carrying on "
        "from a state nobody verified.\n\n"
        f"    {result.summary()}\n\n"
        "The request carries a redacted page structure as the operator found it. "
        "`run.jsonl` holds the request, what they decided, and the pages and "
        "field changes observed while they held the session.\n\n"
        "Reproduce it interactively, working in the browser window yourself:\n\n"
        "```bash\n"
        "make replay CAP=member.open_subaccount@1.0.0 \\\n"
        "  IN=\"--input member_id=12345 --input account_type='Vacation Club Savings' \\\n"
        "      --input nickname='Vacation 2027' --input initial_deposit=150.00 \\\n"
        "      --input funding_account=0001234502 \\\n"
        "      --allow-irreversible --operator console --headed\"\n"
        "```\n",
        encoding="utf-8",
    )
    print(f"  {'replay-escalation':<22} {result.status:<17} {result.summary()[:60]}")


def main() -> int:
    from werkzeug.serving import make_server

    from sharebase import create_app

    server = make_server("127.0.0.1", PORT, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    EVIDENCE.mkdir(exist_ok=True)

    try:
        print("writing replay evidence:")
        run("replay-success", {"member_id": "12345"}, fault=None,
            note="A clean replay. The flow completes, the checkpoint holds, and "
                 "both declared outputs are read and coerced to their declared "
                 "types -- note `savings_balance` arrives as `4821.37` from a "
                 "screen that renders `4,821.37`.")
        run("replay-not-found", {"member_id": "99999"}, fault=None,
            note="A legitimate business answer. No member record exists, so the "
                 "run reports `MEMBER_NOT_FOUND` with `needs_attention` false. "
                 "This is the case the brief calls out: it is an answer the "
                 "caller asked for, not an incident.")
        run("replay-hard-failure", {"member_id": "12345"}, fault="hard_error",
            note="The application fails mid-flow with an error the artifact does "
                 "not declare. The run stops and reports which step failed, what "
                 "it expected and what it observed, with a redacted record "
                 "of the page structure the surface layer perceived at that "
                 "moment.")

        escalation_run()

        # The artifact itself belongs with the runs that exercise it.
        shutil.copy(
            ROOT / "capabilities" / "member.savings_balance@1.0.0.capability.json",
            EVIDENCE / "member.savings_balance@1.0.0.capability.json",
        )
        print(f"\nwrote {EVIDENCE.relative_to(Path.cwd())}/")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

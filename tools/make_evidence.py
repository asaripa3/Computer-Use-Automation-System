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
                       path_patterns=("/login", "/console/*"))

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
                 "it expected and what it observed, with a screenshot and a "
                 "record of everything the surface layer perceived at that "
                 "moment.")

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

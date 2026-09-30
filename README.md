# Computer-Use Automation System

An LLM drives a legacy back-office application once to work out how to complete
a goal; that run is compiled into a typed, versioned capability artifact; and
production invocations replay the artifact deterministically, with no model in
the decision loop.

> The model discovers. The artifact becomes a reusable capability.
> Deterministic replay is how the calling agent invokes it.

## Build status

Built as a waterfall — each step is finished and tested before the next begins.
Every completed step has a walkthrough in [`docs/steps/`](docs/steps/).

| Step | Component | State |
|-----|-----------|-------|
| 0 | [ShareBase target application](docs/steps/step-0-target-app.md) | **done** — 51 tests |
| 1 | [The surface layer — perceive and act](docs/steps/step-1-surface-layer.md) | **done** — 63 tests |
| 2 | [The capability contract and guardrails](docs/steps/step-2-capability-contract.md) | **done** — 95 tests |
| 3 | Deterministic replay engine | not started |
| 4 | LLM discovery loop and recorder | not started |
| 5 | Escalation and control transfer | not started |
| 6 | Evidence runs and `REPORT.md` | not started |

Step 3 is deliberately built before step 4: proving replay against a
hand-written artifact forces the schema to be right before any model run
depends on it.

## Layout

```
src/
  sharebase/          the target application -- the thing being automated
tests/
  sharebase/          its surface, flow and fault tests
docs/
  steps/             one walkthrough per completed build step
  assignment.pdf     the brief
evidence/            discovery and replay runs (step 6)
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env
```

`.env.example` holds local demo values and no real secrets. The ShareBase
sign-on credentials exist only so the automation has a login step to learn.

## Running

```bash
make run                                   # ShareBase on http://127.0.0.1:8080
make test                                  # the full suite
make observe URL=/console/member/12345     # what the surface layer sees on a page
make review                                # the capability catalog
make help                                  # every available command
```

Sign on with `svc_agent` / `Demo-Pass-1234` (or whatever is in your `.env`).

The demo path — a goal, a discovery run, then a deterministic replay of the
resulting artifact — arrives with step 6.

## The target application

ShareBase is a fictional credit-union member servicing console standing in for
the systems this project automates. It is deliberately hostile in the ways real
systems of its era are: a frameset console, control ids regenerated on every
render, no test ids and no `<label for>` anywhere, nested-table layout that
collapses to unassociated nodes in the accessibility tree, and balances
rendered without a currency symbol.

Every runtime condition a replay must survive — session expiry, interstitials,
transient errors, permission denials, validation failures — can be armed on
demand from `/admin/faults` or over HTTP.

Full detail, including why it is built rather than borrowed and what each file
does: [`docs/steps/step-0-target-app.md`](docs/steps/step-0-target-app.md).

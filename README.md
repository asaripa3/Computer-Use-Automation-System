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
| 3 | [Deterministic replay](docs/steps/step-3-replay.md) | **done** — 53 tests |
| 4 | [The discovery loop](docs/steps/step-4-discovery.md) | **done** — 35 tests, real run in `evidence/` |
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
evidence/            saved runs: success, business outcome, hard failure
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env
```

`.env.example` holds local demo values and no real secrets. The ShareBase
sign-on credentials exist only so the automation has a login step to learn.

**An API key is needed for one thing only.** Recording a capability calls a
model once; replaying one never does. Everything except `make discover`
runs with no key at all, including the full test suite.

## Running

```bash
make run                                   # ShareBase on http://127.0.0.1:8080
make test                                  # the full suite
make observe URL=/console/member/12345     # what the surface layer sees on a page
make review                                # the capability catalog
make help                                  # every available command
```

## Recording a capability

A model drives the application once, and what it learned is compiled into a
reusable artifact. This is the only place a model is ever called.

```bash
export OPENAI_API_KEY=...
make discover GOAL="look up member 12345 and read their current savings balance" \
              ID=member.savings_balance
```

Without a key, a recorded run's decisions replay against the live application
and produce the same artifact — the same tools execute, only the network call
to the model is skipped:

```bash
make discover GOAL="..." ID=member.savings_balance \
              TRANSCRIPT=evidence/discovery-.../transcript.jsonl
```

## Replaying a capability

With ShareBase running, this is the production path an agent would trigger —
no model involved in any decision:

```bash
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"
```

Three endings, all reachable on demand:

```bash
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=99999"
```
a legitimate business answer (`MEMBER_NOT_FOUND`), which exits zero because it
is not an incident.

```bash
make fault F=hard_error
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"
make reset
```
a hard failure, reporting which step failed, what it expected and what it
observed, with a screenshot and a record of everything perceived at that
moment.

Saved runs of all three are in [`evidence/`](evidence/), alongside
[`evidence/discovery-run/`](evidence/discovery-run/) — a real gpt-4.1
session recording `member.savings_balance@0.1.0`, which replays for a
member the model never saw.

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

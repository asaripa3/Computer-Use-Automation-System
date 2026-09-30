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

## Demo path

Four commands, end to end. Start the target application in one terminal:

```bash
make run
```

**1. Record a capability.** A model drives the application once; what it
learned is compiled into a typed artifact. This is the only place a model is
ever called.

```bash
make discover GOAL="look up member 12345 and read their current savings balance" ID=member.savings_balance
```

Needs `OPENAI_API_KEY`. Without one, replay the decisions of the run committed
in `evidence/` instead — the same tools execute against the same live
application and the same artifact comes out, only the model call is skipped:

```bash
make discover GOAL="look up member 12345 and read their current savings balance" ID=member.savings_balance TRANSCRIPT=evidence/discovery-run/transcript.jsonl
```

Either way it writes `capabilities/member.savings_balance@0.1.0.capability.json`.

**2. Review what it recorded**, before trusting it with anything:

```bash
make review CAP=member.savings_balance@0.1.0
```

**3. Replay it.** No model is involved in any decision from here on.

```bash
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=12345"
```

**4. Replay it for a member the model never saw**, which is the difference
between a recording and a capability:

```bash
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=22001"
```

### The three endings

The same artifact, classified three different ways:

```bash
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=99999"
```
`MEMBER_NOT_FOUND` — a legitimate business answer. Exits zero, because it is
not an incident.

```bash
make fault F=hard_error
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=12345"
make reset
```
A hard failure, reporting which step failed, what it expected and what it
observed, with a screenshot and a record of everything perceived at that
moment.

Saved runs of all of these are in [`evidence/`](evidence/), including
[`evidence/discovery-run/`](evidence/discovery-run/) — a real gpt-4.1 session
with its full transcript and reasoning.

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

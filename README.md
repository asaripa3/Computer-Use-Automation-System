# Computer-Use Automation System

An LLM drives a legacy back-office application once to work out how to complete
a goal. That run is compiled into a typed, versioned capability artifact, and
production invocations replay the artifact deterministically with no model in
the decision loop.

> The model discovers. The artifact becomes a reusable capability.
> Deterministic replay is how the calling agent invokes it.

The design write-up is **[REPORT.md](REPORT.md)**, covering architecture, the
artifact schema, determinism and error handling, heterogeneity and multi-tenant
reuse, escalation, safety, and what was cut.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env
```

`make install` also downloads the browser Playwright drives. `.env.example`
holds local demo values and no real secrets.

**An API key is needed for one thing only.** Recording a capability calls a
model once. Replaying one never does. Everything except `make discover` runs
with no key at all, including the full test suite, and even `make discover` has
a no-key path shown below.

## Demo path

Start the target application in one terminal:

```bash
make run
```

Then, in another:

**1. Record a capability.** A model drives the application once, and what it
learned is compiled into a typed artifact.

```bash
export OPENAI_API_KEY=...
make discover GOAL="look up member 12345 and read their current savings balance" ID=member.savings_balance
```

Without a key, replay the decisions of the run committed in `evidence/`. The
same tools execute against the same live application and the same artifact
comes out. Only the call to the model is skipped:

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

**4. Replay it for a member the model never saw.** This is the difference
between a recording and a capability:

```bash
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=22001"
```

### The three endings

The same artifact, classified three different ways.

A legitimate business answer, which exits zero because it is not an incident:

```bash
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=99999"
```

A hard failure, reporting which step failed, what it expected and what it
observed, with a redacted record of the page structure at that moment. Raw
screenshots are not persisted because they can expose unstructured member data:

```bash
make fault F=hard_error
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=12345"
make reset
```

### When it needs a person

Some steps should not be taken by an automation on its own. The sub-account
flow ends in a commit, so it stops on the review screen and hands you the
browser window it was using, the same session, still signed in:

```bash
make handoff
```

Work in that window, then answer `retry` (let it take the step now you have
seen it), `skip` (you did it yourself) or `abandon`. The automation cannot act
while you hold the session, and a `skip` is checked against the page rather
than taken on trust.

Saved runs of all of the above are in [`evidence/`](evidence/), including
[`evidence/discovery-run/`](evidence/discovery-run/), a real gpt-4.1 session
with its full transcript and reasoning.

## Where to start reading

The code is arranged so that each layer depends only on the ones above it.
Reading in this order means never meeting a concept before it is introduced.

| Read | To understand |
|---|---|
| `src/surface/model.py` | The seam. `Node`, `Observation`, and the `Surface` protocol. Nothing above this layer knows what a browser is. |
| `src/contract/locator.py` | How a step says which control it means: an ordered ladder of candidates, ranked by how far the application vouches for each. |
| `src/contract/capability.py` | What a recorded flow is. Inputs, outputs, steps, checkpoints, declared outcomes, recoveries. |
| `src/contract/result.py` | What a run hands back. Success, business outcome, and failure as separate validated shapes. |
| `src/replay/engine.py` | The production path. The ordering inside a step is the load-bearing part. |
| `src/explore/tools.py` | What the model can do during discovery. Half the tools do not touch the application. |
| `src/handoff/control.py` | Who holds the session, enforced rather than recorded. |

A reviewer in a hurry can read `contract/locator.py` and `replay/engine.py`
and have most of the design.

### Layout

```
src/
  sharebase/         the target application, the thing being automated
  surface/           the seam: observing and acting on a surface
  contract/          what a recorded flow is, and what a run returns
  policy/            guardrails: allowlist, risk gating, redaction
  replay/            the production execution path, no model in the loop
  explore/           the discovery loop: a model drives the app once
  handoff/           bringing a person in, and giving them the live session
capabilities/        the capability artifacts themselves
evidence/            saved runs: discovery, success, business outcome, failure, handoff
docs/steps/          one walkthrough per build step
tests/               mirrors src/, plus the submission contract
```

## The target application

ShareBase is a fictional credit-union member servicing console, standing in for
the systems this project automates. It is deliberately hostile in the ways real
systems of its era are: a frameset console, control ids regenerated on every
render, no test ids and no `<label for>` anywhere, nested-table layout that
collapses to unassociated nodes in the accessibility tree, and balances
rendered without a currency symbol.

Every runtime condition a replay must survive can be armed on demand from
`/admin/faults` or over HTTP: session expiry, interstitials, transient errors,
permission denials, validation failures.

```bash
make fault F=interstitial_notice
make reset
```

Full detail in
[`docs/steps/step-0-target-app.md`](docs/steps/step-0-target-app.md).

## Tests

```bash
make test        # everything, about 3 minutes
make test-fast   # everything except the browser tests, about a second
make help        # every available command
```

The suite is split by what each part proves:

| Command | Proves |
|---|---|
| `make test-hostile` | the target is still as awkward as the systems it stands for |
| `make test-surface` | the surface layer sees and drives a real frameset application |
| `make test-contract` | a capability is coherent, and the shipped artifacts stay valid |
| `make test-policy` | allowlist, risk gating and redaction |
| `make test-replay` | the error taxonomy, against faults armed on demand |
| `make test-discovery` | a scripted run produces an artifact that actually replays |
| `make test-handoff` | control transfer, and that the automation cannot act during one |

## Build order

Built as a waterfall. Each step was finished and tested before the next began,
and each has a walkthrough in [`docs/steps/`](docs/steps/).

| Step | Component |
|-----|-----------|
| 0 | [ShareBase target application](docs/steps/step-0-target-app.md) |
| 1 | [The surface layer, perceive and act](docs/steps/step-1-surface-layer.md) |
| 2 | [The capability contract and guardrails](docs/steps/step-2-capability-contract.md) |
| 3 | [Deterministic replay](docs/steps/step-3-replay.md) |
| 4 | [The discovery loop](docs/steps/step-4-discovery.md) |
| 5 | [Escalation and handing over the session](docs/steps/step-5-handoff.md) |
| 6 | Evidence runs and [REPORT.md](REPORT.md) |

Replay was deliberately built before discovery. Proving it against a
hand-authored artifact forced the schema to be right before any model run
depended on it.

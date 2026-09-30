# Step 3 — Deterministic replay

**Status:** complete · 53 replay tests (297 total) · still no model anywhere

## Why this step exists

§3.3 is the production execution path: given a saved artifact and a set of
input parameters, re-run the flow **without invoking the LLM for decisions**,
using stable targeting, verifying the checkpoint, returning declared outputs,
and classifying whatever happens into business outcomes, recoverable
conditions and hard failures.

It is built before the discovery loop on purpose. Replaying artifacts that
were authored by hand forces the schema to be right while it is still cheap to
change, and it means nothing about the contract was shaped by whatever a model
happened to emit.

## Commands

```bash
make test-replay              # the browser-driven replay tests (~3 min)
make test-fast                # everything that does not need a browser (~1s)
make evidence                 # produce the runs in evidence/

# with ShareBase running (make run):
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=99999"

make fault F=hard_error       # then replay again to see a failure classified
make reset
```

## Files and what each one does

| File | Responsibility |
|------|----------------|
| `src/replay/resolve.py` | Walks the locator ladder. Returns which rung won, or *why* nothing resolved. |
| `src/replay/conditions.py` | Evaluates the four condition kinds against an observation; finds the first matching outcome or recovery. |
| `src/replay/engine.py` | The orchestrator. Owns the step ordering, the wait loop, the risk gate and the failure taxonomy. |
| `src/replay/evidence.py` | The run record: structured log, result, and a redacted page-shape snapshot on failure. |
| `src/replay/cli.py` | `make replay`. The only place credentials are read. |

| Test file | What it proves |
|-----------|----------------|
| `tests/replay/test_resolve.py` | The ladder's behaviour at the edges — drift, ambiguity, absence — without a browser. |
| `tests/replay/test_engine.py` | The taxonomy end to end against real armed faults. |

## The ordering that carries the whole step

Inside every step:

1. **absorb any declared recovery that is on screen**
2. **check for a declared outcome**
3. **only then judge the step's own expectation**

Getting this wrong is the mistake the brief warns about. If the checkpoint
were judged first, a page reading *"No member records match"* would be
reported as a failed checkpoint — an incident for somebody to investigate —
when it is the correct answer to the question the caller asked. Recoveries
come first for the same reason in reverse: an interstitial standing in front
of the page is not an ending, it is something in the way.

A measurable consequence: a not-found result returns in well under the step
timeout rather than waiting it out, and there is a test asserting exactly that.

## Waiting

Replay never sleeps. One loop polls until the expectation holds, an outcome
appears, or the timeout is reached. A fixed sleep is either too short when the
application is slow or wasted when it is not, and it cannot tell "still
loading" apart from "will never happen".

One thing the integration testing forced: a click returns as soon as the event
is dispatched, so on a server-rendered application the *old* page is still on
screen for a moment. An observation taken immediately reports the form that
was just submitted rather than the page it produced — which is how a run ends
up looking for search results on the search form. The surface layer now waits
for any navigation a click started before the next observation.

## What the run reports

Every result carries the rung that resolved each target. A target resolving on
a less-trusted candidate than the one recorded is **drift**: the surface moved
under the artifact. It is reported on runs that *succeed*, with
`needs_attention` false, because the entire value of noticing drift is
noticing it before it becomes a failure.

There is a test that breaks the recorded form field name — standing in for a
vendor release that renamed it — and asserts the run still completes via the
caption the surface layer inferred, and says so.

## The taxonomy, demonstrated

Every row below is a real condition the target produces on demand, verified in
`tests/replay/test_engine.py`. Nothing is simulated at the boundary under test.

| Condition | Classified as | `needs_attention` |
|---|---|---|
| member does not exist | `MEMBER_NOT_FOUND` | no |
| servicing restriction on file | `PERMISSION_DENIED` | no |
| already at the sub-account limit | `SUBACCOUNT_LIMIT_REACHED` | no |
| closed member record | `MEMBER_NOT_SERVICEABLE` | no |
| field validation rejected | `VALIDATION_REJECTED` | no |
| maintenance interstitial | recovered, run completes | no |
| transient server error | retried, run completes | no |
| session expiry mid-run | re-authenticated, run completes | no |
| unexpected confirmation step | acknowledged, run completes | no |
| member index down *(declared)* | `application_error` | **yes** |
| undeclared application error | `timeout` with expected/observed | **yes** |
| bad invocation parameter | `contract_violation`, nothing touched | **yes** |
| commit with no authorisation | `escalated` at step 11 | **yes** |
| commit under a read-only policy | `policy_refused`, nothing ran | **yes** |

Two of those deserve a note.

**A declared hard outcome still fails.** The member index being down is
written into the artifact, so replay recognises it — and reports it as a
failure anyway. Anticipating a failure does not make it acceptable; the caller
cannot be given an answer that does not exist.

**An escalation happens with ten steps already done.** The run pauses at the
commit, not at the start, so a human takes over on a prepared screen rather
than beginning again. That is the seam step 5 plugs into.

## Decisions taken here

**Ambiguity is not absence.** A candidate matching two controls has not
identified anything, and acting on the first of them would be a coin flip
against a banking screen. So the resolver moves to the next candidate and, if
none is specific enough, reports `target_ambiguous` rather than
`target_not_found` — they need different fixes.

**Provenance is part of a target's identity.** Both halves of a
`Date of Birth | 03/11/1974` panel answer to "Date of Birth". Only where the
name came from tells them apart, so matching honours the recorded
`name_source`. If the application later gains a real label the provenance
changes, the candidate stops matching, and the ladder falls through while
reporting drift — the run still works and says the surface moved.

**A recorded frame narrows, it does not require.** The same page is framed
when reached through the console and unframed by direct link. The frame
disambiguates when it is there and is ignored when it is not.

**Navigations are recorded as paths.** A step carrying an absolute URL would
keep navigating to the institution it was recorded at, however the tenant
overlay was written. Paths resolve against the surface origin, so changing one
field relocates the whole flow.

**Credentials never reach the engine.** The artifact holds none, and the
engine receives a *hook* it can call rather than any values. Re-authentication
still works, and with no hook supplied it becomes an escalation rather than
the engine inventing a way to sign in. Signing on lands on the application's
home page, so the recovery is not complete until the run is returned to the
page it was working through.

**Evidence is redacted, including the parts the application echoes back.** The
member id is declared PII and shows up in the page title, in headings and in
the URL. A test walks every file in an evidence directory and asserts the
value does not appear in any of them.

**A business outcome exits zero.** The CLI returns non-zero only for
`needs_attention`, because that is what a caller's automation branches on.

## Bugs this step found in earlier work

Integration surfaced three things unit tests could not, all now fixed and
pinned by tests:

- **The surface layer captured no `<div>` at all.** A legacy application
  states every outcome in a styled div — *"No member records match"*,
  *"SEC-403"*, *"Sub-account opened successfully"*. Every declared outcome,
  recovery trigger and success condition in both artifacts was undetectable.
- **Grid cells containing a link were dropped.** The scanner skipped any cell
  holding a control, and in a results grid the drill-down link lives inside
  the very cell carrying the row's key — making that column unaddressable.
- **Tenant overlays could not actually relocate a flow**, because navigate
  steps recorded absolute URLs.

## What this step deliberately does not do

- **No LLM recovery on failure.** §8 offers a bounded, policy-checked single
  step of model-driven recovery as a stretch goal. It is not built: a replay
  that can reason its way out of a surprise is no longer deterministic, and
  the determinism is the product.
- **No real escalation.** The engine raises `Escalated` and stops. Step 5
  turns that into an intervention request, a live session a human can drive,
  and a resumption.
- **No parallelism, no queue, no retry orchestration.** §7 is explicit that
  building scaling infrastructure is not rewarded.

## Carried into step 4

The engine is the thing discovery has to produce artifacts *for*. A recorded
run is only useful if every step it emits carries a locator ladder deep enough
to resolve, an expectation the replay can wait on, and declared outcomes for
the endings the model saw along the way.

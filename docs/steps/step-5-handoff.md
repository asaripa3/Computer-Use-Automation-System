# Step 5 — Escalation and handing over the live session

**Status:** complete · 33 handoff tests (443 total) · a recorded takeover in `evidence/replay-escalation/`

## Why this step exists

§3.6 asks for three things, and is explicit that a TODO does not count:

1. **Detect and route** — raise an intervention request carrying enough
   context to act on.
2. **Take control of the live session** — the human operates *the same
   session the automation was using, not a fresh one*, then hands it back.
3. **The seam this implies** — automation must pause, cede control and resume
   on the same session, and there must be a way to know who is in control.

The operator console may be mocked. The handoff mechanism and the
control-transfer model may not.

## Commands

```bash
make test-handoff      # the control-transfer model and live escalations
```

To drive a takeover yourself, with ShareBase running:

```bash
make handoff
```

That replays the flow that commits, stops on the review screen, and hands you
the browser window it was using. Work in that window, then answer `retry`,
`skip`, or `abandon` in the terminal.

## Files and what each one does

| File | Responsibility |
|------|----------------|
| `src/handoff/control.py` | Who holds the session, and `Supervised` — the wrapper that **enforces** it. |
| `src/handoff/intervention.py` | The request, the reply, and the plain-language instructions. |
| `src/handoff/journal.py` | What the person did while they held it. |
| `src/handoff/operator.py` | Where a request is routed: console, file/queue, scripted. |
| `src/handoff/coordinator.py` | Runs one handoff start to finish. |

| Test file | What it proves |
|-----------|----------------|
| `tests/handoff/test_control.py` | The ownership model, including that every acting method is refused during a handoff — without a browser. |
| `tests/handoff/test_escalation.py` | Real escalations mid-replay on a live browser session. |

## The same session, without co-browsing infrastructure

This is the requirement that looks hardest and turns out not to be, provided
you do not reach for the wrong answer first.

The live session is **already a browser window**. Handing it to a person means
not touching it. There is nothing to stream, mirror or proxy: run headed, stop
driving, and the operator works in the window that is already in front of
them — same browser, same cookies, still signed in, on the page the automation
had reached. When they are done, the automation picks up from wherever they
left it.

A test asserts the operator inherits a signed-in session and finds the Confirm
button waiting, with nothing re-authenticated and nothing re-navigated.

`FileOperator` is the same handoff routed somewhere else: the request is
written where a queue or a ticket would pick it up, and the answer is read
back. That is the seam a real deployment replaces, and it is how the handoff
runs unattended.

## Ownership is enforced, not agreed

A field recording who is in control is easy. The useful part is that the
answer binds.

`Supervised` wraps any surface and refuses every acting method while the human
holds it. Without that, ownership is a note saying the automation ought not to
click, in a process perfectly capable of clicking. With it, an automation that
tries to act during a handoff fails loudly rather than racing somebody halfway
through typing an amount into a banking screen. Five parametrised tests cover
every acting method, and one asserts the refusal happens *before* the
underlying surface is touched.

**Perceiving is deliberately not gated.** While the human drives, the system
still needs to watch — that is how it records what they did, and observing
changes nothing. Ownership governs acting, not looking.

The states are few:

```
automation ──request──> awaiting ──take──> human ──release──> automation
                            │                 │
                            └────abandon──────┴──> abandoned
```

**Control leaves the automation when the request is raised, not when someone
picks it up.** Between those two moments nobody is driving. An automation that
carried on "until an operator arrives" would do the very thing it just asked
permission for.

## Decisions taken here

**The run escalates late, not early.** The sub-account flow completes ten
steps before stopping, so the operator arrives at a review screen with the
request already filled in rather than being handed a blank form and the
original instruction. Refusing at step one would be safer to implement and
far worse to receive.

**The request says what to do, not just what happened.** §3.6 asks for
context; an operator with a queue of these needs instructions. "Step 11 needs
authorisation" makes them reconstruct the goal first. "The sub-account form is
filled in and waiting on the review screen; confirm it if the member requested
this, otherwise abandon" can be acted on immediately. The system knows which
it is, so it says so.

**The reply is three answers, not "done".** A human does not simply finish —
what they did changes what happens next:

| answer | meaning |
|---|---|
| `retry` | "I put it right; try that step again." |
| `skip` | "I did that step myself; carry on from the next one." |
| `abandon` | "This should not continue." |

**`skip` is a claim, not a fact.** The automation does not perform the step —
but it still evaluates the step's checkpoint. An operator who believes they
completed something they did not gets a failed checkpoint rather than a run
that carries on from a state nobody verified. There is a test for exactly
that: an operator answers `skip` without doing the work, and the run fails at
step 11.

**An unanswered request is abandoned, never waved through.** A run holding a
banking session open indefinitely is its own incident, and the alternative —
proceeding with an irreversible step nobody approved — is worse. The same
reasoning applies to `ConsoleOperator` reading EOF: silence is not consent.

**With no operator configured, the run fails exactly as before.** An
escalation path that silently became a no-op when unconfigured would be worse
than not having one.

**A run a person approved is not an incident.** `needs_attention` stays false
on a successful handoff. The result carries the whole transfer — what was
asked, who decided, what they said, and what they were observed doing — so it
is auditable without being alarming.

## Recording what the human did

Harder than it looks: the person is driving a real browser directly, so there
are no tool calls to log. What is available is observation, which the
ownership model leaves ungated on purpose.

So the journal samples the session while they work and records what *changed*
— pages visited, fields whose values moved — plus whatever they said they did.
A sampled trail is not a keystroke log and is not meant to be. The question it
must answer afterwards is "what did the operator do to this member's record",
and the pages they moved through and the values they left behind answer it.
It also survives them doing something the automation has no concept of, which
a replay of intercepted events would not.

Everything written passes through the redactor. In the committed evidence the
operator's navigation reads
`/console/member/<pii:26a111>/subaccount -> .../subaccount/commit` — the trail
is legible, the member is not identified.

## Two escalation triggers

| Trigger | Condition |
|---|---|
| `authorization_required` | An irreversible step, and no authorisation for this invocation |
| `target_unresolvable` | A recorded control is not on the page — the case a person can resolve and the automation cannot |

On `retry` after an unresolvable target, the step is attempted again against a
freshly observed page, since the operator has presumably put the session
somewhere it can succeed.

## What this step deliberately does not do

- **No real-time co-browsing console.** Explicitly out of scope, and the
  headed-browser handoff makes it unnecessary for a single operator at the
  same machine. A remote operator needs streaming, which is where a CDP-based
  console would go, behind the `Operator` protocol.
- **No keystroke-level capture of the human.** It would need CDP input
  interception and would still miss anything outside the page.
- **No queue, no assignment, no SLA.** `FileOperator` is the seam those plug
  into; building them is the scaling infrastructure §7 says is not rewarded.
- **No resumption across process restarts.** The handoff is within one run.
  Durable suspend/resume needs the session itself to be recoverable, which is
  a genuinely different problem and is named in the write-up as such.

## The worst bug in the project, found by driving it by hand

An interactive run went like this: the operator pressed Confirm, saw the
confirmation, clicked "Return to Member", and *then* answered `skip`. The run
reported **failure**.

The account had been created. A caller reading that result would retry, and
the retry would open a second one.

The design intent was right -- a `skip` is a claim, verified rather than
trusted -- but the verification was screen-based and the screen that proved it
had gone. The engine had two states where it needed three:

| | meaning | correct next action |
|---|---|---|
| verified | the effect happened | carry on |
| failed | the effect did not happen | retry |
| **unverified** | **nobody can tell** | **check before retrying** |

For an irreversible step those last two demand opposite responses, so
conflating them is the same category of mistake as conflating a business
outcome with a failure -- just with worse consequences.

Three changes came out of it:

**The proof is watched for throughout the handoff, not looked for at the
end.** A confirmation screen is often the only evidence an irreversible step
took effect *and* the only place its declared outputs appear, and operators
move on from it. The handoff now keeps the observation at which each watched
condition first held, and both verification and extraction use it. The
scenario above now succeeds, with the outputs read from the screen they were
on.

**`effect_unverified` is its own failure kind**, carrying
`safe_to_retry: false` and an explicit instruction not to re-run without
checking. It applies only to an irreversible step a person reported doing that
was never observed succeeding -- everything else stays retryable.

**The instructions say to answer while the resulting screen is still up**,
which is the thing no one had told the operator.

## Three more bugs the interactive run found

Driving `make handoff` by hand surfaced what the headless tests did not.

**A checkpoint satisfied by the page being left.** Step 5 clicked a link
reading "Open Sub-Account" and then waited for the text "Open Sub-Account" --
which is on the page it was leaving. The wait returned before the click had
gone anywhere, so the next step acted on a page already navigating away and
crashed on a detached element. Fixed by naming something only the destination
has, and pinned by a test that refuses any checkpoint echoing its own step's
target.

**A driver exception escaping `replay()`.** The detached element surfaced as a
raw Playwright traceback. A caller that gets an exception out of the engine
has lost the entire result contract, so the surface layer now raises
`StaleElement` and `ActionFailed` -- driver-neutral, defined in
`surface/model.py` -- and the engine handles both. Staleness is recovered by
looking again, once; anything else becomes a structured failure. This also
tightened the §3.7 seam: the engine no longer imports anything from a browser
library, so a desktop surface raising the same two errors needs no change
above it.

**Every selection by value cost thirty seconds.** `select` tried a label match
first and let it fail, which burns the driver's full timeout before the value
match is attempted. Reading the options and deciding first made the
sub-account flow go from 32 seconds to 1.3, and the whole test suite from 3:38
to 2:03. It had been there since step 1, invisible because nothing looked at
per-step timings until the run printed them.

A fourth came from the evidence check: a failure record contained a member's
name, which schema-driven redaction could not catch because no capability had
ever declared it -- it was simply on screen. Evidence now records the *shape*
of a page rather than its content: every control name, column header and ref
is kept, and values (and names that are merely whatever a block of text
happened to say) are replaced by their length. A locator failure is debugged
from structure, and structure carries no member data.

The general lesson matches step 4's: the guards that pay are the ones that
check a thing at the moment it is claimed. A checkpoint is claimed when it is
written, so that is where it is verified. An effect is claimed when an
operator answers, so that is where it is checked -- and when it cannot be,
the honest answer is "unknown", not "failed".

## Carried into step 6

Everything §3 asks for now exists. What remains is `REPORT.md` — the seven
mandated headings — and turning the design work already done for §3.7 into
the write-up it was always meant to become.

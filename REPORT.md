# Design write-up

## Architecture

A model run is a compiler pass, not a way of getting work done. The model
drives a legacy application once to learn a flow, that run is compiled into a
typed versioned capability, and production invokes it with no model in the
decision loop.

Seven modules under `src/`, each depending only on the ones above it:

| module | owns |
|---|---|
| `sharebase/` | the target application, a deliberately awkward stand-in |
| `surface/` | observing and acting on a surface, whatever it is |
| `contract/` | what a recorded flow is, and what a run returns |
| `policy/` | allowlist, risk gating, redaction |
| `replay/` | the production execution path |
| `explore/` | the discovery loop |
| `handoff/` | giving a person the live session |

Two choices did most of the work. **Replay was built before discovery**,
against hand written capabilities, so the schema was shaped by what a caller
and reviewer need rather than by whatever a model emitted; when the real model
run came, four of its recordings were subtly wrong and each was caught by a
contract that already existed. And **nothing above `surface/` knows what a
browser is**: the engine imports no driver, and when a Playwright exception
did leak out it was fixed by giving the surface its own error vocabulary in
`surface/model.py` rather than by catching it upstream.

It is a single process with command line entry points and no service
infrastructure: abstractions that could scale are useful, building them now is
not.

## Artifact schema

A capability is a contract an agent can call and a human can approve, so the
schema is shaped by who reads it. See `contract/capability.py` and
`contract/locator.py`.

**Targets are an ordered ladder, not a selector.** Each candidate is tagged
with how far the application vouches for it: `asserted` for a form field name
it states about itself, `derived` for a name the surface layer inferred from a
neighbouring cell, `positional` for a place in the document. Prefer what the
application asserts over what we inferred. Element ids are recorded as evidence
and never targeted, since this target regenerates them every render.

**Declared outcomes are part of the contract.** A flow does not only succeed
or crash, so the artifact lists the endings it knows about, each with a way to
detect it and whether it is a business answer or a hard failure. A capability
declaring none is refused, since every flow can fail to find its subject.

Also deliberate: recoveries carry bounded attempt counts, since an unbounded
retry is not a recovery; values are literals or typed input references with no
expression language, since a reviewer must be able to see what gets typed into
a banking screen; and at most one irreversible step is allowed, since if a
second fails the first cannot be undone.

`contract/result.py` separates `success`, `business_outcome` and `failure`
into constructor validated fields, so a caller never checks for a failure on a
success. `needs_attention` is true only for a failure; derived from a status
code, "no such member" would page somebody.

## Determinism & error handling

Resolution stops at the first candidate matching exactly one control, since a
candidate matching two has identified nothing. Finding nothing and finding too
many are separate failure kinds, because they need different fixes.

Replay never sleeps. One loop polls until the expectation holds, an outcome
appears, or the step times out, because a fixed sleep cannot tell "still
loading" from "will never happen".

The ordering inside a step carries the most weight: recoveries are absorbed
first, declared outcomes checked second, and only then is the checkpoint
judged. With the checkpoint first, "No member records match" would be reported
as a failed checkpoint, an incident, when it is the correct answer.

Each condition below is produced by the target on demand, and
`tests/replay/test_engine.py` verifies its classification:

| condition | result |
|---|---|
| no such member, permission denied, limit reached, validation rejected | business outcome, no attention needed |
| interstitial, transient error, session expiry, extra confirmation | recovered, run completes |
| declared outage, undeclared error, bad parameter, policy refusal | failure, needs attention |

A run resolving on a lower rung than recorded reports drift while still
succeeding, which is how a surface change is noticed before it breaks.

One state was missing until an interactive run exposed it. An operator who
completes an irreversible step then navigates away leaves the automation unable
to confirm it, and a plain failure invites a retry that does the thing twice.
That case is `effect_unverified`, carrying `safe_to_retry: false`.

## Heterogeneity & multi-tenant

The seam is `surface/model.py`. A `Node` carries role, name, value, bounds,
enabled state, frame path and table coordinates, all of which map onto Windows
UI Automation and macOS Accessibility. Web specific detail lives in `WebHints`,
opaque to the core, and `contract/derive.py` is the only module that reads
it.

A desktop surface implements the five surface methods, populates its own
hints, and adds a sibling pair in `derive.py` proposing an asserted locator
from a UI Automation id instead of an HTML field name. Nothing in the schema,
the engine or any artifact changes. A legacy web application needs nothing new:
frames, volatile ids and unlabelled fields are what the target already is.

For tenants, one vendor level capability carries a small version pinned
overlay. The shipped Riverbend example changes the host and overrides one
renamed control; its caption differences cost nothing, because the asserted
field name sits above the caption on every ladder. Navigation steps record
paths, so changing the host relocates the whole flow. An overlay may not
reorder steps: a tenant needing a different flow needs its own capability.

## Escalation & handoff

Two triggers raise an intervention: an irreversible step with no matching
authorization, and a recorded control that is not on the page. The request
carries the capability, goal, redacted URL and page structure, and plain
instructions, since an operator needs to act, not reconstruct intent.

The live session is already a browser window, so handing it over means not
touching it. Run headed, stop driving, and the operator works in that window
with the same cookies and signed in session. Ownership is enforced, not
recorded: `handoff/control.py` refuses every acting method while the human
holds the session, though observation stays available so the system records
what they did. Control leaves the automation when the request is raised, not
when someone picks it up, since the gap between is when it would do the thing
it just asked permission for.

The operator answers `retry`, `skip` or `abandon`. A skip is a claim, not a
fact, so the checkpoint is still evaluated. Because a confirmation screen is
often the only proof and the only place declared outputs appear, the handoff
watches for it throughout and keeps the observation where it held.

## Safety

The allowlist defaults to deny and is checked twice: before a run against the
declared origins, and on every navigation against the actual URL, since a
declaration is a promise and a redirect can break it. The fault console sits
outside it, since automation able to disarm the conditions it is tested against
would make every robustness result meaningless.

Irreversible actions escalate rather than block or auto-confirm: blocking
would make half of back office automation unbuildable, auto-confirming reduces
the guardrail to a boolean somebody sets once. Authorization is pinned to an
exact capability version, and a draft never commits, which is why a recorded
capability is born a draft.

Redaction is both schema and value driven: sensitivity comes from the
contract, and a run's concrete values are scrubbed from free text, since a
member id typed into a search box comes back in the page title and every error
message. Credentials never enter an artifact or the engine, which receives a
sign-in hook rather than values.

The limits are worth stating. Schema driven redaction only knows what a
capability declared, which is why evidence records page structure with values
replaced by their length. The allowlist is route level, so nothing stops a
permitted capability reading records it should not. Risk labels are declared
then reviewed, not detected. And none of it constrains a human operator, who
can do anything their credentials allow.

## Cuts

Left out deliberately: model driven recovery during replay, since a replay
that reasons its way out of a surprise is no longer deterministic; a remote
co-browsing console, since the headed handoff already exercises the control
seam; keystroke level capture, which needs input interception and still misses
anything off-page; and queues, services, desktop support and a published JSON
Schema.

Kept minimal but real: the operator console is a terminal prompt and a file
drop, the overlay ships one example, discovery records one capability per run.

Next I would expose the catalog as typed agent callable tools, the most direct
proof that an agent can invoke these; then make draft to approved promotion
evidence based by replaying a capability repeatedly and scoring its stability;
then durable suspend and resume, and a second surface, the cheapest honest test
of whether this seam got anything wrong.

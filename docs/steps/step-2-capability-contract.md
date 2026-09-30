# Step 2 — The capability contract and the guardrails

**Status:** complete · 137 contract and policy tests (254 total) · still no model

## Why this step exists

§3.2 says the artifact schema is *a focal point of the evaluation*, and §3.4
asks for an explicit guardrail model. This step builds both, and deliberately
builds them **before** the replay engine and before the discovery loop.

That ordering is the whole reason for the waterfall. Replay in step 3 is built
against artifacts authored here by hand, which forces the schema to be right
while it is still cheap to change. If the schema were derived from whatever a
model happened to emit, it would be shaped by the recorder instead of by what
a caller and a reviewer actually need.

## Commands

```bash
make test-contract                            # the schema tests
make test-policy                              # allowlist, risk, redaction
make review                                   # list the capability catalog
make review CAP=member.savings_balance@1.0.0  # review one in full
make capabilities                             # re-author the seed artifacts
```

`make review` is the human-facing half of §3.2. The JSON serves the calling
agent; this serves the reviewer, and it leads with the three questions that
actually decide approval: what will this commit, what data does it touch, and
which of its targets is weakest.

## Files and what each one does

### `src/contract/` — what a recorded flow *is*

| File | Responsibility |
|------|----------------|
| `locator.py` | The targeting ladder: ordered candidates, each tagged with how much the application vouches for it. The intellectual core of the step. |
| `capability.py` | `Capability`, `Step`, `InputSpec`, `OutputSpec`, `OutcomeSpec`, `RecoverySpec`, `Condition`, and the cross-field validation. |
| `values.py` | A step's value: a literal, or a reference to a declared input. No expression language, on purpose. |
| `derive.py` | The translation seam — the only place permitted to read a surface's own hints. |
| `overlay.py` | Tenant overlays: one artifact, many institutions. |
| `io.py` | JSON on disk, plus the capability catalog. |
| `cli.py` | `make review`. |
| `binding.py` | Validating invocation parameters, and giving extracted values their declared shape. |
| `result.py` | The **replay contract** — what a run hands back to a caller. |
| `errors.py` | Validation failures that name where in the artifact they happened. |

### `src/policy/` — the guardrails

| File | Responsibility |
|------|----------------|
| `allowlist.py` | Default-deny origins, routes and action types. Checked before a run *and* at every navigation. |
| `risk.py` | What to do about a step that cannot be undone. |
| `redaction.py` | Keeping regulated data out of artifacts and logs. |

### Artifacts

| Path | What it is |
|------|-----------|
| `capabilities/member.savings_balance@1.0.0.capability.json` | Read-only, 4 steps, 2 declared outcomes. |
| `capabilities/member.open_subaccount@1.0.0.capability.json` | 11 steps, 7 declared outcomes, ends in one irreversible commit. |
| `capabilities/overlays/…riverbend.overlay.json` | A second institution on the same product, branded differently and on an older release. |
| `tools/author_capabilities.py` | Authors the seed artifacts **by observing the running application**, so field names and column headers are facts read off the surface rather than values typed from memory. |

## The locator ladder

A locator is not one selector. It is an ordered list of candidates, each
tagged with how much the application itself vouches for it:

| Tier | Meaning | Example |
|---|---|---|
| `asserted` | The application states this about itself | the form field name `txtMemberNo` |
| `derived` | The surface layer inferred it | `textbox named "Member Number"`, from the neighbouring cell |
| `positional` | Neither; a position in the document | `tr[2]/td[1]/…/input[1]` |

**The ordering rule is: prefer what the application asserts over what we
inferred.** A form field name is a fact — the application's own request
handling depends on it. An accessible name reconstructed from a table cell is
a good guess, and on this target it is usually the only name available, but it
is still a guess.

Three things follow, and all three are the point:

**Robustness is ranked, not assumed.** The recorder does not have to predict
which strategy will survive. It records all of them in trust order.

**The ladder is a drift detector.** If the first candidate stops resolving and
a lower one takes over, the surface moved. Replay reports which rung it landed
on, so drift shows up as a signal on a *successful* run rather than waiting to
become a failure. That is §3.7's drift question answered as a by-product of
how targeting already works.

**Tenant fragility and version fragility are different axes.** An asserted
identifier is stable when a tenant rebrands but can move between vendor
releases. A role-and-name is stable across releases but is exactly what a
tenant rebrands. Carrying both means one artifact survives either kind of
change, and the overlay only has to handle the case where both fail at once.

The tiers are enforced, not advisory: an `asserted_id` may not claim to be
`derived`, and only a `structural_path` may be `positional`. Letting a
recorder label these freely would silently reorder the ladder.

## Decisions taken here

**Declared outcomes are part of the contract.** A flow does not only succeed
or crash. The artifact enumerates the endings it knows about — each with a way
to detect it and whether it is a *business* answer or a *hard* failure — so
replay classifies rather than guesses. The schema refuses a capability that
declares none, because every flow against a real system can fail to find its
subject. Conflating these is the mistake the brief names, so the schema is
where it gets prevented.

**Recoveries are declared, not improvised.** A known interstitial, a transient
error, an expired session: each is written down with how to detect it, what to
do, and a bounded attempt count. Replay absorbing a condition it was told
about is deterministic; replay reasoning its way out of a surprise is not. An
unbounded retry is rejected at load time — it is not a recovery.

**Outputs carry their own locator.** Extraction is a declaration, not a step:
*this* output comes from *that* place. It keeps the step list about acting and
makes the returned shape reviewable on its own.

**No expression language in values.** A value is a literal or an input
reference, and nothing else. An artifact is meant to be approved by a human
before it runs unattended against member records, and every syntax added here
is something a reviewer has to evaluate in their head to know what will be
typed into a banking screen.

**At most one irreversible step per capability.** If a second commit fails
there is no way to undo the first, and the caller is left with a partial
change it was never told about. Flows that genuinely need that belong behind a
transaction the application provides.

**Irreversible steps escalate rather than block or auto-confirm.** Blocking
outright would make half the useful capabilities in a back-office system
unbuildable — opening an account *is* the work. Auto-confirming on a flag
reduces the guardrail to a boolean someone sets once and never revisits. So an
irreversible step needs an authorisation naming who granted it and for which
capability, and in its absence the run pauses and asks a human through the same
escalation path as any other stuck state. The dangerous case stays on a route
that already carries context, evidence and a record of who decided what.

Two constraints fall out and are enforced: an authorisation is pinned to
`id@version`, because a capability that changed since approval has not been
approved; and a `draft` capability never commits, whatever the flags say.

**Redaction is schema-driven *and* value-driven.** Every input and output
declares its sensitivity, so a value is redacted because the contract says what
it is — not because a field name matched a pattern. Separately, the concrete
values handed to a run are registered and scrubbed from free text, because a
member id typed into a search box comes back in the page title, in a heading
and in an error message. Redacting the parameter and then logging the page that
echoes it would be theatre.

PII becomes a stable token (`<pii:a3f2c1>`) so a run stays debuggable and two
occurrences of the same member are still recognisable as the same member.
Secrets become `<redacted>` with no token at all — a hash of a password is
still an oracle for it. A capability may not declare a secret *output*: there
is no safe way to return one.

**The allowlist is checked twice.** Once before a run against the capability's
declared origins, and again at every navigation against the actual URL. A
declaration is a promise, and a redirect or an injected link can break it. §3.4
says the agent must not *act* outside the allowlist, not that it must not
intend to. The default policy permits nothing, so forgetting to pass one fails
closed.

## The replay contract

§7 names "the artifact schema **and replay contract**" as the two central
pieces, so the result shape is defined here, for the caller, rather than
falling out of whatever the engine finds convenient to return.

It turns on one distinction:

| status | meaning | who acts |
|---|---|---|
| `success` | the flow completed and the declared outputs were read | the caller |
| `business_outcome` | a known, legitimate ending that is not success | the caller |
| `failure` | something is broken | a person |

These are separate fields, not one status string with a code bolted on, and
the invariants are enforced in the constructor: a `business_outcome` must
carry its outcome, a `failure` must carry its failure, and a `success` may
carry neither. A caller must never have to defensively check for a failure on
a successful result.

The property that matters most is `needs_attention`, which is **true only for
`failure`**. If it were computed from a status code, "no such member" would
page somebody at three in the morning.

Every result also carries a step-by-step report including **which rung of the
locator ladder resolved each target**, and a `drift` list naming any target
that resolved lower than recorded. Drift is reported on runs that *succeeded*
— it is an early warning that the surface moved, not an error.

A `Failure` requires both `expected` and `observed`. "Step 10 expected the
review page and observed a field validation banner" is debuggable; "replay
failed" is not.

## Waiting

§3.3 asks for a sound locator, **wait** and checkpoint strategy. The rule
here: replay never sleeps. It waits *for a declared condition* with a bounded
timeout.

A fixed sleep is either too short when the application is slow or wasted when
it is not, and it cannot tell "still loading" apart from "will never happen".
So a step's `expect` condition is awaited up to `timeout_ms`, defaulting to
the capability's `default_timeout_ms` (10s). An unbounded wait is rejected at
load time — it is not a wait strategy, it is a hang.

The commit step in the sub-account capability carries an explicit 30s: posting
a share goes to the core, and the default is generous for a page load but not
for a transaction.

## Binding and shape

An artifact declaring `type: "money"` while returning `"4,821.37"` as a string
makes the type decorative, and nothing stopping a caller passing
`"'; DROP TABLE"` into a banking search box makes the pattern decorative too.
`binding.py` closes both, asymmetrically:

**Inbound is strict.** Parameters are checked against declared type, pattern
and choices *before a single step runs*, and every problem is reported at
once. Failing before touching the application is the only point at which a bad
parameter is free.

**Outbound is forgiving, then exact.** A value read off a legacy screen
arrives as display text — `4,821.37` with grouping and no currency symbol,
`03/11/1974` in the local convention. Coercion accepts what the surface
actually renders and returns a `Decimal` or a `date`, so the calling agent is
not left parsing bank screens it never saw. A missing *optional* output is
`None`; a missing *required* one is an error, because an output silently
coming back empty is how a caller acts on a balance it never read.

Rendering back the other way happens in one place, which is how a deposit
supplied as `150` is typed as `150.00` and clears the application's
minimum-deposit check.

## Multi-tenant reuse

The artifact stays at the vendor level; a tenant contributes an overlay of
differences resolved at load time. Three kinds cover nearly everything: a
different address, different wording, and — rarely — a genuinely different
control.

The shipped example is the interesting part. Riverbend runs an older release
that brands member numbers as *account* numbers and named the lookup button
differently. The caption differences **cost nothing**: because the asserted
field name sits above the caption on every ladder, replay never reaches the
rung that changed. Only the one genuinely renamed control needed an explicit
override.

An overlay may not add, remove or reorder steps. A tenant that needs a
different *flow* does not have an overlay, it has a different capability, and
letting the two blur is how a shared artifact quietly becomes hundreds of
incompatible ones. An overlay is also pinned to a capability version, so one
written against an older artifact must be reviewed before it is carried
forward.

## What this step deliberately does not do

- **No resolution.** Turning a `Locator` into a live node — trying the ladder,
  deciding what to do when a candidate matches nothing or matches twice — is
  replay's job in step 3.
- **No evaluation of conditions.** The schema defines what a checkpoint *is*;
  checking one against an observation belongs with the engine that acts on the
  answer.
- **No formal JSON Schema document.** The dataclasses are the source of truth
  and produce better errors than a schema validator would. A published
  `.schema.json` would help an external consumer and is worth adding later;
  it is not what makes the artifact reviewable today.

## Carried into step 3

Replay needs to: bind inputs before touching the application; walk the ladder
and report which rung resolved; wait for each step's declared condition within
its timeout; match declared outcomes **before** declaring failure; apply
recoveries within their attempt budgets; call `policy.risk.decide` before the
irreversible step and escalate rather than proceed when the answer is not
`proceed`; coerce the outputs; and return a `ReplayResult`.

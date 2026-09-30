# Step 0 — ShareBase, the target application

**Status:** complete · 51 tests passing · nothing automates it yet

## Why this step exists

Every later step is measured against this application, so it is built and
verified first. If the target is not genuinely awkward, the locator strategy,
the error taxonomy and the replay engine are all being tested against a problem
easier than the one they claim to solve.

ShareBase is a fictional credit-union member servicing console. It stands in for
the back-office systems in the brief. It is built locally rather than borrowed
from a public demo site for three reasons: runtime conditions can be armed on
demand instead of waited for, no third party's terms or rate limits are
involved, and the surface can be made awkward in the precise ways that matter
rather than the ways a demo site happens to be.

## Commands

```bash
make install          # install dependencies
make run              # start ShareBase on http://127.0.0.1:8080
make test             # run all 51 tests
```

Sign on with the operator ID and password from `.env` — `svc_agent` /
`Demo-Pass-1234` by default.

Run the three test files separately to see what each is asserting:

```bash
make test-surface     # the hostile properties of the surface
make test-flow        # the two flows and every business outcome
make test-faults      # every class of runtime condition
```

With ShareBase running, drive the fault board from a second terminal:

```bash
make fault F=hard_error           # arm a hard failure
make fault F=interstitial_notice  # arm a recoverable interstitial
make reset                        # disarm everything, reseed member data
```

Or use the console at <http://127.0.0.1:8080/admin/faults>.

## Files and what each one does

### `src/sharebase/`

| File | Responsibility |
|------|----------------|
| `app.py` | The Flask application: every route, the sub-account wizard, the servicing gate that turns each refusal into an identifiable business outcome, and the `before_request` hook that injects armed runtime conditions. |
| `data.py` | The synthetic member store. Deterministic seed, rebuilt per process. Each of the eight members exists to make one branch of the flow reachable. |
| `faults.py` | The fault switchboard. Declares nine faults, each tagged with the outcome class it provokes, and tracks firing budgets so transient conditions genuinely self-clear. |
| `session.py` | Sign-on, credential checking and the idle-timeout session. Expiry is reachable both by waiting out the TTL and by arming a fault. |
| `legacy.py` | The helpers that keep the surface hostile on purpose: volatile control ids, per-render viewstate tokens, and currency formatted without a symbol. |
| `__main__.py` | `python -m sharebase`. Threaded, so an armed delay fault does not stall the console used to disarm it. |
| `templates/` | Sixteen server-rendered pages: the frameset shell, sign-on, search, results, member detail, the four-page sub-account wizard, the outcome and error pages, and the fault console. |
| `static/sharebase.css` | Period-correct presentation. No utility classes and no semantic hooks, because offering either would hand the automation an anchor the real systems do not have. |

### `tests/`

| File | What it proves |
|------|----------------|
| `conftest.py` | Fixtures. Every test starts from a freshly seeded store with no faults armed, so no test can leak state into the next. |
| `helpers.py` | Shared constants and a valid sub-account submission. |
| `sharebase/test_surface.py` | Pins the hostile properties. If one of these starts failing, the target has quietly become easier than the systems it stands for. |
| `sharebase/test_flow.py` | Both flows end to end, plus every business outcome each can end in. |
| `sharebase/test_faults.py` | That each class of runtime condition is reachable on demand. This file is the contract between the target and the replay engine built in step 3. |

## What the surface does to you

These are the properties that invalidate a locator strategy which would look
fine against a modern application. Each is asserted in `test_surface.py`.

- **The authenticated console is a frameset.** Reading the accessibility tree
  of `/console` returns only the `<noframes>` fallback. The frames are
  same-origin and fully reachable, but perception has to enumerate them
  explicitly rather than assuming one document.
- **Control ids are regenerated on every render.** Any locator built on an `id`
  is dead on the next page load.
- **There are no test ids and essentially no ARIA.** There is not one
  `<label for>` in the application; fields are tied to their labels only by
  table-cell adjacency.
- **Nested-table layout.** In the accessibility tree a data grid collapses into
  a flat run of untyped nodes — on the member page, `"Current Balance"` and
  `"4,821.37"` are siblings with no association between them. The enclosing
  table node is exposed, so the column-to-cell mapping has to be reconstructed
  by chunking the cell run by header count.
- **Some controls are not buttons.** Grid rows are `<td>` elements carrying
  `onclick`; some actions are `javascript:` anchors.
- **Balances carry no currency symbol.** A value has to be identified from its
  label and column position, not from a convenient `$`.

## The decision that matters most in this step

Business outcomes return **HTTP 200 with an identifiable outcome code**, not an
error status.

"No such member" and "permission denied" are answers the calling agent has to
act on, not transport failures. A caller that reads outcomes off the status
code cannot tell a denial apart from a crashed application — and the brief
names conflating those two the most common design mistake in this problem. The
target encodes the distinction at the source so the replay engine can be held
to it.

Hard failures do return 5xx, and render the legacy stack-trace page with a
reference number, because those genuinely are transport failures.

## The two flows

| Goal | Kind | Ends at |
|------|------|---------|
| Look up a member and read their current savings balance | read-only | the member detail grid |
| Open a sub-account for a member and reach confirmation | state-changing | a confirmation number |

The sub-account wizard holds its pending request in server-side session state
rather than in the page. Replaying the commit out of order therefore fails
loudly with `SEQ-409` instead of silently opening an account from stale values.

## Members

| Member | Why it exists |
|--------|---------------|
| 12345 | happy path — active, holds a regular savings account |
| 12346 | checking only, so a savings balance is legitimately absent |
| 12347 | servicing restriction on file — readable, but account opening is denied |
| 12348 | already at the four sub-account limit |
| 12349 | dormant savings — a balance exists but the account is not active |
| 22001, 22002 | share a surname, so a name search returns two rows |
| 33100 | closed member — found, but not serviceable |

All records are invented. They are *shaped* like regulated member data so that
redaction, in step 2, has something realistic to act on.

## Faults

| Class | Meaning | Faults |
|-------|---------|--------|
| **business** | a legitimate answer the caller must be told about | `permission_denied`, `spurious_validation` |
| **recoverable** | a condition automation can deliberately absorb | `session_expired`, `interstitial_notice`, `slow_response`, `transient_error`, `unexpected_confirm` |
| **hard** | stop, and surface enough detail to debug | `search_unavailable`, `hard_error` |

Faults with a finite firing budget disarm themselves once spent, so "retry and
it works" is real behaviour rather than a mock: `transient_error` fails twice,
then serves normally.

Business outcomes that need no fault at all are reachable from the data alone —
searching for member 99999 returns an empty result set, and members 12347,
12348 and 33100 each end the sub-account flow in a different refusal.

## Decisions taken here

- **Python and Flask, with no frontend build.** The target has to be a
  server-rendered frameset application with volatile ids and no semantic
  markup. A component framework would produce the opposite surface, and would
  add a second toolchain to a setup path the brief grades.
- **A purpose-built target rather than a public demo site.** Arming a session
  expiry or a transient 500 on demand is the only way to demonstrate the error
  taxonomy repeatably, and it keeps the project clear of anyone's terms of use.
- **Faults are HTTP-addressable, not just a UI.** Step 3's replay tests need to
  arm a condition, drive the flow and assert how it was classified, without a
  human in the loop.

## What this step deliberately does not do

No perception, no artifact, no replay, no model. ShareBase has no knowledge that
it will be automated, which is the point — it is a target, not a participant.

## Carried into step 1

Two constraints measured against the running application rather than assumed:

1. Perception must walk the frame tree; a top-level accessibility read yields
   nothing usable.
2. Table cells arrive unassociated, so the perception layer needs a labelling
   pass that rebuilds the header-to-cell mapping positionally.

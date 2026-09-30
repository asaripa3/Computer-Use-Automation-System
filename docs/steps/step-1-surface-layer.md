# Step 1 — The surface layer, and the seam it defines

**Status:** complete · 63 surface-layer tests (114 total) · no model involved yet

## Why this step exists

Section 3.7 of the brief asks for the seam between *how we perceive and act on
a surface* and *the recorded flow*. This step is that seam. Everything built
after it — the capability artifact, the replay engine, the discovery loop — is
written against `Observation` and `Node` and may not mention a DOM, a selector
or a browser. That restriction is what lets a second surface implementation be
substituted later without the recorded flow changing shape.

## Commands

```bash
make test-surface                        # the browser-driven tests
make test-fast                              # everything except the browser tests
make run                                    # in one terminal
make observe URL=/console/member/12345      # in another: what the surface layer sees
make observe URL=/console/member/12345/subaccount
```

`make observe` is the quickest way to answer the question that matters before
any locator is written: what does this layer believe is on the page, and which
of those names did it have to infer?

## Files and what each one does

| File | Responsibility |
|------|----------------|
| `src/surface/model.py` | **The seam.** `Node`, `Observation`, `Bounds`, `TableCell` and the `Surface` protocol, plus the query API (`find`, `field`, `cell`, `value_cell`, `rows`). Nothing here knows what a browser is. |
| `src/surface/scan.js` | Raw in-page capture, run per frame. Gathers facts and decides nothing. |
| `src/surface/naming.py` | The judgement: role and name derivation. Pure functions over dictionaries, so the rules are testable without a browser. |
| `src/surface/browser.py` | The Playwright implementation of `Surface` — frame traversal, element handles, and the act methods. |
| `src/surface/view.py` | Observation to text for a model, with grids rendered as grids. |
| `src/surface/cli.py` | `make observe`. |

| Test file | What it proves |
|-----------|----------------|
| `tests/surface/test_naming.py` | The naming and role rules, exhaustively, in 0.06s with no browser. |
| `tests/surface/test_observation.py` | The query API's behaviour at the edges: no match, several matches, absent key. |
| `tests/surface/test_browser.py` | That the capture feeding those rules is correct, and that the application can be driven end to end through perceived names alone. |

## The two problems this layer exists to solve

Both were measured against the running application in step 0, not assumed.

### Frames

Reading the accessibility tree of the console's top document returns its
`<noframes>` fallback and nothing else. The entire application lives in child
frames. The surface layer therefore enumerates the frame tree on every observation and
tags each node with the path it was found at, so a caller can say
"the Member Search link in `navFrame`" and mean it.

A test asserts the top document is empty *and* that the observation is not, so
the constraint cannot quietly stop being true.

### Collapsed grids

In the accessibility tree a data grid becomes a flat run of untyped nodes: the
header `"Current Balance"` and the value `"4,821.37"` are siblings with nothing
joining them. Restoring the coordinates and naming each cell for its column
turns that back into something addressable:

```python
observation.cell(
    column="Current Balance",
    where_column="Account No",
    where_value="0001234501",
).text                                      # -> "4,821.37"
```

That expression survives the member holding a different number of accounts.
"The sixth node after the word Balance" does not, and that is the difference
between a capability that works next month and one that does not.

## Decisions taken here

**Names carry their provenance.** Every node records *how* it got its name —
`aria-label`, `label-element`, `value`, `text`, or the derived
`adjacent-label` and `column-header`. The target application has no ARIA and
not one `<label for>`, so almost every control's name is inferred from the
neighbouring table cell. That is legitimate and often the only option, but a
locator built on an inferred name deserves less trust than one built on a name
the application asserted. The artifact schema in step 2 will record that
distinction rather than pretending all names are equal. The renderer marks
derived names with a tilde so a model can see it too.

**Authoritative names always outrank derived ones.** If the application ever
gains real labels, the safer name wins automatically and nothing above this
layer changes.

**Roles describe behaviour, not tags.** An anchor whose `href` is a
`javascript:` URL is reported as a button, because it does not navigate.
Calling it a link would tell both the model and the replay engine something
untrue about what clicking it does. Legacy applications are full of these.

**Layout tables are not data tables.** A table is treated as a grid only when
it has `<th>`, or is at least three columns by three rows with plausible
headers and no nested table. Without that rule the page wrapper — itself a
table — is read as a one-row grid whose single cell contains the entire page.
Layout cells still borrow their captions from the neighbouring cell, which is
how a `Date of Birth | 03/11/1974` panel stays readable; they simply are not
presented as a grid.

**Captions are named by their own text, values by their neighbour.** Both
halves of a label/value pair would otherwise end up named for the row above,
making every row report the first row's label. `value_cell()` tells them apart
by provenance: the value is the one whose name was derived.

**Refs are scoped to one observation, and say so.** Each ref carries the
observation's token. This was not the original design — it was added because a
test that expected a stale ref to be rejected found it silently resolving to a
different element at the same index. A loud failure is vastly better than an
automation acting on something nobody chose.

**Perceiving and acting sit behind one protocol.** A surface you can read but
not drive, or drive but not read, is no use to either the discovery loop or to
replay. Resolving a *durable description* back to a live node is deliberately
not this layer's job: that is replay's, because only replay knows what to do
when a description matches nothing, or matches more than one thing.

**The page is never written to.** The capture script parks elements on
`window.__px`; a ref is a frame index and a position in that array. Nothing is
injected into the application's markup, so observing cannot change how it
behaves — which matters when the whole exercise is judging whether a replay is
deterministic.

## How this extends to other surfaces

The node model is expressed in terms that exist in all three accessibility
systems this has to reach:

| this model | web (ARIA) | Windows UIA | macOS AX |
|---|---|---|---|
| `role` | computed role | `ControlType` | `AXRole` |
| `name` | accessible name | `Name` | `AXTitle` / `AXDescription` |
| `value` | value | `ValuePattern` | `AXValue` |
| `bounds` | bounding rect | `BoundingRectangle` | `AXFrame` |
| `frame_path` | frame chain | window + pane | `AXWindow` chain |
| `table` | table coordinates | `GridItemPattern` | `AXTable` coordinates |

Anything that exists only on the web lives in `WebHints`, which the core model
treats as opaque. A desktop surface would populate its own hints type and leave
web hints empty, and the layers above would not notice. That is the concrete
answer to "what's the seam?" — it is `WebHints`, and the rule that nothing
above the surface layer may read it.

## What this step deliberately does not do

- **No durable locators.** Refs are ephemeral by construction. Deciding what a
  step should target *next month* is the artifact schema's job, in step 2.
- **No resolution strategy, no fallbacks, no waiting for a condition.** Replay
  owns those, because only replay knows what it is waiting for and what to do
  when a description no longer matches.
- **No model.** The surface layer produces text a model can read; nothing calls one
  yet.

## Carried into step 2

The artifact schema needs to record, per step: the frame path, the role, the
name, **the provenance of that name**, and the surviving web hint worth
targeting (`field_name` — the form field name, which outlives a re-render,
unlike the element id). Those five together are what a locator can be rebuilt
from on a page the recorder has never seen.

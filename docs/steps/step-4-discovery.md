# Step 4 — The discovery loop

**Status:** complete · 35 discovery tests (339 total) · **a real gpt-4.1 run is in `evidence/discovery-run/`**

## Why this step exists

§3.1 asks for a goal-driven agent loop: accept a goal and a target, run an
LLM-driven observe → decide → act loop against a live surface until the goal is
met or a stopping condition is hit. §3.2 then asks that the successful run
become a typed, reusable artifact **decoupled from the raw model transcript**.

This step is both, and the second half is the harder one.

## Commands

```bash
make test-discovery           # the whole loop, with a scripted model, no API key
make test-fast                # includes the recorder unit tests

# a real run (needs OPENAI_API_KEY, and ShareBase running):
make discover GOAL="look up member 12345 and read their current savings balance" \
              ID=member.savings_balance

# or replay a recorded run's decisions, with no model and no key:
make discover GOAL="..." ID=member.savings_balance \
              TRANSCRIPT=evidence/discovery-.../transcript.jsonl
```

## Files and what each one does

| File | Responsibility |
|------|----------------|
| `src/explore/tools.py` | The tool surface the model drives, and the one place a ref is turned into a control. |
| `src/explore/prompt.py` | What the model is told. |
| `src/explore/model.py` | Provider behind a small protocol: OpenAI, or a recorded transcript. |
| `src/explore/recorder.py` | Compiles the run into a `Capability`. |
| `src/explore/loop.py` | Observe → decide → act, with a budget. |
| `src/envfile.py` | Loads `.env` for the CLIs — the server did, they did not. |
| `src/explore/cli.py` | `make discover`. |

| Test file | What it proves |
|-----------|----------------|
| `tests/explore/test_recorder.py` | Compilation rules, with no browser and no model. |
| `tests/explore/test_loop.py` | A scripted run produces an artifact that **actually replays** — including against a different member than the one recorded. |

## The idea that does most of the work

**The model does not only act, it annotates.**

An artifact cannot be derived from an action trace. A trace records that
`"12345"` was typed into a field; it cannot say whether that was a parameter a
caller supplies each time or an incidental value, and it cannot say which
screen proves the goal was reached. Those are judgements, and the model is the
only thing in the loop holding them.

So half the tools touch nothing: `declare_input`, `declare_output`, `expect`
and `note_outcome` exist purely to let the model state what it worked out. The
prompt reinforces it — the model is told it is *recording a reusable
capability by doing the task once*, not doing the task. A model told to look
up a balance types "12345" and stops; a model told it is recording a
capability declares the member number as a parameter and notes that "no such
member" is a possible answer.

`note_outcome` is the one that matters most. It is how a run that only ever saw
the happy path still produces a capability that can tell a legitimate answer
apart from a broken application.

## Decisions taken here

**The model never chooses a selector, and never sees one.** It points at a
control by ref; the recorder takes the node the *surface layer* perceived and
asks `contract.derive.locator_for` for every way of finding it again, ordered
by trustworthiness. That is what §3.2 means by an artifact decoupled from the
transcript — the transcript is kept as evidence and the capability owes it
nothing. A test asserts the recorded ladder leads with `txtMemberNo`, a field
name the script never mentions.

**Concrete values are canonicalised into parameters.** Opening a member's
record means clicking the row whose number matches. Recorded literally that
control is named "12345", and the capability would only ever work for one
member. Because the run declared that value as an input, the recorder notices
the control's text *is* the declared example and records the row as *the
column keyed on that input* instead. There is a test that replays the
resulting artifact for a different member and gets that member's balance —
which is the whole difference between a recording and a capability.

This is §8's canonicalisation stretch goal, built as a core feature because
without it the artifact is not reusable at all.

**A recorded capability is born a draft.** An irreversible step may not run
from an unapproved capability, so nothing a model produced can commit anything
until a person has read it. `make review` is that reading.

**A run that did not finish produces nothing.** No success condition, no
declared outcome, or a step referencing an undeclared input, and `build()`
refuses — listing every problem at once. Half a flow that replays to the wrong
screen is worse than nothing, because it looks like a capability.

**A capability begins by establishing where it starts.** A discovery run
begins wherever the harness left the browser, so the recorded steps often
start mid-flow. Replayed that way the capability would act on whatever page
the caller's session happened to be showing, so an opening navigation is
prepended when the run did not record one.

**Checkpoints are verified as they are declared.** When the model calls
`expect`, the text is checked against the page immediately and the model is
told if it is not there. A checkpoint the model believes in but the page does
not show would make replay wait for something that never arrives — and the run
that recorded it would look like a success.

**The guardrail applies during discovery, not only on replay.** §3.4 says the
agent must not act outside the allowlist, and discovery is when the agent is
least predictable. The fault console is outside it: a model able to disarm the
conditions it is meant to learn about would record a capability that only
works on a good day.

**House recoveries are not left to the model.** An interstitial or an expired
session is a property of the *application*, not of the flow being recorded.
Asking a model to rediscover them each run would make each artifact's
robustness depend on whether it happened to trip over one.

**The budget is a stopping condition, not a suggestion.** A loop that can run
forever against a banking application is not a loop.

## Running without a model

`--transcript` replays a previous run's decisions against the live
application. This is not a mock: the same tools execute, the same recorder
runs, and a test asserts the resulting artifact is byte-identical to the
original's steps.

Making that work needed one real change. A ref is valid for exactly one
observation, so a transcript full of refs replays into nothing. The transcript
therefore records, for each acting call, a *durable description* of the
control it touched — role and name, or column and value in a grid — and the
loop resolves that against the freshly rendered page. It is what makes a
discovery run reproducible rather than a thing that happened once, and it is
what lets a reviewer without an API key watch the loop work.

## Provider choice

§4 leaves the provider open. The loop is written against a four-method
protocol rather than any vendor's SDK; the conversation format, the
tool-calling shape and the retry behaviour all live behind it. `OpenAIProvider`
is the default and swapping vendor means adding a sibling class and changing
nothing else — `TranscriptProvider` is the proof, since it satisfies the same
protocol while making no network calls at all.

`temperature=0`, because discovery should be as repeatable as the model allows.

## The real run, and what it taught

§4 requires a genuine LLM-driven run. There is one in
[`evidence/discovery-run/`](../../evidence/discovery-run/): gpt-4.1 driving
ShareBase, producing `member.savings_balance@0.1.0`, which then replays:

| input | result |
|---|---|
| `member_number=12345` | success, `savings_balance = 4821.37` |
| `member_number=22001` | success, `savings_balance = 15310.66` |
| `member_number=99999` | `MEMBER_NOT_FOUND`, a business outcome |

The middle row is the whole point: the model only ever saw member 12345.

**Getting there took four attempts, and each failure produced a guard.** The
model was not doing anything unreasonable — it completed the task correctly
every time. It was the *recording* that kept being subtly wrong, in ways a
prompt alone did not fix:

| What the model did | Why it breaks | The guard |
|---|---|---|
| Finished without recording any non-success outcome | Replay cannot tell an answer from an outage | `finish` refuses and names what is missing |
| Recorded `"1 record(s) returned for member number 12345"` as a checkpoint | True of that run and no other | A checkpoint containing a declared input's value is refused |
| Clicked the whole `<tr>`, naming it after the entire row's text | Pinned to one record forever | A clickable `<tr>` is no longer perceived as a control |
| Invented `"0 record(s) returned"` as the not-found wording — text the application never shows | The outcome is never recognised | `note_outcome` refuses wording the run has not actually seen |
| Explored *after* completing the task | The flow ends where its own success condition does not hold | `finish` refuses unless the success text is on screen now |

The lesson worth keeping: **prompts are hope, guards are engineering.** Each
of these was first attempted as prompt wording, and the model kept making the
same mistake until the loop enforced it. The pattern that works is to check
the thing at the moment it is declared, refuse with the specific reason, and
let the model correct itself while it still can — the run recovers instead of
being wasted.

`restart_flow` came out of the same process. A real run explores before it
performs, and those detours have no business in the capability; dropping them
while keeping what was learned is what lets the model look around freely.

## What this step deliberately does not do

- **No multi-goal or chained discovery.** One goal, one capability.
- **No self-correction loop.** If the model records a bad checkpoint it is
  told immediately, but nothing retries on its behalf.
- **No model in replay, ever.** §8 offers bounded LLM recovery on replay
  failure as a stretch goal; it is not built, because a replay that can reason
  its way out of a surprise is no longer deterministic.

## Carried into step 5

The engine already raises `Escalated` where a human is needed — an
irreversible step without authorisation, a session expiry with no way to
recover — and the loop has `give_up` for a model that cannot proceed. Step 5
turns both into an intervention request carrying context, a live session a
human can drive, and a way to hand control back.

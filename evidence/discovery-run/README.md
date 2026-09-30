# A real discovery run

One genuine LLM-driven run against the live application, as §4 requires.

    model:  openai:gpt-4.1
    goal:   look up member 12345 and read their current savings balance
    result: member.savings_balance@0.1.0.capability.json

## What is here

| File | What it holds |
|------|---------------|
| `transcript.jsonl` | Every decision the model made, with its reasoning, and the durable description of each control it touched. This file can be replayed against the application to reproduce the run without an API call. |
| `run.jsonl` | The structured log: each turn's reasoning, each tool call, each result. |
| `member.savings_balance@0.1.0.capability.json` | What the run produced. |

## Reproducing it

With ShareBase running (`make run`) and no API key at all:

```bash
make discover GOAL="look up member 12345 and read their current savings balance" \
              ID=member.savings_balance \
              TRANSCRIPT=evidence/discovery-run/transcript.jsonl
```

## What the recorded capability does

Replaying it for the member it was recorded on, for a member it never saw, and
for one that does not exist:

```bash
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=12345"
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=22001"
make replay CAP=member.savings_balance@0.1.0 IN="--input member_number=99999"
```

    12345  success           savings_balance = 4821.37
    22001  success           savings_balance = 15310.66
    99999  business_outcome  MEMBER_NOT_FOUND

The middle one is the point. The model only ever saw member 12345, and the
capability it recorded addresses the result row as *the Member No cell keyed
on the supplied member number* rather than as the literal "12345" it clicked.

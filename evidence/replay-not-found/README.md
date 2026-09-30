# replay-not-found

A legitimate business answer. No member record exists, so the run reports `MEMBER_NOT_FOUND` with `needs_attention` false. This is the case the brief calls out: it is an answer the caller asked for, not an incident.

    member.savings_balance@1.0.0: MEMBER_NOT_FOUND -- No member record exists for the supplied member number. A legitimate answer, not a failure.

Reproduce with ShareBase running (`make run`):

```bash
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=99999"
```

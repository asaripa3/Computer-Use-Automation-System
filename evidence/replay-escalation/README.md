# replay-escalation

A replay that reaches an irreversible step it may not take on its own, hands the live session to a person, and carries on from what they decided.

The run completes ten steps first, so the operator arrives at the review screen with the request already filled in rather than being handed a blank form. They confirm it themselves in that same signed-in session and answer `skip`.

The automation does not take that on trust: the step's checkpoint is still evaluated, so an operator who said they did something they did not would produce a failed checkpoint rather than a run carrying on from a state nobody verified.

    member.open_subaccount@1.0.0: success (new_account_number, confirmation_number)

The request carries a redacted page structure as the operator found it. `run.jsonl` holds the request, what they decided, and the pages and field changes observed while they held the session.

Reproduce it interactively, working in the browser window yourself:

```bash
make replay CAP=member.open_subaccount@1.0.0 \
  IN="--input member_id=12345 --input account_type='Vacation Club Savings' \
      --input nickname='Vacation 2027' --input initial_deposit=150.00 \
      --input funding_account=0001234502 \
      --allow-irreversible --operator console --headed"
```

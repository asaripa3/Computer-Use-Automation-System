# replay-success

A clean replay. The flow completes, the checkpoint holds, and both declared outputs are read and coerced to their declared types -- note `savings_balance` arrives as `4821.37` from a screen that renders `4,821.37`.

    member.savings_balance@1.0.0: success (member_name, savings_balance)

Reproduce with ShareBase running (`make run`):

```bash
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"
```

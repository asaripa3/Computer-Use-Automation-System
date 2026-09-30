# replay-hard-failure

The application fails mid-flow with an error the artifact does not declare. The run stops and reports which step failed, what it expected and what it observed, with a screenshot and a record of everything the surface layer perceived at that moment.

    member.savings_balance@1.0.0: failed -- step 4 expected the member detail page has loaded, observed 'ShareBase — Application Error' at http://127.0.0.1:8097/console/member/12345

Reproduce with ShareBase running (`make run`):

```bash
make fault F=hard_error
make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"
```

# Execution v2 contracts

Execution commands are defined by [`control.openapi.yaml`](./control.openapi.yaml)
and are sent over the Execution UDS endpoint using HTTP-shaped JSON. The
shared command envelope, admission response, and error response are defined by
[`common/control.openapi.yaml`](../common/control.openapi.yaml).
FlatBuffers is reserved for the Execution event stream and active mmap views.

The command response acknowledges admission or rejection only. Exchange
acknowledgements, cancellations, expirations, and fills are immutable facts on
the event stream. `ActiveIntentsView` and `ActiveOrdersView` retain only
non-terminal state; terminal history belongs to query/audit storage.

The execution model is split by lifecycle and ownership boundary. Recursive
references are kept within the smallest necessary type family: plan types
include intent types, while order, fill, and reconciliation types remain
independent. The public roots are organized by transport role:

```text
v2/
  control.openapi.yaml
  events/
    intent/
    plan/
    order/
    fill/
    reconciliation/
  views/
  types/
    intent.fbs
    plan.fbs
    order.fbs
    fill.fbs
    reconciliation.fbs
```

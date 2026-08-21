# Execution v2 contracts

Execution commands are defined by the Execution Rust JSON-RPC contract trait
and are sent over the Execution workspace Unix control socket. FlatBuffers is
reserved for the Execution event stream and active mmap views.

The command response acknowledges admission or rejection only. Exchange
acknowledgements, cancellations, expirations, and fills are immutable facts on
the event stream. `ActiveIntentsView` and `ActiveOrdersView` retain only
non-terminal state; terminal history belongs to query/audit storage.

Strategy-originated intents carry the optional migration field
`strategy_decision_id`, which Execution preserves as an opaque and immutable
causal reference. Intent event roots have distinct meanings:

- `IntentAccepted` (`EIA2`) is admission acceptance only;
- `IntentRejected` (`EIR2`) is admission rejection only;
- `IntentLifecycleChanged` (`EIL2`) carries every later transition with its
  explicit previous/current lifecycle, completed quantity, child order IDs,
  reason, and dependency evidence.

Publishers map application models directly to these FlatBuffers roots. New
consumers should decode all three; they must not infer a previous lifecycle
from local delivery order.

The execution model is split by lifecycle and ownership boundary. Recursive
references are kept within the smallest necessary type family: plan types
include intent types, while order, fill, and reconciliation types remain
independent. The public roots are organized by transport role:

```text
v2/
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

# System v2 contract boundary

System owns workspace and runtime operation facts: process, Actor, connection,
and alert health. It does not own business truth from Account, Market, Risk,
Execution, or Reference.

System does not currently publish an indexed current view. Operational health
and stop operations are exposed through its Conflux JSON-RPC control
registration; business freshness and state remain in each business owner.

The `owner_id` on a connection row identifies the runtime or business
component that owns the connection. Connection status is normalized into the
System vocabulary and must not expose provider-specific domain types.

Any future System current view must use the owner-scoped indexed-view contract;
removed aggregate roots are not a compatibility surface.

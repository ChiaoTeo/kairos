# System v2 contract boundary

System owns workspace and runtime operation facts: process, Actor, connection,
and alert health. It does not own business truth from Account, Market, Risk,
Execution, or Reference.

`SystemHealthCurrentView` and `AlertsCurrentView` are current views published
by the System monitor. They use `common/v2/ViewMetadata`; their rows contain
operational state only. Business freshness and business state remain in the
owning module's views.

The `owner_id` on a connection row identifies the runtime or business
component that owns the connection. Connection status is normalized into the
System vocabulary and must not expose provider-specific domain types.

System-level health and stop methods are exposed through the Conflux JSON-RPC
control registration. They are intentionally separate from the FlatBuffers
current view, which is a one-shot operational current view for local consumers.

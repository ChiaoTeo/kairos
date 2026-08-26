# Risk v2 contracts

Risk commands are defined by the Risk Rust JSON-RPC contract trait and use the
workspace Unix control socket.
`AuthorizeAndReserve` is synchronous and returns the
authoritative decision plus reservation result. `ConsumeReservation` and
`ReleaseReservation` are idempotent cleanup commands.

FlatBuffers is used for the retained Risk event stream and individual indexed current
values. The current view separates state, policies, limit usage, allocations, reservations, and
circuits into named LMDB databases. The former aggregate root and reader have been removed.
Events describe concrete facts such as a
reservation being reserved, consumed, released, or expired, and a circuit
opening or closing. They are not status polling notifications.

The latest view contains policy/limit usage, active reservations, and current
circuit state. Terminal reservations move to query/audit storage and are not
retained indefinitely in the current view.

# Risk v2 contracts

Risk commands are defined by the Risk Rust JSON-RPC contract trait and use the
workspace Unix control socket.
`AuthorizeAndReserve` is synchronous and returns the
authoritative decision plus reservation result. `ConsumeReservation` and
`ReleaseReservation` are idempotent cleanup commands.

FlatBuffers is reserved for the retained Risk event stream and the
`RiskLatestView` mmap resource. Events describe concrete facts such as a
reservation being reserved, consumed, released, or expired, and a circuit
opening or closing. They are not status polling notifications.

The latest view contains policy/limit usage, active reservations, and current
circuit state. Terminal reservations move to query/audit storage and are not
retained indefinitely in mmap.

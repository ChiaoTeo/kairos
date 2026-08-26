# Capital v2 contracts

Capital commands and synchronous queries use the explicit JSON control
boundary. The current view stores independently keyed Capital entities in one owner-scoped LMDB
environment. Every named database has one dedicated FlatBuffers current-value root with one required
business value; no generic optional-field entity envelope exists. The former aggregate root was
removed rather than retained as a fallback. Durable business transitions are published as typed
FlatBuffers events on the Capital event stream (`1701`).

The current view exposes policies, objectives, demand observations, source
facts, readiness/effective targets, routes, plans, reservations, and
participant operations. Actor persistence records, journal entries, lease
secrets, and pending outbox bookkeeping are intentionally not part of the
public contract.

Each Actor event variant has its own root and identifier. The Capital process
persists the transition before publication and acknowledges its outbox entry
only after the event publisher returns success; recovery republishes any
unacknowledged event with the same sequence and payload identity.

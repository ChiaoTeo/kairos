# OKX Trading Account

This note records the provider behavior and provenance used by the OKX Trading Account async
slice. The long-term boundary is defined in
[`integration-session-and-operation-design.md`](../integration-session-and-operation-design.md).

## Sources and license

- Fact source: official OKX API documentation for REST account/balance, account configuration,
  private WebSocket login, `account`, and `orders` channels.
- Upstream adapter source copied in this slice: none.
- Third-party license obligations introduced by this slice: none.

## Kairos implementation

- Provider/principal construction:
  `crates/platform/integration/src/application/participants/okx/connection.rs`.
- Async Account capability handles:
  `crates/platform/integration/src/application/participants/okx/connection/account.rs`.
- Payload normalizers:
  `crates/platform/integration/src/services/participants/okx/mod.rs` and `stream.rs`.
- Business composition and recovery:
  `crates/modules/account/src/composition/account.rs` and
  `src/application/process.rs`.

## Mapping and deliberately uncopied areas

- OKX `instType` is retained as a provider-native request filter; Account segment state remains
  owned by the Account Actor.
- REST account/configuration responses become Integration external account/profile facts before
  crossing into Account.
- Private WebSocket login must succeed and both `account` and `orders` subscription acknowledgments
  must arrive before the channel reports ready.
- Private events carry participant, binding, channel epoch, provider event ID/sequence, and two
  timestamps. Account owns duplicate suppression, recovery buffering, and snapshot resync.
- No provider SDK domain model, event bus, cache, engine runtime, or generic session registry was
  introduced.

## Tests and open items

- Focused tests cover async snapshot/profile normalization, login/subscription readiness, private
  event normalization, and Account continuity behavior.
- Provider instrument identity is resolved through the Reference current-view database using
  `exchange_id`, `venue_symbol`, and `instrument_kind`; the temporary canonical-identity mapping is
  no longer part of Account composition.
- Production server and CLI use only the native async OKX Account snapshot/private-event path; the
  legacy blocking Account registry/synchronization path has been removed.

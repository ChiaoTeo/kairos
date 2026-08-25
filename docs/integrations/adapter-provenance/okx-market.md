# OKX Market adapter provenance

## Sources and license

- Provider specifications: OKX V5 public WebSocket `books` and `trades` channels, sequence-ID
  rules, and the 2026 checksum deprecation notice; retrieved 2026-08-11.
- Upstream behavior reference: NautilusTrader `develop` commit
  `718f0b704aa4144c3237eb32fb88be7e15cef3b7`, paths
  `crates/adapters/okx/src/book_sync.rs`, `crates/adapters/okx/src/data.rs`, and
  `docs/integrations/okx.md`. License: LGPL-3.0-or-later.
- No upstream source was copied or translated. Provider behavior, failure cases, and test coverage
  were inspected as engineering evidence.

## Kairos mapping

- Integration owns the native async socket, OKX subscribe/unsubscribe payloads, provider payload
  parsing and `prevSeqId/seqId` continuity validation.
- Initial `books` frames become `BookSnapshot`; linked updates become `BookDelta`; a gap becomes
  `IntegrationError::ResyncRequired` naming the affected source symbol.
- Market owns canonical route selection, subscription intent, source epoch, bounded backpressure,
  resubscription/resync commands, the order book, freshness and business publication.
- The deprecated checksum is not used. Sequence continuity is the integrity rule.

## Deliberately uncopied

- Nautilus domain models, caches, message bus, engine/runtime, generalized client factories,
  Python bindings, execution/account paths and book health monitor architecture.

## Evidence

- Focused normalizer test verifies snapshot acceptance and gap-triggered resync.
- The common Market source recovery test verifies a controlled transport disconnect advances the
  source epoch and returns to ready.
- REST ticker snapshots remain a separate async capability selected explicitly with
  `transport = "rest"`; they do not masquerade as a live channel.
- The 2026-08-25 production-public certification covers Spot trades, Swap mark price, an active
  expiry Futures quote, and active-family Options Greeks across subscribe, receive,
  replacement-first reconnect/restore, receive, and unsubscribe. See
  [OKX public market-stream certification](../okx-market-stream-certification.md).

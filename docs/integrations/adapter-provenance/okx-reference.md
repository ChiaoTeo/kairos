# OKX Reference catalog adapter provenance

## Sources and license

- Provider specification: [OKX V5 API guide](https://www.okx.com/docs-v5/en/), Public Data
  `GET /api/v5/public/instruments` and `GET /api/v5/public/underlying`.
- Live behavior reconfirmed on 2026-08-11: `instType=OPTION` requires either `uly` or
  `instFamily`; the public underlying endpoint returns the option underlyings that must be
  enumerated for a complete catalog.
- Upstream adapter source copied or translated: none.
- Upstream behavior reference for the later live Market slice: NautilusTrader `develop` commit
  `718f0b704aa4144c3237eb32fb88be7e15cef3b7`, especially
  `crates/adapters/okx/src/book_sync.rs`, `crates/adapters/okx/src/data.rs`, and
  `docs/integrations/okx.md`. License: LGPL-3.0-or-later. No source was copied or translated.
- Third-party license obligations introduced by this slice: none.

## Kairos mapping

- Integration owns the OKX provider-native connection, option-underlying enumeration, instrument
  requests, payload validation, and normalized `ExternalInstrumentCatalog` facts.
- Reference owns canonical entities, assets, instruments, listings, markets, lifecycle diffing,
  persistence, snapshots, and event publication.
- Spot, swap, futures, and options remain independent capability projections in Reference
  composition. An option refresh is complete only after every advertised underlying has returned
  successfully.
- Market's independent public WebSocket projection subscribes to `books` and `trades`. A snapshot
  requires `prevSeqId=-1`; each incremental update must link its `prevSeqId` to the accepted
  `seqId`. A gap becomes `ResyncRequired` for the affected canonical market. The deprecated
  checksum field is deliberately ignored in favor of sequence continuity.

## Deliberately uncopied

- OKX SDK domain models, caches, event buses, trading engines, account state, and generic session
  registries.
- Provider WebSocket runtime architecture. Instrument stream updates remain a separate slice until
  ordering, reconnect, backpressure, and REST resync behavior are explicit.

## Evidence

- Integration caller-runtime test verifies the option request sequence: enumerate underlyings,
  then fetch and merge every option family.
- Reference provider tests verify OKX normalized facts map into canonical Reference markets.
- Live full-refresh validation covers all configured OKX product projections and reports provider
  health independently.
- Focused Market-data test verifies snapshot acceptance and gap-triggered resync.

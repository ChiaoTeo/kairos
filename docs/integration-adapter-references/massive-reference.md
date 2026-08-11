# Massive Reference Catalog

## Sources and license

- Provider contract: Massive REST reference endpoints for stock tickers and
  option contracts (`/v3/reference/tickers` and
  `/v3/reference/options/contracts`).
- Upstream adapter source copied or translated: none.
- Third-party source-code license obligations introduced by this slice: none.

## Kairos mapping

- Integration owns bearer authentication, provider pagination, cursor parsing,
  HTTP failure classification, and normalized `ExternalInstrumentCatalog`
  facts.
- Reference owns the durable cursor and page staging, canonical
  entities/assets/instruments/listings/markets, last-known-good promotion,
  lifecycle events, snapshots, and publication.
- Stock and option catalogs are independent capability projections. Each page
  is persisted as its own SQLite staging row before its cursor is exposed as
  completed progress; only a page with no next cursor promotes a candidate.
  This avoids repeatedly serializing a growing candidate on every page.
- Authentication is carried only in the `Authorization: Bearer` header. Kairos
  does not put credentials in request URLs and strips an `apiKey` query field
  from provider-supplied continuation URLs before following them.

## Deliberately uncopied

- Provider SDK domain objects, caches, streaming runtimes, and application
  architecture.
- A generic session/operation facade. REST catalog pagination remains an async
  query capability rather than being modeled as a long-lived session.
- A default global stock-options catalog. Reference now requires explicit,
  durable underlying coverage and uses a provider key per underlying
  (`massive-options:<UNDERLYING>`) for cursors, staging and last-good facts.

## Evidence

- Local HTTP contract coverage verifies header-only authentication, including
  continuation URLs, and verifies that the request target never contains the
  credential.
- Failure/resume coverage verifies that every successful page and its cursor
  survive restart before a later page fails.
- The current workspace has exercised the real credentialed endpoint for both
  equity and option catalogs. Exact full-catalog completion and scale evidence
  are tracked in `docs/integration-migration-status.md`; an in-progress cursor
  is not reported as a completed acceptance run. SQLite staging bounds
  pagination progress only—the final catalog promotion is still a scale gate,
  not evidence that an all-market options universe is suitable for the
  steady-state Reference snapshot.

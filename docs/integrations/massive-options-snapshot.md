# Massive options snapshot integration

Last reviewed: 2026-08-18.

`MassiveRestConnection` configured for Options exposes a provider-native
`fetch_option_snapshot` method and implements `MarketGreeksQuery`. The request uses Massive's
single-contract snapshot endpoint and therefore requires the connection's bounded underlying plus
the requested option contract symbol.

The typed snapshot preserves:

- contract symbol, expiry and strike;
- delta, gamma, theta, vega and implied volatility;
- break-even price, open interest and market status;
- the latest provider timestamp available from FMV, quote, trade or daily snapshot evidence.

Massive documents Greeks and several market fields as optional. Missing values remain `None`; the
adapter does not manufacture zeroes. If no component supplies a timestamp, request receipt time is
used and the derivation remains `massive-options-snapshot`. HTTP 403 plan denials continue to map to
`IntegrationError::Entitlement`.

This is an Integration market-data extension only. It does not create option valuation logic,
portfolio risk state or a business-layer snapshot owner.

Official references:

- <https://massive.com/docs/rest/options/snapshots/option-contract-snapshot>
- <https://massive.com/docs/rest/options/snapshots/option-chain-snapshot>

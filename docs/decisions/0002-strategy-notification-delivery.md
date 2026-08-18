# Decision 0002: Strategy notification delivery

- Status: Accepted
- Date: 2026-08-18
- Scope: Strategy runtime, Workspace/Launch configuration and notification delivery

## Context

Strategies need to emit operational and trading-signal notifications without
blocking market callbacks, handling provider credentials, or duplicating
Feishu and Telegram transport behavior. Backtests must not accidentally send
historical signals to live destinations.

## Decision

- Strategy publishes a provider-neutral notification request and selects only
  logical routes.
- Workspace owns destinations and credentials. Launch selects the routes
  allowed for one run, and the instance stores only normalized, secret-free
  configuration.
- `NotificationApplication.publish` performs synchronous validation and
  bounded enqueueing; it never performs network I/O.
- One runtime-owned delivery worker owns queue, deduplication, fan-out and
  delivery status. Each destination is isolated so one failure does not cancel
  other deliveries.
- Provider HTTP behavior is delegated to the pinned Apprise integration.
  Strategy and application code do not create provider clients.
- Queue saturation is an explicit rejection. Delivery failures are observable
  but do not mutate Strategy, Market or Execution business state.
- Backtest uses recording delivery by default and cannot bypass its external
  delivery safety switch.
- Required notification configuration must validate before the Strategy
  process becomes ready.

## Consequences

Notification owns outbound delivery attempts, not the strategy signal that
caused them. System Alert remains a separate operational concept. A new
cross-process notification service is justified only after a real non-Strategy
caller needs the same capability.

User configuration and API examples belong in
[`../guides/strategy-notifications.md`](../guides/strategy-notifications.md).


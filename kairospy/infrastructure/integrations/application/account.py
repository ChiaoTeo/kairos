from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from kairospy.core.account import AccountBookRef, AccountContext, AccountSnapshot
from kairospy.core.order import OrderState
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, RawPayload, RawPayloadRows, RawPayloadStream
from kairospy.infrastructure.integrations.services.resolver import DEFAULT_INTEGRATION_RESOLVER, IntegrationResolver


@dataclass(frozen=True, slots=True)
class AccountIntegrationApplicationService:
    """Concrete account integration service exposed to application composition."""

    book: AccountBookRef
    credential: str | None = None
    resolver: IntegrationResolver = DEFAULT_INTEGRATION_RESOLVER
    mode_label: str = "runtime"
    error_type: type[Exception] = ValueError
    market_resolver: object | None = None

    def fetch_balance(self, *, params: IntegrationParams | None = None) -> RawPayload:
        return self._account_client().fetch_balance(params=params)

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self._account_client().fetch_open_orders(symbol, since=since, limit=limit, params=params)

    def watch_balance(self, *, params: Mapping[str, object] | None = None) -> RawPayloadStream:
        return self._account_client().watch_balance(params=params)

    def watch_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> RawPayloadStream:
        return self._account_client().watch_orders(symbol, since=since, limit=limit, params=params)

    def watch_my_trades(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> RawPayloadStream:
        return self._account_client().watch_my_trades(symbol, since=since, limit=limit, params=params)

    def snapshot(
        self,
        context: AccountContext,
        raw_balance: RawPayload,
        raw_orders: tuple[RawPayload, ...],
        *,
        observed_at: datetime,
    ) -> AccountSnapshot:
        return self._payload_adapter().snapshot(context, raw_balance, raw_orders, observed_at=observed_at)

    def import_open_order(
        self,
        context: AccountContext,
        coordinator: object,
        raw: RawPayload,
        *,
        observed_at: datetime,
    ) -> OrderState:
        return self._payload_adapter().import_open_order(context, coordinator, raw, observed_at=observed_at)

    def balance_snapshot(
        self,
        context: AccountContext,
        raw_balance: RawPayload,
        *,
        at: datetime,
        open_orders: tuple = (),
    ) -> AccountSnapshot:
        return self._payload_adapter().balance_snapshot(context, raw_balance, at=at, open_orders=open_orders)

    def ingest_order_update(self, coordinator: object, context: AccountContext, raw: RawPayload) -> OrderState:
        return self._payload_adapter().ingest_order_update(coordinator, context, raw)

    def ingest_trade_update(self, coordinator: object, context: AccountContext, raw: RawPayload) -> OrderState:
        return self._payload_adapter().ingest_trade_update(coordinator, context, raw)

    def _account_client(self) -> object:
        return self.resolver.account_bootstrap_for_book(
            self.book,
            self.credential,
            mode_label=self.mode_label,
            error_type=self.error_type,
        )

    def _payload_adapter(self) -> CcxtAccountPayloadAdapter:
        return CcxtAccountPayloadAdapter(self.market_resolver)  # type: ignore[arg-type]


__all__ = ["AccountIntegrationApplicationService"]

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.usecases.account.application.directory import AccountBinding, AccountDirectory
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.account.application.runtime import RuntimeAccountService
from kairospy.application.usecases.account.application.view_contracts import AccountViewObservation
from kairospy.application.usecases.execution.application.runtime import RuntimeExecutionService
from kairospy.application.usecases.account.application.provisioning import AccountProvisioningService
from kairospy.domain.account import (
    ACCOUNT_SCOPES_SCHEMA,
    ACCOUNT_CAPABILITIES_SCHEMA,
    ACCOUNT_EQUITY_CURVE_SCHEMA,
    ACCOUNT_FEES_SCHEMA,
    ACCOUNT_MARKET_PROFILES_SCHEMA,
    AccountBalance,
    AccountSegment,
    AccountSegmentSummary,
    AccountSegmentsView,
    AccountCapabilitiesView,
    AccountCapability,
    AccountRuntimeContext,
    AccountCurrentView,
    AccountDetailView,
    AccountEvent,
    AccountEventKind,
    AccountFeesView,
    AccountMarketProfilesView,
    AccountPortfolioView,
    AccountSnapshot,
    AccountViewKeys,
    EquityCurvePoint,
    EquityCurveView,
    account_current_schema,
    account_detail_schema,
    account_portfolio_schema,
)
from kairospy.domain.market import Bar, MarketEvent, Quote, RateObservation, TradePrint
from kairospy.domain.reference import MarketId, reference_slug


class AccountCurrentProjector:
    def __init__(self, service: RuntimeAccountService, context: AccountRuntimeContext, execution: RuntimeExecutionService | None = None) -> None:
        self.service = service
        self.context = context
        self.execution = execution
        self.key = AccountViewKeys.current(context)
        self.detail_key = AccountViewKeys.detail(context)
        self.schema = account_current_schema(self.key)
        self.detail_schema = account_detail_schema(self.detail_key)
        self._count = 0
        self._last: Message | None = None
        self._payload: AccountViewObservation | None = None
        initial_view = service.current_view(context, now=datetime.now(timezone.utc))
        self._initial: Decimal | None = initial_view.equity
        self._latest: Decimal | None = None

    def on_event(self, event: Message) -> None:
        if event.domain != "account" or getattr(event.payload, "context", None) != self.context:
            return
        if not isinstance(event.payload, AccountSnapshot) and not hasattr(event.payload, "context"):
            return
        self._count += 1
        metadata = getattr(event.payload, "metadata", {})
        self._last = event
        self._payload = AccountViewObservation(
            equity=None if getattr(event.payload, "equity", None) is None else Decimal(str(event.payload.equity)),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        equity = getattr(event.payload, "equity", None)
        if equity is not None:
            value = Decimal(str(equity))
            self._initial = value if self._initial is None else self._initial
            self._latest = value

    def view(self, *, as_of: datetime | None = None) -> AccountCurrentView:
        orders = getattr(self.execution, "orders", None)
        pending_orders = () if not callable(orders) else tuple(
            order for order in orders(self.context.segment) if not order.status.terminal
        )
        return self.service.current_view(
            self.context,
            event_count=self._count,
            last_event_time=None if self._last is None else self._last.time,
            payload=self._payload,
            latest_equity=self._latest,
            initial_equity=self._initial,
            pending_orders=pending_orders,
            now=as_of,
        )

    def detail(self, *, as_of: datetime | None = None) -> AccountDetailView:
        return self.service.detail_view(
            self.context,
            event_count=self._count,
            last_event_time=None if self._last is None else self._last.time,
            metadata=None if self._payload is None else dict(self._payload.metadata),
            now=as_of,
        )


class AccountProjector:
    def __init__(self, service: RuntimeAccountService, execution: RuntimeExecutionService | None = None) -> None:
        self.service = service
        self.directory = service.directory()
        self.states = tuple(AccountCurrentProjector(service, context, execution) for context in self.directory.contexts())

    def on_event(self, event: Message) -> None:
        for state in self.states:
            state.on_event(event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        for schema in (ACCOUNT_SCOPES_SCHEMA, ACCOUNT_CAPABILITIES_SCHEMA, ACCOUNT_FEES_SCHEMA, ACCOUNT_MARKET_PROFILES_SCHEMA):
            if views.registry.get(schema.key) is None:
                views.register(schema)
        for state in self.states:
            for schema in (state.schema, state.detail_schema):
                if views.registry.get(schema.key) is None:
                    views.register(schema)
        for context in self._portfolio_contexts():
            schema = account_portfolio_schema(AccountViewKeys.portfolio(context))
            if views.registry.get(schema.key) is None:
                views.register(schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(AccountViewKeys.segments, self._scopes(), as_of=as_of, available_time=as_of)
        views.put_runtime(AccountViewKeys.capabilities, self._capabilities(), as_of=as_of, available_time=as_of)
        views.put_runtime(AccountViewKeys.fees, AccountFeesView(len(self.service.fees()), self.service.fees()), as_of=as_of, available_time=as_of)
        profiles = tuple(self.service.market_profiles())
        views.put_runtime(AccountViewKeys.market_profiles, AccountMarketProfilesView(len(profiles), profiles), as_of=as_of, available_time=as_of)
        current = []
        for state in self.states:
            item = state.view(as_of=as_of)
            current.append(item)
            views.put_runtime(state.key, item, as_of=as_of, available_time=as_of)
            views.put_runtime(state.detail_key, state.detail(as_of=as_of), as_of=as_of, available_time=as_of)
        for portfolio in _portfolio_views(current):
            views.put_runtime(AccountViewKeys.portfolio(portfolio.segments[0].context), portfolio, as_of=as_of, available_time=as_of)

    def _scopes(self) -> AccountSegmentsView:
        bindings = self.directory.bindings
        summaries = []
        for state in self.states:
            binding = next((item for item in bindings if state.context in item.segments), None)
            summaries.append(
                AccountSegmentSummary(
                    state.key,
                    _alias(state.context),
                    "" if binding is None else binding.alias,
                    0 if binding is None else binding.index,
                    _account_key(state.context),
                    _segment_key(state.context),
                    state.context.environment.value,
                    str(state.context.segment.broker),
                    str(state.context.segment.account_id),
                    state.context.segment.model.value,
                    state.context.segment.qualifier,
                )
            )
        return AccountSegmentsView(len(summaries), tuple(summaries))

    def _capabilities(self) -> AccountCapabilitiesView:
        values = self.service.capabilities() or tuple(AccountProvisioningService().capability(state.context.segment) for state in self.states)
        return AccountCapabilitiesView(len(values), values)

    def _portfolio_contexts(self) -> tuple[AccountRuntimeContext, ...]:
        return tuple({(state.context.environment.value, str(state.context.identity.broker), str(state.context.identity.account_id)): state.context for state in self.states}.values())


class EquityProjector:
    key = "account.equity_curve"

    def __init__(self, service: RuntimeAccountService) -> None:
        self.service = service
        self.account = service.account
        self.valuation_asset = service.valuation_asset
        self._marks: dict[str, Decimal] = {}
        self._points: list[EquityCurvePoint] = []
        self._last_marker: object | None = None

    def on_event(self, event: Message) -> None:
        self._mark(event.payload)
        self.record(event.time)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if getattr(context, "now", None) is not None:
            self.record(context.now)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(ACCOUNT_EQUITY_CURVE_SCHEMA.key) is None:
            views.register(ACCOUNT_EQUITY_CURVE_SCHEMA)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.key, EquityCurveView(self.account, tuple(self._points)), as_of=as_of, available_time=as_of)

    def record(self, at: datetime) -> None:
        selected_balance = self.service.asset_balance(self.valuation_asset)
        positions = tuple(sorted(self.service.positions().items()))
        equity = selected_balance + sum(quantity * self._marks[instrument] for instrument, quantity in positions if instrument in self._marks)
        marker = (at, equity, selected_balance, positions)
        if marker != self._last_marker:
            self._points.append(EquityCurvePoint(at, equity, selected_balance, positions))
            self._last_marker = marker

    def _mark(self, payload: object) -> None:
        value = payload.value if isinstance(payload, MarketEvent) else payload
        instrument = getattr(value, "instrument_id", None)
        price = _mark_price(value)
        if instrument is not None and price is not None and price > 0:
            self._marks[str(instrument)] = price


class FundingProjector:
    def __init__(self, service: RuntimeAccountService) -> None:
        self.service = service
        self.currency = service.settlement_asset
        self._applied: set[tuple[str, datetime]] = set()

    def on_event(self, event: Message) -> None:
        value = event.payload.value if isinstance(event.payload, MarketEvent) else event.payload
        if not isinstance(value, RateObservation) or value.rate == 0 or value.mark_price is None:
            return
        instrument = value.instrument_id or _instrument_from_market(value.market_id)
        if instrument is None:
            return
        position = self.service.positions().get(str(instrument), Decimal("0"))
        key = (str(value.market_id or value.rate_id), value.time)
        if position == 0 or key in self._applied:
            return
        self.service.record_funding(occurred_at=value.time, currency=self.currency, balance_delta=-(position * value.mark_price * value.rate), instrument_id=str(instrument), reference_id=f"funding:{key[0]}:{value.time.isoformat()}")
        self._applied.add(key)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        return None

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        return None


def _portfolio_views(views: list[AccountCurrentView]) -> tuple[AccountPortfolioView, ...]:
    groups: dict[str, list[AccountCurrentView]] = {}
    for projector in views:
        groups.setdefault(_account_key(projector.context), []).append(projector)
    results = []
    for key, items in groups.items():
        currencies = {item.valuation_asset for item in items}
        valuation_asset = next(iter(currencies)) if len(currencies) == 1 and None not in currencies else None
        complete = valuation_asset is not None and not any(item.stale for item in items)
        selected_balance_values = tuple(item.selected_balance for item in items)
        equity_values = tuple(item.equity for item in items)
        results.append(
            AccountPortfolioView(
                key,
                items[0].context.environment.value,
                str(items[0].context.identity.broker),
                str(items[0].context.identity.account_id),
                tuple(items),
                tuple(balance for item in items for balance in item.balances),
                tuple(margin for item in items for margin in item.margins),
                tuple(collateral for item in items for collateral in item.collaterals),
                tuple(liability for item in items for liability in item.liabilities),
                tuple(position for item in items for position in item.positions),
                tuple(order for item in items for order in item.open_orders),
                sum(selected_balance_values, Decimal("0")) if complete and all(value is not None for value in selected_balance_values) else None,
                sum(equity_values, Decimal("0")) if complete and all(value is not None for value in equity_values) else None,
                any(item.stale for item in items),
                max((item.last_event_time for item in items if item.last_event_time is not None), default=None),
                valuation_asset,
                complete,
            )
        )
    return tuple(results)


def _account_key(context: AccountRuntimeContext) -> str:
    return ".".join(_key_part(value) for value in (context.segment.broker, context.segment.account_id) if value)


def _segment_key(context: AccountRuntimeContext) -> str:
    return ".".join(_key_part(value) for value in context.segment.key.split(":"))


def _alias(context: AccountRuntimeContext) -> str:
    return ".".join(value for value in (_account_key(context), _segment_key(context)) if value)


def _key_part(value: object) -> str:
    return "_".join("".join(char if char.isalnum() else "_" for char in str(value).lower()).split("_"))


def _mark_price(value: object) -> Decimal | None:
    price = value.close if isinstance(value, Bar) else value.price if isinstance(value, TradePrint) else value.mark_price if isinstance(value, RateObservation) else value.midpoint or value.bid or value.ask if isinstance(value, Quote) else None
    return None if price is None else Decimal(str(price))


def _instrument_from_market(value: MarketId | str | None) -> str | None:
    if value is None:
        return None
    parts = str(value).split(":")
    if len(parts) < 4 or parts[0] != "market":
        return None
    tokens = reference_slug(":".join(parts[3:])).split("_", 1)
    return None if len(tokens) != 2 else f"instrument:{reference_slug(parts[2])}:{tokens[0]}:{tokens[1]}"


__all__ = ["AccountCurrentProjector", "AccountProjector", "EquityProjector", "FundingProjector"]

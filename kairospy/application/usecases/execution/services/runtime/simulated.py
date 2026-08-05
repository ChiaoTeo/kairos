from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from decimal import Decimal

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.execution.application.component import ExecutionApplication, ExecuteIntentCommand
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.application.usecases.execution.domain.simulation import TradingRules
from kairospy.application.usecases.execution.services.simulation import CommissionModel, FillModel, ImmediateFillModel, NoSlippageModel, PercentageCommissionModel, SimulatedFill, SimulatedOrderConnection, SlippageModel
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.domain.account import AccountRuntimeContext, AccountSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderEventKind, OrderStatus
from kairospy.domain.intent import TradeIntent
from kairospy.domain.order import OrderRequest, OrderState
from kairospy.domain.market import Bar, MarketObservation, OrderBookDelta, OrderBookSnapshot, OrderBookSynchronizer, Quote, RateObservation, TradePrint


class SimulatedExecutionRuntimeService:
    def __init__(
        self,
        coordinator: ExecutionCoordinator,
        *,
        account: AccountRuntimeContext | None = None,
        settlement_asset: str = "USD",
        price_field: str,
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        commission_model: CommissionModel | None = None,
        mode_label: str = "simulated",
        directory: AccountDirectory | None = None,
        market_reference: object | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.account = account
        self.directory = directory
        self.settlement_asset = settlement_asset
        self.price_field = price_field
        self.fill_model = fill_model
        self.slippage_model = slippage_model
        self.commission_model = commission_model
        self.mode_label = mode_label
        self._fills: list[SimulatedFill] = []
        self._events: asyncio.Queue[Message] = asyncio.Queue()
        self._event_sequence = 0
        self._canceled_orders: set[str] = set()
        self._connections: dict[str, SimulatedOrderConnection] = {}
        self._order_books: dict[str, OrderBookSynchronizer] = {}
        self._market_prices: dict[str, tuple[Decimal | None, Decimal | None]] = {}
        self.market_reference = market_reference

    @property
    def fills(self) -> tuple[SimulatedFill, ...]:
        return tuple(self._fills)

    async def events(self) -> AsyncIterator[Message]:
        while True:
            message = await self._events.get()
            update = message.payload
            if isinstance(update, ExecutionUpdate):
                if update.kind in {OrderEventKind.ACKNOWLEDGED, OrderEventKind.PARTIALLY_FILLED, OrderEventKind.FILLED} and update.order_id in self._canceled_orders:
                    continue
            yield message

    def execute_intent(self, intent: TradeIntent, context: object) -> SimulatedFill | None:
        account = self._resolve_account(intent)
        if account is None:
            raise RuntimeError(f"{self.mode_label} execution service requires an account before it can execute intents")
        connection = SimulatedOrderConnection(
            coordinator=self.coordinator,
            context=context,
            intent=intent,
            settlement_asset=self.settlement_asset,
            price_field=self.price_field,
            fill_model=self.fill_model or ImmediateFillModel(),
            slippage_model=self.slippage_model or NoSlippageModel(),
            commission_model=self.commission_model or PercentageCommissionModel(),
            emit_update=self._schedule_update,
            record_fill=self._record_fill,
            trading_rules=self._trading_rules_for(intent, context),
        )
        current = self.coordinator.ledger.positions(account.segment).get(str(intent.instrument_id), Decimal("0"))
        delta = (intent.target_quantity or Decimal("0")) - current
        reserve_amount = None
        reserve_currency = None
        if delta > 0:
            price = self._market_price(str(intent.instrument_id), side="buy", limit_price=getattr(intent, "limit_price", None))
            if price is not None:
                reserve_amount = abs(delta) * price
                reserve_currency = self.settlement_asset
        ExecutionApplication.compose(self.coordinator, order_connection=connection).execute_intent(
            ExecuteIntentCommand(
                intent=intent,
                context=context,
                account=account,
                current_quantity=current,
                safety_policy=ExecutionSafetyPolicy(trading_enabled=True, require_limit_orders=False),
                reserve_currency=reserve_currency,
                reserve_amount=reserve_amount,
            )
        )
        if connection.order_id is not None:
            self._connections[connection.order_id] = connection
        return connection.last_fill

    def set_market_reference(self, reference: object | None) -> None:
        self.market_reference = reference

    def _trading_rules_for(self, intent: TradeIntent, context: object) -> TradingRules | None:
        reference = self.market_reference
        market_id = getattr(intent, "market_id", None)
        definition = None
        lookup = getattr(reference, "market_definition", None)
        if reference is not None and market_id is not None and callable(lookup):
            definition = lookup(market_id, at=getattr(context, "now", None) or self._now())
        if definition is None:
            return None
        return TradingRules(
            status=str(getattr(definition, "status", "active")),
            price_tick=getattr(definition, "price_tick", None),
            amount_tick=getattr(definition, "amount_tick", None),
            min_amount=getattr(definition, "min_amount", None),
            min_notional=getattr(definition, "min_notional", None),
        )

    def _now(self) -> datetime:
        return datetime.now().astimezone()

    def on_market_event(self, event: object) -> None:
        value = getattr(event, "value", event)
        self._remember_market_price(value)
        liquidity: dict[tuple[str, str], Decimal] = {}
        if isinstance(value, Quote):
            key = str(value.instrument_id)
            if value.ask_size is not None:
                liquidity[(key, "buy")] = value.ask_size
            if value.bid_size is not None:
                liquidity[(key, "sell")] = value.bid_size
        elif isinstance(value, OrderBookSnapshot):
            key = str(value.instrument_id)
            self._order_books.setdefault(key, OrderBookSynchronizer()).reset(value)
            if value.ask1 is not None:
                liquidity[(key, "buy")] = value.ask1.size
            if value.bid1 is not None:
                liquidity[(key, "sell")] = value.bid1.size
        elif isinstance(value, OrderBookDelta):
            synchronizer = self._order_books.get(str(value.instrument_id))
            if synchronizer is None:
                return
            try:
                value = synchronizer.apply(value).book
            except (ValueError, LookupError):
                return
            event = value
            key = str(value.instrument_id)
            if value.ask1 is not None:
                liquidity[(key, "buy")] = value.ask1.size
            if value.bid1 is not None:
                liquidity[(key, "sell")] = value.bid1.size
        for connection in tuple(self._connections.values()):
            if liquidity:
                state = connection._state
                side = None if state is None else state.side.value
                instrument = None if state is None else str(state.instrument_id)
                available = None if side is None or instrument is None else liquidity.get((instrument, side))
                consumed = connection.on_market_event(event, available_quantity=available)
                if available is not None:
                    liquidity[(instrument, side)] = max(available - consumed, Decimal("0"))
            else:
                connection.on_market_event(event)

    def _market_price(self, instrument_id: str, *, side: str, limit_price: object | None) -> Decimal | None:
        if limit_price is not None:
            return Decimal(str(limit_price))
        bid, ask = self._market_prices.get(instrument_id, (None, None))
        return ask if side == "buy" else bid

    def _remember_market_price(self, value: object) -> None:
        instrument = getattr(value, "instrument_id", None)
        if instrument is None and isinstance(value, MarketObservation):
            subject = value.subject
            instrument = subject.subject_id if subject.subject_type == "instrument" else None
        if instrument is None:
            return
        bid: Decimal | None = None
        ask: Decimal | None = None
        if isinstance(value, Quote):
            bid, ask = value.bid, value.ask
        elif isinstance(value, OrderBookSnapshot):
            bid = None if value.bid1 is None else value.bid1.price
            ask = None if value.ask1 is None else value.ask1.price
        elif isinstance(value, Bar):
            close = value.close
            bid = ask = close
        elif isinstance(value, TradePrint):
            bid = ask = value.price
        elif isinstance(value, RateObservation):
            bid = ask = value.mark_price
        elif isinstance(value, MarketObservation):
            raw = value.payload.get(self.price_field) or value.payload.get("price") or value.payload.get("close")
            if raw is not None:
                bid = ask = Decimal(str(raw))
        if bid is not None or ask is not None:
            previous = self._market_prices.get(str(instrument), (None, None))
            self._market_prices[str(instrument)] = (bid if bid is not None else previous[0], ask if ask is not None else previous[1])

    def _record_fill(self, fill: SimulatedFill) -> None:
        self._fills.append(fill)

    def _schedule_update(self, update: ExecutionUpdate) -> None:
        if update.kind is OrderEventKind.CANCELED and update.order_id:
            self._canceled_orders.add(update.order_id)
        self._event_sequence += 1
        self._events.put_nowait(
            Message(
                topic="execution.update",
                payload=update,
                published_at=update.observed_at,
                producer=f"{self.mode_label}.exchange",
                producer_sequence=self._event_sequence,
                correlation_id=str((update.metadata.get("correlation_id") if isinstance(update.metadata, Mapping) else None) or update.order_id or "") or None,
            )
        )

    def plan_order(
        self,
        request: OrderRequest,
        *,
        reserve_currency: str | None = None,
        reserve_amount: Decimal | None = None,
        margin_notional: Decimal | None = None,
        margin_leverage: Decimal = Decimal("1"),
        margin_instrument_id: str | None = None,
        venue_snapshot: AccountSnapshot | None = None,
        at: datetime,
    ) -> OrderState:
        return self.coordinator.plan_order(
            request,
            reserve_currency=reserve_currency,
            reserve_amount=reserve_amount,
            margin_notional=margin_notional,
            margin_leverage=margin_leverage,
            margin_instrument_id=margin_instrument_id,
            venue_snapshot=venue_snapshot,
            at=at,
        )

    def submit_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        return self.coordinator.submit_order(order_id, at=at)

    def cancel_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.orders.get(order_id)
        if state.status in {OrderStatus.PLANNED, OrderStatus.RESERVED}:
            self._schedule_update(
                ExecutionUpdate(
                    at,
                    OrderEventKind.CANCELED,
                    order_id=state.order_id,
                    order_venue_id=state.order_venue_id or f"simulated.{state.order_id}",
                    context=state.request.context,
                    instrument_id=state.request.instrument_id,
                    market_id=state.request.market_id,
                    side=state.request.side,
                    quantity=state.request.quantity,
                    order_type=state.request.order_type,
                    limit_price=state.request.limit_price,
                    source="simulated_exchange",
                )
            )
            return state
        state = self.coordinator.cancel_order(order_id, at=at)
        connection = self._connections.get(state.order_id)
        if connection is not None and state.order_venue_id:
            connection.cancel(
                ConnectionOrderCancelRequest(
                    account=state.request.context.segment,
                    order_venue_id=state.order_venue_id,
                    symbol=str(state.request.market_id or state.request.instrument_id),
                )
            )
        else:
            self._schedule_update(
                ExecutionUpdate(
                    at,
                    OrderEventKind.CANCELED,
                    order_id=state.order_id,
                    order_venue_id=state.order_venue_id or f"simulated.{state.order_id}",
                    context=state.request.context,
                    instrument_id=state.request.instrument_id,
                    market_id=state.request.market_id,
                    side=state.request.side,
                    quantity=state.request.quantity,
                    order_type=state.request.order_type,
                    limit_price=state.request.limit_price,
                    filled_quantity=state.filled_quantity or None,
                    source="simulated_exchange",
                )
            )
        return state

    def _resolve_account(self, intent: TradeIntent) -> AccountRuntimeContext | None:
        directory = self.directory
        if directory is None:
            return self.account
        return directory.resolve_context(
            account_id=getattr(intent, "account_id", None),
            account_index=getattr(intent, "account_index", None),
            scope=getattr(intent, "account_segment", None),
            environment=None if self.account is None else self.account.environment,
            default=self.account,
        )


__all__ = ["SimulatedExecutionRuntimeService"]

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, overload

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId, IntentId, OrderId, SegmentKey

from .errors import (
    ExecutionAccountNotEnabledError,
    IntentNotFoundError,
    OrderNotFoundError,
)
from .events import ExecutionEvent
from .intents import (
    MakerExecutionPolicy,
    OptionSpreadRequest,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
)
from .mapping import map_execution_event
from .models import (
    BulkOrderCommandReceipt,
    DeliveryCertainty,
    ExecutionIntent,
    IntentReceipt,
    LimitOrderRequest,
    MarketOrderRequest,
    OrderCommitment,
    Order,
    OrderCommandReceipt,
    OrderRequest,
    OrderSide,
    ReplaceOrderRequest,
    RiskReservationSaga,
    SubmissionStatus,
    TimeInForce,
)


class ExecutionApplication:
    """Concrete strategy-facing Execution use cases and projections."""

    def __init__(
        self,
        commands: Any | None,
        projection: Any | None,
        event_source: Any | None = None,
        *,
        strategy_id: str,
        instance_id: str,
        launch_id: str | None = None,
        account_ids: tuple[AccountId, ...] = (),
        disabled_reason: str = "execution is disabled for this launch",
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        self._commands = commands
        self._projection = projection
        self._event_source = event_source
        self._strategy_id = strategy_id
        self._instance_id = instance_id
        self._launch_id = launch_id
        self._account_ids = frozenset(account_ids)
        self._event_cursor = 0
        self._event_source_ready = event_source is None
        self._disabled_reason = disabled_reason
        self._event_sequence: int | None = None
        self._event_time_unix_nanos: int | None = None
        self._request_counter = 0

    def check_event_source_ready(self) -> None:
        """Validate the configured Execution event source without reading mmap."""

        if self._event_source_ready:
            return
        check_ready = getattr(self._event_source, "check_ready", None)
        if callable(check_ready):
            check_ready()
        self._event_source_ready = True

    def commitments(self) -> tuple[OrderCommitment, ...]:
        """Read Execution-owned capacity commitments from the typed mmap view."""
        if self._projection is None:
            return ()
        return tuple(self._projection.commitments())

    def risk_reservations(self) -> tuple[RiskReservationSaga, ...]:
        """Read the persisted Risk reservation saga from the typed mmap view."""
        if self._projection is None:
            return ()
        return tuple(self._projection.risk_reservations())

    async def events(self) -> AsyncIterator[ExecutionEvent]:
        if self._event_source is None:
            return
        cursor = self._event_cursor
        async for record in self._event_source.subscribe_live():
            if record.stream_id != "execution.events":
                raise RuntimeError(
                    f"Execution event stream identity is invalid: {record.stream_id}"
                )
            if self._launch_id is not None and record.launch_id != self._launch_id:
                raise RuntimeError("Execution event belongs to another launch")
            if cursor == 0:
                cursor = record.sequence - 1
            if record.sequence <= cursor:
                continue
            expected = cursor + 1
            if record.sequence != expected:
                raise RuntimeError(
                    "Execution event stream is not contiguous: "
                    f"expected {expected}, received {record.sequence}"
                )
            if record.instance_id != self._instance_id:
                raise RuntimeError("Execution event belongs to another launch instance")
            cursor = record.sequence
            self._event_cursor = cursor
            scoped = tuple(
                change
                for change in record.changes
                if change.strategy_id == self._strategy_id
                and self._change_belongs_to_accounts(change)
            )
            if not scoped:
                continue
            for event in map_execution_event(
                type(record)(
                    record.stream_id,
                    record.sequence,
                    record.producer,
                    record.instance_id,
                    scoped,
                    record.occurred_at_unix_nanos,
                    record.launch_id,
                )
            ):
                yield event

    def _change_belongs_to_accounts(self, change: object) -> bool:
        if not self._account_ids:
            return True
        kind = getattr(change, "kind", "")
        account_id = getattr(change, "account_id", None)
        if kind == "intent_update":
            payload = getattr(change, "payload", None)
            if not isinstance(payload, Mapping):
                raise ValueError("Execution intent scope payload must be an object")
            intent = payload.get("intent")
            if not isinstance(intent, Mapping):
                raise ValueError("Execution intent scope is missing intent payload")
            raw_account_ids = intent.get("account_ids")
            if not isinstance(raw_account_ids, (list, tuple)) or not raw_account_ids:
                raise ValueError("Execution intent scope requires account_ids")
            account_ids = frozenset(AccountId(str(value)) for value in raw_account_ids)
            return account_ids.issubset(self._account_ids)
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError(f"Execution {kind or 'change'} scope requires account_id")
        return AccountId(account_id) in self._account_ids

    def bind_event(
        self,
        sequence: int | None,
        occurred_at_unix_nanos: int | None,
    ) -> None:
        """Bind command causation to the currently dispatched strategy event."""

        self._event_sequence = sequence
        self._event_time_unix_nanos = occurred_at_unix_nanos

    def target_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        *,
        account: AccountId | str,
        segment: SegmentKey | str = "spot",
        limit_price: Decimal | None = None,
        reason: str = "",
        intent_id: IntentId | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        request_id = self._request_id("intent.target_position")
        scope_error = self._account_scope_error((account,))
        if scope_error is not None:
            return self._rejected_intent(request_id, scope_error)
        if self._commands is None:
            return self._rejected_intent(request_id)
        request = TargetPositionRequest(
            instrument_id=str(_instrument_id(instrument)),
            quantity=quantity,
            account_id=str(account),
            segment_key=str(segment),
            limit_price=limit_price,
            reason=reason,
            intent_id=None if intent_id is None else str(intent_id),
            source_event_sequence=self._event_sequence,
            source_event_time_unix_nanos=self._event_time_unix_nanos,
            split=split,
            maker=maker,
        )
        return _intent_receipt(
            self._commands.target_position(request, **self._identity(request_id))
        )

    def close_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        account: AccountId | str,
        segment: SegmentKey | str = "spot",
        reason: str = "",
        intent_id: IntentId | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        return self.target_position(
            instrument,
            Decimal("0"),
            account=account,
            segment=segment,
            reason=reason,
            intent_id=intent_id,
            split=split,
            maker=maker,
        )

    def cancel_intent(self, intent_id: IntentId, *, reason: str = "") -> IntentReceipt:
        request_id = self._request_id("intent.cancel")
        if self._commands is None:
            return self._rejected_intent(request_id)
        if self.intent(intent_id) is None:
            return self._rejected_intent(
                request_id,
                f"Execution intent '{intent_id}' was not found or is not owned",
            )
        return _intent_receipt(
            self._commands.cancel_intent(
                str(intent_id), reason=reason, **self._identity(request_id)
            )
        )

    def submit_order(self, request: OrderRequest) -> OrderCommandReceipt:
        request_id = self._request_id("execution.submit_order")
        scope_error = self._account_scope_error((request.account,))
        if scope_error is not None:
            return self._rejected_order(request_id, scope_error)
        if self._commands is None:
            return self._rejected_order(request_id)
        return _order_receipt(
            self._commands.submit_order(request, **self._identity(request_id))
        )

    def market_order(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        *,
        account: AccountId | str,
        side: OrderSide,
        segment: SegmentKey | str = "spot",
        time_in_force: TimeInForce = TimeInForce.IOC,
        reduce_only: bool = False,
        reason: str = "",
    ) -> OrderCommandReceipt:
        """Submit one explicit market order through the canonical order path."""

        return self.submit_order(
            MarketOrderRequest(
                instrument,
                account,
                side,
                quantity,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                reason=reason,
                segment=segment,
            )
        )

    def limit_order(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        price: Decimal,
        *,
        account: AccountId | str,
        side: OrderSide,
        segment: SegmentKey | str = "spot",
        time_in_force: TimeInForce = TimeInForce.DAY,
        post_only: bool = False,
        reduce_only: bool = False,
        reason: str = "",
    ) -> OrderCommandReceipt:
        """Submit one explicit limit order through the canonical order path."""

        return self.submit_order(
            LimitOrderRequest(
                instrument,
                account,
                side,
                quantity,
                price,
                time_in_force=time_in_force,
                post_only=post_only,
                reduce_only=reduce_only,
                reason=reason,
                segment=segment,
            )
        )

    @overload
    def execute(self, request: PairArbitrageRequest) -> IntentReceipt: ...

    @overload
    def execute(self, request: PortfolioRebalanceRequest) -> IntentReceipt: ...

    @overload
    def execute(self, request: QuoteProvisioningRequest) -> IntentReceipt: ...

    @overload
    def execute(self, request: OptionSpreadRequest) -> IntentReceipt: ...

    def execute(
        self,
        request: PairArbitrageRequest
        | PortfolioRebalanceRequest
        | QuoteProvisioningRequest
        | OptionSpreadRequest,
    ) -> IntentReceipt:
        """Submit a typed advanced intent without exposing transport details."""

        if isinstance(request, PairArbitrageRequest):
            return self.pair_arbitrage(request)
        if isinstance(request, PortfolioRebalanceRequest):
            return self.portfolio_rebalance(request)
        if isinstance(request, QuoteProvisioningRequest):
            return self.quote_provisioning(request)
        if isinstance(request, OptionSpreadRequest):
            return self.option_spread(request)
        raise TypeError(f"unsupported Execution request: {type(request).__name__}")

    def pair_arbitrage(self, request: PairArbitrageRequest) -> IntentReceipt:
        request_id, rejected = self._prepare_advanced_intent(
            "intent.pair_arbitrage",
            (request.first.account_id, request.second.account_id),
        )
        if rejected is not None:
            return rejected
        assert self._commands is not None
        return _intent_receipt(
            self._commands.pair_arbitrage(request, **self._identity(request_id))
        )

    def portfolio_rebalance(self, request: PortfolioRebalanceRequest) -> IntentReceipt:
        request_id, rejected = self._prepare_advanced_intent(
            "intent.portfolio_rebalance",
            tuple(target.account_id for target in request.targets),
        )
        if rejected is not None:
            return rejected
        assert self._commands is not None
        return _intent_receipt(
            self._commands.portfolio_rebalance(request, **self._identity(request_id))
        )

    def quote_provisioning(self, request: QuoteProvisioningRequest) -> IntentReceipt:
        request_id, rejected = self._prepare_advanced_intent(
            "intent.quote_provisioning",
            (request.account_id,),
        )
        if rejected is not None:
            return rejected
        assert self._commands is not None
        return _intent_receipt(
            self._commands.quote_provisioning(request, **self._identity(request_id))
        )

    def option_spread(self, request: OptionSpreadRequest) -> IntentReceipt:
        """Submit one fixed-risk, all-or-nothing option spread Intent.

        The two legs remain one Execution-owned intent.  This method only
        adapts the typed public request; fill atomicity and lifecycle remain
        owned by the Execution application.
        """

        request_id, rejected = self._prepare_advanced_intent(
            "intent.option_spread", (request.account_id,)
        )
        if rejected is not None:
            return rejected
        assert self._commands is not None
        return _intent_receipt(
            self._commands.option_spread(request, **self._identity(request_id))
        )

    def refresh_quote(self, request: QuoteRefreshRequest) -> IntentReceipt:
        request_id = self._request_id("intent.refresh_quote")
        if self._commands is None:
            return self._rejected_intent(request_id)
        if self.intent(IntentId(request.intent_id)) is None:
            return self._rejected_intent(
                request_id,
                f"Execution intent '{request.intent_id}' was not found or is not owned",
            )
        return _intent_receipt(
            self._commands.refresh_quote(request, **self._identity(request_id))
        )

    def _prepare_advanced_intent(
        self,
        operation: str,
        accounts: tuple[AccountId | str, ...],
    ) -> tuple[str, IntentReceipt | None]:
        request_id = self._request_id(operation)
        scope_error = self._account_scope_error(accounts)
        if scope_error is not None:
            return request_id, self._rejected_intent(request_id, scope_error)
        if self._commands is None:
            return request_id, self._rejected_intent(request_id)
        return request_id, None

    def cancel_order(
        self, order_id: OrderId, *, reason: str = ""
    ) -> OrderCommandReceipt:
        request_id = self._request_id("execution.cancel_order")
        if self._commands is None:
            return self._rejected_order(request_id)
        if self.order(order_id) is None:
            return self._rejected_order(
                request_id,
                f"Execution order '{order_id}' was not found or is not owned",
            )
        return _order_receipt(
            self._commands.cancel_order(
                str(order_id), reason=reason, **self._identity(request_id)
            )
        )

    def replace_order(
        self, order_id: OrderId, request: ReplaceOrderRequest
    ) -> OrderCommandReceipt:
        request_id = self._request_id("execution.replace_order")
        if self._commands is None:
            return self._rejected_order(request_id)
        if self.order(order_id) is None:
            return self._rejected_order(
                request_id,
                f"Execution order '{order_id}' was not found or is not owned",
            )
        return _order_receipt(
            self._commands.replace_order(
                str(order_id), request, **self._identity(request_id)
            )
        )

    def cancel_all(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        account: AccountId | str | None = None,
        reason: str = "",
    ) -> BulkOrderCommandReceipt:
        if instrument is None and account is None:
            raise ValueError("cancel_all requires an instrument or account scope")
        request_id = self._request_id("execution.cancel_all")
        if account is not None:
            scope_error = self._account_scope_error((account,))
            if scope_error is not None:
                return BulkOrderCommandReceipt(
                    request_id,
                    (),
                    SubmissionStatus.REJECTED,
                    DeliveryCertainty.NOT_SENT,
                    scope_error,
                )
        if self._commands is None:
            return BulkOrderCommandReceipt(
                request_id,
                (),
                SubmissionStatus.REJECTED,
                DeliveryCertainty.NOT_SENT,
                self._disabled_reason,
            )
        result = self._commands.cancel_all(
            instrument_id=None
            if instrument is None
            else str(_instrument_id(instrument)),
            account_id=None if account is None else str(account),
            reason=reason,
            **self._identity(request_id),
        )
        return _bulk_receipt(result)

    def intent(self, intent_id: IntentId) -> ExecutionIntent | None:
        if self._projection is None:
            return None
        intent = self._projection.get_intent(str(intent_id))
        if intent is None:
            return None
        return (
            intent
            if intent.strategy_id == self._strategy_id
            and self._accounts_are_enabled(intent.account_ids)
            else None
        )

    def require_intent(self, intent_id: IntentId) -> ExecutionIntent:
        value = self.intent(intent_id)
        if value is None:
            raise IntentNotFoundError(intent_id)
        return value

    def order(self, order_id: OrderId) -> Order | None:
        if self._projection is None:
            return None
        order = self._projection.get_order(str(order_id))
        if (
            order is None
            or order.strategy_id != self._strategy_id
            or not self._accounts_are_enabled((order.account_id,))
        ):
            return None
        return order

    def require_order(self, order_id: OrderId) -> Order:
        value = self.order(order_id)
        if value is None:
            raise OrderNotFoundError(order_id)
        return value

    def open_orders(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        account: AccountId | str | None = None,
    ) -> tuple[Order, ...]:
        if self._projection is None:
            return ()
        orders = self._projection.open_orders(
            account_id=None if account is None else str(account)
        )
        orders = tuple(
            order
            for order in orders
            if order.strategy_id == self._strategy_id
            and self._accounts_are_enabled((order.account_id,))
        )
        instrument_id = None if instrument is None else _instrument_id(instrument)
        return (
            orders
            if instrument_id is None
            else tuple(
                order for order in orders if order.instrument.id == instrument_id
            )
        )

    def for_account(
        self,
        account: AccountId | str,
        *,
        segment: SegmentKey | str = "spot",
    ) -> AccountExecution:
        """Bind common single-account arguments without narrowing root capabilities."""

        account_id = account if isinstance(account, AccountId) else AccountId(account)
        if not self._accounts_are_enabled((account_id,)):
            raise ExecutionAccountNotEnabledError(account_id)
        return AccountExecution(self, account_id, SegmentKey(str(segment)))

    def _accounts_are_enabled(self, accounts: tuple[AccountId | str, ...]) -> bool:
        if not self._account_ids:
            return True
        requested = frozenset(
            value if isinstance(value, AccountId) else AccountId(value)
            for value in accounts
        )
        return requested.issubset(self._account_ids)

    def _account_scope_error(self, accounts: tuple[AccountId | str, ...]) -> str | None:
        if not self._account_ids:
            return None
        for value in accounts:
            account_id = value if isinstance(value, AccountId) else AccountId(value)
            if account_id not in self._account_ids:
                return str(ExecutionAccountNotEnabledError(account_id))
        return None

    def _request_id(self, operation: str) -> str:
        self._request_counter += 1
        return (
            f"{self._strategy_id}:{self._instance_id}:{operation}:"
            f"{self._event_sequence or 0}:{self._request_counter}"
        )

    def _identity(self, request_id: str) -> dict[str, str]:
        return {
            "strategy_id": self._strategy_id,
            "instance_id": self._instance_id,
            "request_id": request_id,
        }

    def _rejected_intent(
        self, request_id: str, error: str | None = None
    ) -> IntentReceipt:
        return IntentReceipt(
            request_id,
            None,
            SubmissionStatus.REJECTED,
            DeliveryCertainty.NOT_SENT,
            error or self._disabled_reason,
        )

    def _rejected_order(
        self, request_id: str, error: str | None = None
    ) -> OrderCommandReceipt:
        return OrderCommandReceipt(
            request_id,
            None,
            None,
            SubmissionStatus.REJECTED,
            DeliveryCertainty.NOT_SENT,
            error or self._disabled_reason,
        )


@dataclass(frozen=True, slots=True)
class AccountExecution:
    """Optional immutable single-account convenience view over Execution."""

    _application: ExecutionApplication
    account_id: AccountId
    segment_key: SegmentKey

    def target_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        *,
        limit_price: Decimal | None = None,
        reason: str = "",
        intent_id: IntentId | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        return self._application.target_position(
            instrument,
            quantity,
            account=self.account_id,
            segment=self.segment_key,
            limit_price=limit_price,
            reason=reason,
            intent_id=intent_id,
            split=split,
            maker=maker,
        )

    def close_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        reason: str = "",
        intent_id: IntentId | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        return self._application.close_position(
            instrument,
            account=self.account_id,
            segment=self.segment_key,
            reason=reason,
            intent_id=intent_id,
            split=split,
            maker=maker,
        )

    def market_order(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        *,
        side: OrderSide,
        time_in_force: TimeInForce = TimeInForce.IOC,
        reduce_only: bool = False,
        reason: str = "",
    ) -> OrderCommandReceipt:
        return self._application.market_order(
            instrument,
            quantity,
            account=self.account_id,
            segment=self.segment_key,
            side=side,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            reason=reason,
        )

    def limit_order(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        price: Decimal,
        *,
        side: OrderSide,
        time_in_force: TimeInForce = TimeInForce.DAY,
        post_only: bool = False,
        reduce_only: bool = False,
        reason: str = "",
    ) -> OrderCommandReceipt:
        return self._application.limit_order(
            instrument,
            quantity,
            price,
            account=self.account_id,
            segment=self.segment_key,
            side=side,
            time_in_force=time_in_force,
            post_only=post_only,
            reduce_only=reduce_only,
            reason=reason,
        )

    def cancel_all(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        reason: str = "",
    ) -> BulkOrderCommandReceipt:
        return self._application.cancel_all(
            instrument=instrument,
            account=self.account_id,
            reason=reason,
        )

    def open_orders(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
    ) -> tuple[Order, ...]:
        return self._application.open_orders(
            instrument=instrument,
            account=self.account_id,
        )


def _instrument_id(value: InstrumentRef | InstrumentId) -> InstrumentId:
    return value.id if isinstance(value, InstrumentRef) else value


def _submission_status(value: Any) -> SubmissionStatus:
    status = value.status.lower()
    return (
        SubmissionStatus(status)
        if status in SubmissionStatus._value2member_map_
        else SubmissionStatus.REJECTED
    )


def _certainty(value: Any) -> DeliveryCertainty:
    return (
        DeliveryCertainty.NOT_SENT
        if _submission_status(value) is SubmissionStatus.REJECTED
        else DeliveryCertainty.SENT
    )


def _result_id(value: Any, name: str, id_type):
    raw = value.result.get(name)
    if raw is None:
        nested = value.result.get("result")
        raw = nested.get(name) if isinstance(nested, Mapping) else None
    return id_type(raw) if isinstance(raw, str) and raw.strip() else None


def _intent_receipt(value: Any) -> IntentReceipt:
    return IntentReceipt(
        request_id=value.request_id,
        intent_id=_result_id(value, "intent_id", IntentId),
        status=_submission_status(value),
        delivery_certainty=_certainty(value),
        error=value.error,
    )


def _order_receipt(value: Any) -> OrderCommandReceipt:
    return OrderCommandReceipt(
        request_id=value.request_id,
        order_id=_result_id(value, "order_id", OrderId),
        intent_id=_result_id(value, "intent_id", IntentId),
        status=_submission_status(value),
        delivery_certainty=_certainty(value),
        error=value.error,
    )


def _bulk_receipt(value: Any) -> BulkOrderCommandReceipt:
    raw_ids = value.result.get("order_ids", ())
    if not isinstance(raw_ids, (list, tuple)):
        raw_ids = ()
    return BulkOrderCommandReceipt(
        request_id=value.request_id,
        order_ids=tuple(OrderId(item) for item in raw_ids if isinstance(item, str)),
        status=_submission_status(value),
        delivery_certainty=_certainty(value),
        error=value.error,
    )

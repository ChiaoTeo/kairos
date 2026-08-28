from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, cast, overload

from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.execution import IntentId, OrderId
from kairospy.primitives.reference import InstrumentId
from kairospy.primitives.decimal import Price, Quantity

from .errors import (
    ExecutionAccountNotEnabledError,
    IntentNotFoundError,
    OrderNotFoundError,
)
from kairospy.infrastructure.contracts.execution.events import ExecutionEvent
from .intents import (
    ExecutionAlgorithmPolicy,
    MakerExecutionPolicy,
    OptionSpreadRequest,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
)
from .mapping import (
    map_execution_intent,
    map_execution_order,
    map_order_commitment,
    map_risk_reservation,
)
from .models import (
    BulkOrderCommandReceipt,
    DeliveryCertainty,
    ExecutionIntent,
    Fill,
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
from ..services import ExecutionEventCursorCheckpoint


class ExecutionApplication:
    """Concrete strategy-facing Execution use cases and current views."""

    def __init__(
        self,
        commands: Any | None,
        current_views: Any | None,
        event_source: Any | None = None,
        *,
        strategy_id: str,
        instance_id: str,
        launch_id: str | None = None,
        account_ids: tuple[AccountId, ...] = (),
        disabled_reason: str = "execution is disabled for this launch",
        cursor_checkpoint: ExecutionEventCursorCheckpoint | None = None,
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        self._commands = commands
        self._current_views = current_views
        self._event_source = event_source
        self._strategy_id = strategy_id
        self._instance_id = instance_id
        self._launch_id = launch_id
        self._account_ids = frozenset(account_ids)
        self._cursor_checkpoint = cursor_checkpoint
        cursor_position = (
            None if cursor_checkpoint is None else cursor_checkpoint.load_position()
        )
        self._event_cursor = 0 if cursor_position is None else cursor_position.sequence
        self._event_cursor_key: tuple[str, str, int] | None = None
        if (
            cursor_position is not None
            and cursor_position.producer is not None
            and cursor_position.producer_incarnation is not None
        ):
            self._event_cursor_key = (
                "execution.events",
                cursor_position.producer,
                cursor_position.producer_incarnation,
            )
        self._durable_event_cursor = self._event_cursor
        self._event_head_sequence = self._event_cursor
        self._event_source_ready = event_source is None
        self._event_gap_count = 0
        self._event_scope_error_count = 0
        self._notification_incarnation_change_count = 0
        self._disabled_reason = disabled_reason
        self._event_sequence: int | None = None
        self._event_time_unix_nanos: int | None = None
        self._request_counter = 0
        self._decision_application: Any | None = None

    def bind_decisions(self, decisions: Any) -> None:
        """Bind the Strategy-owned decision tracker for this process instance."""

        self._decision_application = decisions

    def check_event_source_ready(self) -> None:
        """Validate the configured Execution event source without reading current state."""

        if self._event_source_ready:
            return
        check_ready = getattr(self._event_source, "check_ready", None)
        if callable(check_ready):
            check_ready()
        self._event_source_ready = True

    def commitments(self) -> tuple[OrderCommitment, ...]:
        """Read Execution-owned capacity commitments from the indexed view."""
        if self._current_views is None:
            return ()
        return tuple(map_order_commitment(value) for value in self._current_views.commitments())

    def risk_reservations(self) -> tuple[RiskReservationSaga, ...]:
        """Read the persisted Risk reservation saga from the indexed view."""
        if self._current_views is None:
            return ()
        return tuple(map_risk_reservation(value) for value in self._current_views.risk_reservations())

    def diagnostic_intent(self, intent_id: IntentId | str) -> ExecutionIntent | None:
        """Read one authoritative Execution trace from the current v2 view."""

        if self._current_views is None:
            return None
        value = self._current_views.get_intent(str(intent_id))
        if value is None:
            return None
        intent = map_execution_intent(value)
        if intent.strategy_id != self._strategy_id:
            return None
        if self._account_ids and not set(intent.account_ids).issubset(self._account_ids):
            return None
        return intent

    async def events(self) -> AsyncIterator[ExecutionEvent]:
        if self._event_source is None:
            return
        cursor = self._event_cursor
        async for record in self._event_source.subscribe_live():
            from kairospy.infrastructure.contracts.execution.events import (
                ExecutionEvent as NativeExecutionEvent,
            )

            if not isinstance(record, NativeExecutionEvent):
                raise TypeError(
                    "Execution event source must yield owner-native ExecutionEvent values"
                )
            if record.stream_id != "execution.events":
                self._event_scope_error_count += 1
                raise RuntimeError(
                    f"Execution event stream identity is invalid: {record.stream_id}"
                )
            if self._launch_id is not None and record.launch_id != self._launch_id:
                self._event_scope_error_count += 1
                raise RuntimeError("Execution event belongs to another launch")
            if record.instance_id != self._instance_id:
                self._event_scope_error_count += 1
                raise RuntimeError("Execution event belongs to another launch instance")
            cursor_key = (
                record.stream_id,
                str(record.producer),
                int(record.producer_incarnation),
            )
            if self._event_cursor_key is not None and cursor_key != self._event_cursor_key:
                self._notification_incarnation_change_count += 1
                cursor = record.sequence - 1
            elif self._event_cursor_key is None:
                cursor = record.sequence - 1
            self._event_cursor_key = cursor_key
            if cursor == 0:
                cursor = record.sequence - 1
            if record.sequence <= cursor:
                continue
            if record.sequence != cursor + 1:
                self._event_gap_count += 1
            cursor = record.sequence
            self._event_cursor = cursor
            self._event_head_sequence = max(self._event_head_sequence, cursor)
            if (
                record.strategy_id != self._strategy_id
                or not self._change_belongs_to_accounts(record)
            ):
                self._checkpoint_cursor(cursor, cursor_key)
                continue
            if record.kind != "plan_created":
                yield record
            self._checkpoint_cursor(cursor, cursor_key)

    def health(self) -> dict[str, object]:
        """Return process-local event consumption diagnostics."""

        return {
            "event_source_ready": self._event_source_ready,
            "event_cursor": self._durable_event_cursor,
            "processing_event_cursor": self._event_cursor,
            "event_lag": max(0, self._event_head_sequence - self._durable_event_cursor),
            "event_gap_count": self._event_gap_count,
            "event_scope_error_count": self._event_scope_error_count,
            "notification_incarnation_change_count": (
                self._notification_incarnation_change_count
            ),
        }

    def _checkpoint_cursor(
        self, sequence: int, cursor_key: tuple[str, str, int] | None = None
    ) -> None:
        cursor_key = self._event_cursor_key if cursor_key is None else cursor_key
        if self._cursor_checkpoint is not None:
            self._cursor_checkpoint.save(
                sequence,
                producer=None if cursor_key is None else cursor_key[1],
                producer_incarnation=None if cursor_key is None else cursor_key[2],
            )
        self._durable_event_cursor = sequence

    def _change_belongs_to_accounts(self, change: object) -> bool:
        if not self._account_ids:
            return True
        kind = getattr(change, "kind", "")
        account_id = getattr(change, "account_id", None)
        if kind == "intent_update":
            payload = getattr(change, "data", None)
            intent = getattr(payload, "intent", None)
            if intent is None:
                raise ValueError("Execution intent scope is missing intent payload")
            raw_account_ids = getattr(intent, "account_ids", None)
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
        quantity: Quantity,
        *,
        account: AccountId | str,
        algorithm: ExecutionAlgorithmPolicy,
        segment: SegmentKey | str = "spot",
        limit_price: Price | None = None,
        reason: str = "",
        intent_id: IntentId | None = None,
        strategy_decision_id: str | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        request_id = self._request_id("intent.target_position")
        if self._commands is None:
            return self._rejected_intent(request_id)
        decision_error = self._decision_error(strategy_decision_id)
        if decision_error is not None:
            return self._rejected_intent(request_id, decision_error)
        scope_error = self._account_scope_error((account,))
        if scope_error is not None:
            return self._rejected_intent(request_id, scope_error)
        request = TargetPositionRequest(
            instrument_id=str(_instrument_id(instrument)),
            quantity=quantity,
            algorithm=algorithm,
            account_id=str(account),
            segment_key=str(segment),
            limit_price=limit_price,
            reason=reason,
            intent_id=None if intent_id is None else str(intent_id),
            strategy_decision_id=strategy_decision_id,
            source_event_sequence=self._event_sequence,
            source_event_time_unix_nanos=self._event_time_unix_nanos,
            split=split,
            maker=maker,
        )
        receipt = _intent_receipt(
            self._commands.target_position(request, **self._identity(request_id))
        )
        self._attach_decision(receipt, strategy_decision_id)
        return receipt

    def close_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        account: AccountId | str,
        algorithm: ExecutionAlgorithmPolicy,
        segment: SegmentKey | str = "spot",
        reason: str = "",
        intent_id: IntentId | None = None,
        strategy_decision_id: str | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        return self.target_position(
            instrument,
            Quantity("0"),
            account=account,
            algorithm=algorithm,
            segment=segment,
            reason=reason,
            intent_id=intent_id,
            strategy_decision_id=strategy_decision_id,
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
        quantity: Quantity,
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
        quantity: Quantity,
        price: Price,
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
        decision_error = self._decision_error(request.strategy_decision_id)
        if decision_error is not None:
            return self._rejected_intent(request_id, decision_error)
        assert self._commands is not None
        receipt = _intent_receipt(
            self._commands.pair_arbitrage(request, **self._identity(request_id))
        )
        self._attach_decision(receipt, request.strategy_decision_id)
        return receipt

    def portfolio_rebalance(self, request: PortfolioRebalanceRequest) -> IntentReceipt:
        request_id, rejected = self._prepare_advanced_intent(
            "intent.portfolio_rebalance",
            tuple(target.account_id for target in request.targets),
        )
        if rejected is not None:
            return rejected
        decision_error = self._decision_error(request.strategy_decision_id)
        if decision_error is not None:
            return self._rejected_intent(request_id, decision_error)
        assert self._commands is not None
        receipt = _intent_receipt(
            self._commands.portfolio_rebalance(request, **self._identity(request_id))
        )
        self._attach_decision(receipt, request.strategy_decision_id)
        return receipt

    def quote_provisioning(self, request: QuoteProvisioningRequest) -> IntentReceipt:
        request_id, rejected = self._prepare_advanced_intent(
            "intent.quote_provisioning",
            (request.account_id,),
        )
        if rejected is not None:
            return rejected
        decision_error = self._decision_error(request.strategy_decision_id)
        if decision_error is not None:
            return self._rejected_intent(request_id, decision_error)
        assert self._commands is not None
        receipt = _intent_receipt(
            self._commands.quote_provisioning(request, **self._identity(request_id))
        )
        self._attach_decision(receipt, request.strategy_decision_id)
        return receipt

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
        decision_error = self._decision_error(request.strategy_decision_id)
        if decision_error is not None:
            return self._rejected_intent(request_id, decision_error)
        assert self._commands is not None
        receipt = _intent_receipt(
            self._commands.option_spread(request, **self._identity(request_id))
        )
        self._attach_decision(receipt, request.strategy_decision_id)
        return receipt

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
        if self._current_views is None:
            return None
        intent = self._current_views.get_intent(str(intent_id))
        if intent is None:
            return None
        if not isinstance(intent, ExecutionIntent):
            intent = map_execution_intent(intent)
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
        if self._current_views is None:
            return None
        order = self._current_views.get_order(str(order_id))
        if order is not None and not isinstance(order, Order):
            order = map_execution_order(order)
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
        if self._current_views is None:
            return ()
        terminal = {"filled", "canceled", "rejected", "expired", "failed"}
        account_id = None if account is None else str(account)
        orders = tuple(
            map_execution_order(order)
            for order in self._current_views.orders()
            if getattr(order, "status") not in terminal
            and (account_id is None or getattr(order, "account_id") == account_id)
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

    def _decision_error(self, strategy_decision_id: str | None) -> str | None:
        if self._decision_application is None:
            return None
        if strategy_decision_id is None or not strategy_decision_id.strip():
            return "Strategy Intent requires strategy_decision_id"
        if self._decision_application.decision(strategy_decision_id) is None:
            return f"Strategy decision not found: {strategy_decision_id}"
        return None

    def _attach_decision(
        self, receipt: IntentReceipt, strategy_decision_id: str | None
    ) -> None:
        if (
            self._decision_application is not None
            and strategy_decision_id is not None
            and receipt.accepted
            and receipt.intent_id is not None
        ):
            self._decision_application.attach_intent(
                strategy_decision_id, str(receipt.intent_id)
            )

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
        quantity: Quantity,
        *,
        algorithm: ExecutionAlgorithmPolicy,
        limit_price: Price | None = None,
        reason: str = "",
        intent_id: IntentId | None = None,
        strategy_decision_id: str | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        return self._application.target_position(
            instrument,
            quantity,
            account=self.account_id,
            algorithm=algorithm,
            segment=self.segment_key,
            limit_price=limit_price,
            reason=reason,
            intent_id=intent_id,
            strategy_decision_id=strategy_decision_id,
            split=split,
            maker=maker,
        )

    def close_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        algorithm: ExecutionAlgorithmPolicy,
        reason: str = "",
        intent_id: IntentId | None = None,
        strategy_decision_id: str | None = None,
        split: SplitOrderPolicy | None = None,
        maker: MakerExecutionPolicy | None = None,
    ) -> IntentReceipt:
        return self._application.close_position(
            instrument,
            account=self.account_id,
            algorithm=algorithm,
            segment=self.segment_key,
            reason=reason,
            intent_id=intent_id,
            strategy_decision_id=strategy_decision_id,
            split=split,
            maker=maker,
        )

    def market_order(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Quantity,
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
        quantity: Quantity,
        price: Price,
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
    status = _submission_status(value)
    return (
        DeliveryCertainty.SENT
        if status in {SubmissionStatus.ACCEPTED, SubmissionStatus.DUPLICATE}
        else DeliveryCertainty.NOT_SENT
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

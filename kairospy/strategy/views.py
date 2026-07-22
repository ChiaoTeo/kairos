from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from kairospy.identity import InstrumentId


@dataclass(frozen=True, slots=True)
class ViewFieldSchema:
    name: str
    semantic: str
    time_semantics: str
    evidence: str


@dataclass(frozen=True, slots=True)
class ViewSchema:
    view: str
    owner: str
    field_schemas: tuple[ViewFieldSchema, ...]
    forbidden_dependencies: tuple[str, ...] = ()

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.field_schemas)

    @property
    def schema_hash(self) -> str:
        return _hash(self)


class _ViewContract:
    __slots__ = ()

    @classmethod
    def schema(cls) -> ViewSchema:
        return view_schema(cls.__name__)

    @property
    def view_hash(self) -> str:
        return view_hash(self)


@dataclass(frozen=True, slots=True)
class MarketView(_ViewContract):
    timestamp: datetime
    sequence: int
    instruments: tuple[InstrumentId, ...]
    data_binding: str = "unknown"
    event_window: tuple[datetime, datetime] | None = None
    available_instruments: tuple[InstrumentId, ...] = ()
    reference_prices: tuple[tuple[InstrumentId, Decimal], ...] = ()
    quality_codes: tuple[str, ...] = ()
    snapshot_span_seconds: Decimal = Decimal("0")
    available_time: datetime | None = None
    freshness_seconds: Decimal | None = None

    def __post_init__(self) -> None:
        if self.available_time is None:
            object.__setattr__(self, "available_time", self.timestamp)
        if self.event_window is None:
            start = self.timestamp - timedelta(seconds=float(self.snapshot_span_seconds))
            object.__setattr__(self, "event_window", (start, self.timestamp))
        if self.freshness_seconds is None and self.available_time is not None:
            object.__setattr__(self, "freshness_seconds", Decimal(str((self.available_time - self.timestamp).total_seconds())))

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "MarketView":
        instruments = tuple(item.instrument_id for item in getattr(snapshot, "instruments", ()))
        available = tuple(getattr(snapshot, "instrument_universe", ()) or instruments)
        quality_codes = tuple(str(getattr(item, "code", item)) for item in getattr(snapshot, "quality_issues", ()))
        return cls(
            getattr(snapshot, "timestamp"),
            int(getattr(snapshot, "sequence", 0)),
            instruments,
            getattr(snapshot, "data_binding", "unknown"),
            getattr(snapshot, "event_window", None),
            available,
            tuple(getattr(snapshot, "reference_prices", ())),
            quality_codes,
            getattr(snapshot, "snapshot_span_seconds", Decimal("0")),
            getattr(snapshot, "available_time", getattr(snapshot, "timestamp")),
            getattr(snapshot, "freshness_seconds", None),
        )


@dataclass(frozen=True, slots=True)
class BalanceView:
    account: str
    asset: str
    total: Decimal
    available: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    borrowed: Decimal = Decimal("0")
    interest: Decimal = Decimal("0")
    collateral: Decimal = Decimal("0")

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "BalanceView":
        return cls(
            _identity_value(getattr(snapshot, "account", "")),
            _identity_value(getattr(snapshot, "asset", "")),
            getattr(snapshot, "total", Decimal("0")),
            getattr(snapshot, "available", Decimal("0")),
            getattr(snapshot, "locked", Decimal("0")),
            getattr(snapshot, "borrowed", Decimal("0")),
            getattr(snapshot, "interest", Decimal("0")),
            getattr(snapshot, "collateral", Decimal("0")),
        )


@dataclass(frozen=True, slots=True)
class PositionView:
    instrument_id: InstrumentId
    quantity: Decimal
    account: str | None = None
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    market_value_mid: Decimal | None = None
    market_value_liquidation: Decimal | None = None
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl_mid: Decimal | None = None
    valuation_asset: str | None = None
    mark_source: str = "unknown"

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "PositionView":
        mark_price = getattr(snapshot, "mark_price", getattr(snapshot, "mark_mid", None))
        market_value_mid = getattr(snapshot, "market_value_mid", getattr(snapshot, "market_value_reporting", None))
        unrealized_pnl_mid = getattr(snapshot, "unrealized_pnl_mid", getattr(snapshot, "unrealized_pnl_reporting", None))
        return cls(
            getattr(snapshot, "instrument_id"),
            getattr(snapshot, "quantity"),
            _optional_identity_value(getattr(snapshot, "account", None)),
            getattr(snapshot, "average_price", None),
            mark_price,
            market_value_mid,
            getattr(snapshot, "market_value_liquidation", None),
            getattr(snapshot, "realized_pnl", getattr(snapshot, "realized_pnl_native", Decimal("0"))),
            unrealized_pnl_mid,
            _optional_identity_value(getattr(snapshot, "valuation_asset", None)),
            getattr(snapshot, "mark_source", "accounting_mark" if mark_price is not None else "unpriced"),
        )


@dataclass(frozen=True, slots=True)
class PortfolioView(_ViewContract):
    timestamp: datetime | None = None
    reporting_asset: str | None = None
    accounts: tuple[str, ...] = ()
    balances: tuple[BalanceView, ...] = ()
    cash: Decimal = Decimal("0")
    equity_mid: Decimal = Decimal("0")
    equity_liquidation: Decimal = Decimal("0")
    positions: tuple[PositionView, ...] = ()
    open_structure_count: int = 0
    valuation_status: str = "unknown"
    unpriced_assets: tuple[str, ...] = ()
    unpriced_positions: tuple[str, ...] = ()
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    greeks_coverage: Decimal | None = None
    max_theoretical_risk: Decimal | None = None
    margin_requirement: Decimal | None = None
    buying_power: Decimal | None = None
    ledger_transaction_count: int = 0
    ledger_entry_count: int = 0
    last_ledger_at: datetime | None = None
    account_state_at: datetime | None = None
    market_state_at: datetime | None = None
    ledger_hash: str = "none"
    state_hash: str = "none"

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Any,
        *,
        timestamp: datetime | None = None,
        ledger: Any | None = None,
        account_states: tuple[Any, ...] = (),
        market_view: Any | None = None,
    ) -> "PortfolioView":
        view_time = getattr(snapshot, "timestamp", timestamp)
        reporting_asset = _optional_identity_value(getattr(snapshot, "reporting_asset", None))
        balances = tuple(BalanceView.from_snapshot(item) for item in getattr(snapshot, "balances", ()))
        positions = tuple(PositionView.from_snapshot(item) for item in getattr(snapshot, "positions", ()))
        equity = getattr(snapshot, "equity_mid", getattr(snapshot, "equity", getattr(snapshot, "net_asset_value", Decimal("0"))))
        liquidation = getattr(snapshot, "equity_liquidation", equity)
        ledger_transaction_count, ledger_entry_count, last_ledger_at, ledger_hash = _ledger_evidence(ledger)
        output = cls(
            view_time,
            reporting_asset,
            _portfolio_accounts(balances, positions, account_states),
            balances,
            _portfolio_cash(snapshot, balances, reporting_asset),
            equity,
            liquidation,
            positions,
            len(getattr(snapshot, "open_structures", ())),
            getattr(snapshot, "status", "unknown"),
            tuple(getattr(snapshot, "unpriced_assets", ())),
            tuple(getattr(snapshot, "unpriced_positions", ())),
            getattr(snapshot, "delta", None),
            getattr(snapshot, "gamma", None),
            getattr(snapshot, "theta", None),
            getattr(snapshot, "vega", None),
            getattr(snapshot, "greeks_coverage", None),
            getattr(snapshot, "max_theoretical_risk", None),
            getattr(snapshot, "margin_requirement", None),
            getattr(snapshot, "buying_power", None),
            ledger_transaction_count,
            ledger_entry_count,
            last_ledger_at,
            _account_state_at(account_states),
            _market_state_at(market_view),
            ledger_hash,
        )
        return replace(output, state_hash=_hash(output))


@dataclass(frozen=True, slots=True)
class FeatureValue:
    feature_id: str
    as_of: datetime | None
    values: tuple[tuple[str, object], ...]
    quality: str = "unknown"
    input_identity: str = ""
    state_hash: str = ""
    available_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class FeatureView(_ViewContract):
    as_of: datetime | None = None
    available_time: datetime | None = None
    values: tuple[FeatureValue, ...] = ()
    feature_hash: str = "none"

    @classmethod
    def empty(cls) -> "FeatureView":
        return cls()

    @classmethod
    def from_snapshots(
        cls,
        feature_snapshot: Any | None = None,
        factor_snapshots: tuple[Any, ...] = (),
        *,
        existing: tuple[FeatureValue, ...] = (),
    ) -> "FeatureView":
        values: list[FeatureValue] = list(existing)
        if feature_snapshot is not None:
            values.append(_feature_snapshot_value(feature_snapshot))
        values.extend(_factor_value(item) for item in factor_snapshots)
        as_of = next((item.as_of for item in reversed(values) if item.as_of is not None), None)
        available_time = next((item.available_time for item in reversed(values) if item.available_time is not None), as_of)
        output = tuple(values)
        return cls(as_of, available_time, output, _hash(output) if output else "none")

    def factor(self, factor_id: str) -> FeatureValue:
        matches = [item for item in self.values if item.feature_id == factor_id]
        if len(matches) != 1:
            raise LookupError(f"context requires exactly one factor view: {factor_id}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ReferenceView(_ViewContract):
    as_of: datetime | None = None
    instrument_count: int = 0
    product_count: int = 0
    reference_count: int = 0
    settlement_count: int = 0
    instrument_ids: tuple[str, ...] = ()
    product_ids: tuple[str, ...] = ()
    product_types: tuple[tuple[str, int], ...] = ()
    listing_ids: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    contract_summaries: tuple[tuple[str, str, str], ...] = ()
    mapping_count: int = 0
    version_effective_from: datetime | None = None
    version_effective_to: datetime | None = None
    integrity_issue_count: int = 0
    integrity_hash: str = "none"
    catalog_hash: str = "none"

    @classmethod
    def empty(cls) -> "ReferenceView":
        return cls()

    @classmethod
    def from_catalog(cls, catalog: Any, *, as_of: datetime | None = None) -> "ReferenceView":
        instruments = _collection_values(getattr(catalog, "instruments", ()), as_of)
        products = _collection_values(getattr(catalog, "products", ()), as_of)
        listings = _collection_values(getattr(catalog, "listings", ()), as_of)
        routes = _collection_values(getattr(catalog, "routes", ()), as_of)
        mappings = _active_reference_items(tuple(catalog.mappings()) if hasattr(catalog, "mappings") else (), as_of)
        references = _active_reference_items(
            tuple(catalog.all_references()) if hasattr(catalog, "all_references") else _collection_values(getattr(catalog, "references", ())),
            as_of,
        )
        settlements = _collection_values(getattr(catalog, "settlements", ()), as_of)
        integrity_issues = tuple(catalog.validate_integrity(as_of)) if as_of is not None and hasattr(catalog, "validate_integrity") else ()
        version_from, version_to = _reference_version_window(
            instruments + products + listings + routes + settlements + references + mappings,
        )
        fingerprint = {
            "instruments": [str(getattr(item, "instrument_id", item)) for item in instruments],
            "products": [str(getattr(item, "product_id", item)) for item in products],
            "listings": [str(getattr(item, "listing_id", item)) for item in listings],
            "routes": [str(getattr(item, "route_id", item)) for item in routes],
            "references": [_hash(item) for item in references],
            "settlements": [str(getattr(item, "settlement_terms_id", item)) for item in settlements],
            "mappings": [_hash(item) for item in mappings],
            "integrity": integrity_issues,
        }
        return cls(
            as_of,
            len(instruments),
            len(products),
            len(references),
            len(settlements),
            tuple(sorted(_identity_value(getattr(item, "instrument_id", item)) for item in instruments)),
            tuple(sorted(_identity_value(getattr(item, "product_id", item)) for item in products)),
            _product_type_counts(products),
            tuple(sorted(_identity_value(getattr(item, "listing_id", item)) for item in listings)),
            tuple(sorted(_identity_value(getattr(item, "route_id", item)) for item in routes)),
            _contract_summaries(instruments),
            len(mappings),
            version_from,
            version_to,
            len(integrity_issues),
            _hash(integrity_issues) if integrity_issues else "none",
            _hash(fingerprint),
        )


@dataclass(frozen=True, slots=True)
class OrderSummary:
    order_id: UUID | str
    intent_id: UUID | str
    strategy_id: str
    status: str
    created_at: datetime
    filled_quantity: Decimal | int = Decimal("0")
    reason: str | None = None
    client_order_id: str | None = None
    venue_order_id: str | None = None
    updated_at: datetime | None = None
    order_type: str = "unknown"
    side: str = "unknown"
    quantity: Decimal | int | None = None
    command_id: str | None = None
    command_status: str | None = None
    attempts: int = 0
    last_error: str | None = None

    @classmethod
    def from_order(cls, order: Any, command: Any | None = None) -> "OrderSummary":
        request = getattr(order, "request", order)
        ack = getattr(order, "ack", None)
        instructions = getattr(request, "instructions", None)
        return cls(
            getattr(order, "order_id", getattr(request, "internal_order_id", "")),
            getattr(order, "intent_id", getattr(request, "intent_id", "")),
            getattr(order, "strategy_id", getattr(request, "strategy_id", "")),
            _value(getattr(order, "status", "")),
            getattr(order, "created_at"),
            getattr(order, "filled_quantity", Decimal("0")),
            getattr(order, "reason", None),
            getattr(request, "client_order_id", None),
            getattr(ack, "venue_order_id", None),
            getattr(order, "updated_at", None),
            _value(getattr(instructions, "order_type", "unknown")),
            _value(getattr(request, "side", "unknown")),
            getattr(request, "quantity", getattr(order, "quantity", None)),
            getattr(getattr(command, "command", None), "command_id", None) if command is not None else None,
            _value(getattr(command, "status", "")) if command is not None else None,
            int(getattr(command, "attempts", 0)) if command is not None else 0,
            getattr(command, "last_error", None) if command is not None else None,
        )


@dataclass(frozen=True, slots=True)
class OrderCommandSummary:
    command_id: str
    client_order_id: str
    intent_id: UUID | str
    strategy_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    last_error: str | None = None

    @classmethod
    def from_outbox(cls, record: Any) -> "OrderCommandSummary":
        request = getattr(getattr(record, "command"), "request")
        return cls(
            str(getattr(getattr(record, "command"), "command_id")),
            str(getattr(request, "client_order_id")),
            getattr(request, "intent_id"),
            str(getattr(request, "strategy_id", "")),
            _value(getattr(record, "status")),
            getattr(getattr(record, "command"), "created_at"),
            getattr(record, "updated_at"),
            int(getattr(record, "attempts", 0)),
            getattr(record, "last_error", None),
        )


@dataclass(frozen=True, slots=True)
class OrderView(_ViewContract):
    working: tuple[OrderSummary, ...] = ()
    commands: tuple[OrderCommandSummary, ...] = ()
    last_state_at: datetime | None = None
    state_hash: str = "none"

    @classmethod
    def empty(cls) -> "OrderView":
        return cls()

    @classmethod
    def from_orders(cls, orders: tuple[Any, ...], *, outbox_records: tuple[Any, ...] = ()) -> "OrderView":
        return cls.from_execution_state(orders=orders, outbox_records=outbox_records)

    @classmethod
    def from_execution_state(
        cls,
        *,
        orders: tuple[Any, ...] = (),
        outbox_records: tuple[Any, ...] = (),
    ) -> "OrderView":
        commands = tuple(OrderCommandSummary.from_outbox(item) for item in outbox_records)
        command_by_client_order_id = {_client_order_id_from_outbox(item): item for item in outbox_records}
        working = tuple(
            OrderSummary.from_order(item, command_by_client_order_id.get(_client_order_id(item)))
            for item in orders
        )
        times = tuple(
            item.updated_at or item.created_at for item in working
        ) + tuple(item.updated_at for item in commands)
        last_state_at = max(times) if times else None
        payload = {"working": working, "commands": commands, "last_state_at": last_state_at}
        return cls(working, commands, last_state_at, _hash(payload) if working or commands else "none")


@dataclass(frozen=True, slots=True)
class IntentProgressView:
    intent_id: UUID | str
    scope_key: str
    status: str
    target_quantity: Decimal | None = None
    fulfilled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    working_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    command_ids: tuple[str, ...] = ()
    order_states: tuple[tuple[str, str], ...] = ()
    last_order_update_at: datetime | None = None
    last_execution_at: datetime | None = None
    execution_event_count: int = 0

    @classmethod
    def from_execution(cls, execution: Any) -> "IntentProgressView":
        scope = getattr(execution, "scope")
        return cls(
            getattr(execution, "intent_id"),
            getattr(scope, "key", str(scope)),
            str(getattr(execution, "status", "")),
            getattr(execution, "target_quantity", None),
            getattr(execution, "fulfilled_quantity", Decimal("0")),
            getattr(execution, "remaining_quantity", Decimal("0")),
            getattr(execution, "working_quantity", Decimal("0")),
            getattr(execution, "filled_quantity", Decimal("0")),
            getattr(execution, "attempt_count", 0),
            getattr(execution, "last_attempt_at", None),
            getattr(execution, "last_error", None),
            tuple(getattr(execution, "command_ids", ())),
            tuple(getattr(execution, "order_states", ())),
            getattr(execution, "last_order_update_at", None),
            getattr(execution, "last_execution_at", None),
            int(getattr(execution, "execution_event_count", 0)),
        )


@dataclass(frozen=True, slots=True)
class IntentView(_ViewContract):
    executions: tuple[IntentProgressView, ...] = ()
    last_state_at: datetime | None = None
    state_hash: str = "none"

    @classmethod
    def empty(cls) -> "IntentView":
        return cls()

    @classmethod
    def from_executions(
        cls,
        executions: tuple[Any, ...],
        *,
        orders: tuple[Any, ...] = (),
        outbox_records: tuple[Any, ...] = (),
        execution_records: tuple[Any, ...] = (),
    ) -> "IntentView":
        evidence = _intent_execution_evidence(orders, outbox_records, execution_records)
        progress = {
            item.intent_id: item
            for item in (IntentProgressView.from_execution(item) for item in executions)
        }
        for intent_id, update in evidence.items():
            current = progress.get(intent_id)
            if current is None:
                progress[intent_id] = _intent_progress_from_evidence(intent_id, update)
            else:
                progress[intent_id] = _merge_intent_execution_evidence(current, update)
        values = tuple(sorted(progress.values(), key=lambda item: (item.scope_key, str(item.intent_id))))
        times = tuple(
            item for progress_item in values
            for item in (progress_item.last_order_update_at, progress_item.last_execution_at, progress_item.last_attempt_at)
            if item is not None
        )
        last_state_at = max(times) if times else None
        payload = {"executions": values, "last_state_at": last_state_at}
        return cls(values, last_state_at, _hash(payload) if values else "none")

    def execution(self, intent_id: UUID) -> IntentProgressView | None:
        return next((item for item in self.executions if item.intent_id == intent_id), None)

    def active(self, scope: Any) -> IntentProgressView | None:
        key = scope if isinstance(scope, str) else scope.key
        return next((item for item in self.executions if item.scope_key == key and item.status != "superseded"), None)


@dataclass(frozen=True, slots=True)
class BudgetView(_ViewContract):
    as_of: datetime | None = None
    approved_capital: Decimal | None = None
    remaining_capital: Decimal | None = None
    risk_state: tuple[tuple[str, str], ...] = ()
    strategy_positions: tuple[tuple[InstrumentId, Decimal], ...] = ()
    reduce_only: bool = False
    blocked_reason: str | None = None
    decision_count: int = 0
    approved_count: int = 0
    resized_count: int = 0
    rejected_count: int = 0
    risk_decision_hash: str = "none"
    allocation_hash: str = "none"
    limit_hash: str = "none"
    governance_hash: str = "none"
    state_hash: str = "none"

    def __post_init__(self) -> None:
        if self.remaining_capital is None and self.approved_capital is not None:
            object.__setattr__(self, "remaining_capital", self.approved_capital)

    @classmethod
    def empty(cls) -> "BudgetView":
        return cls()

    @classmethod
    def from_evidence(
        cls,
        *,
        as_of: datetime | None = None,
        approved_capital: Decimal | None = None,
        remaining_capital: Decimal | None = None,
        committed_capital: Decimal = Decimal("0"),
        risk_decisions: tuple[Any, ...] = (),
        allocation_decisions: tuple[Any, ...] = (),
        risk_limits: Any | None = None,
        runtime_state: Any | None = None,
        kill_switch: Any | None = None,
        risk_state: tuple[tuple[str, str], ...] = (),
        strategy_positions: tuple[tuple[InstrumentId, Decimal], ...] = (),
        reduce_only: bool = False,
        blocked_reason: str | None = None,
    ) -> "BudgetView":
        allocation_capital = sum(
            (getattr(item, "approved_risk_budget") for item in allocation_decisions),
            Decimal("0"),
        )
        approved = approved_capital if approved_capital is not None else allocation_capital if allocation_decisions else None
        remaining = remaining_capital
        if remaining is None and approved is not None:
            remaining = max(Decimal("0"), approved - committed_capital)
        view = cls(
            as_of,
            approved,
            remaining,
            tuple(sorted(set(risk_state + _budget_risk_state(risk_decisions, allocation_decisions)))),
            strategy_positions,
            bool(reduce_only or _runtime_reduce_only(runtime_state) or getattr(kill_switch, "reduce_only", False) or getattr(kill_switch, "triggered", False)),
            blocked_reason or _runtime_blocked_reason(runtime_state) or ("kill switch active" if getattr(kill_switch, "triggered", False) else None),
            len(risk_decisions) + len(allocation_decisions),
            _decision_count(risk_decisions, allocation_decisions, "approved"),
            _decision_count(risk_decisions, allocation_decisions, "resized"),
            _decision_count(risk_decisions, allocation_decisions, "rejected"),
            _hash(risk_decisions) if risk_decisions else "none",
            _hash(allocation_decisions) if allocation_decisions else "none",
            _hash(risk_limits) if risk_limits is not None else "none",
            _hash(_governance_evidence(runtime_state, kill_switch)) if runtime_state is not None or kill_switch is not None else "none",
        )
        return replace(view, state_hash=_hash(view))


MARKET_VIEW_SCHEMA = ViewSchema(
    "MarketView",
    "Market Plane",
    (
        ViewFieldSchema("timestamp", "策略可见行情视图时间", "available_time", "market projection timestamp"),
        ViewFieldSchema("sequence", "行情投影序号", "projection order", "market projection sequence"),
        ViewFieldSchema("instruments", "本次视图包含的行情工具", "visible at timestamp", "market projection instruments"),
        ViewFieldSchema("data_binding", "行情数据绑定标识", "binding version time", "dataset release or live source binding"),
        ViewFieldSchema("event_window", "本次视图覆盖的原始事件窗口", "[start,end)", "market source event window"),
        ViewFieldSchema("available_instruments", "策略可交易/可见 universe", "visible at available_time", "universe binding"),
        ViewFieldSchema("reference_prices", "当前可见参考价", "available at timestamp", "canonical market inputs"),
        ViewFieldSchema("quality_codes", "行情质量和缺口信号", "measured at available_time", "market quality evidence"),
        ViewFieldSchema("snapshot_span_seconds", "快照覆盖窗口", "event window", "market projection window"),
        ViewFieldSchema("available_time", "策略实际可见时间", "available_time", "data binding availability"),
        ViewFieldSchema("freshness_seconds", "视图新鲜度", "receive_time - event_time", "freshness evidence"),
    ),
    ("DataClient", "DatasetRelease", "connector payload"),
)
PORTFOLIO_VIEW_SCHEMA = ViewSchema(
    "PortfolioView",
    "Portfolio/Risk Plane",
    (
        ViewFieldSchema("timestamp", "组合投影视图时间", "projection time", "ledger + market projection"),
        ViewFieldSchema("reporting_asset", "组合报告资产", "as of timestamp", "portfolio projection reporting asset"),
        ViewFieldSchema("accounts", "组合视图覆盖账户", "as of timestamp", "ledger/account state identity evidence"),
        ViewFieldSchema("balances", "账户资产余额摘要", "as of timestamp", "ledger facts + account state projection"),
        ViewFieldSchema("cash", "现金余额摘要", "as of timestamp", "portfolio ledger projection"),
        ViewFieldSchema("equity_mid", "按 mid 标记权益", "as of timestamp", "portfolio projection"),
        ViewFieldSchema("equity_liquidation", "按清算价标记权益", "as of timestamp", "portfolio projection"),
        ViewFieldSchema("positions", "只读持仓摘要", "as of timestamp", "ledger facts + market marks"),
        ViewFieldSchema("open_structure_count", "开放结构数量", "as of timestamp", "portfolio projection"),
        ViewFieldSchema("valuation_status", "估值覆盖状态", "as of timestamp", "portfolio valuation coverage evidence"),
        ViewFieldSchema("unpriced_assets", "未能换算的资产余额", "as of timestamp", "conversion coverage evidence"),
        ViewFieldSchema("unpriced_positions", "未能定价的持仓", "as of timestamp", "valuation coverage evidence"),
        ViewFieldSchema("delta", "组合 delta", "as of timestamp", "risk projection"),
        ViewFieldSchema("gamma", "组合 gamma", "as of timestamp", "risk projection"),
        ViewFieldSchema("theta", "组合 theta", "as of timestamp", "risk projection"),
        ViewFieldSchema("vega", "组合 vega", "as of timestamp", "risk projection"),
        ViewFieldSchema("greeks_coverage", "Greeks 覆盖率", "as of timestamp", "risk coverage evidence"),
        ViewFieldSchema("max_theoretical_risk", "理论最大风险", "as of timestamp", "risk projection"),
        ViewFieldSchema("margin_requirement", "保证金需求摘要", "as of timestamp", "portfolio/account projection"),
        ViewFieldSchema("buying_power", "可用购买力摘要", "as of timestamp", "portfolio/account projection"),
        ViewFieldSchema("ledger_transaction_count", "组合投影使用的账本交易数量", "as of timestamp", "ledger projection evidence"),
        ViewFieldSchema("ledger_entry_count", "组合投影使用的账本分录数量", "as of timestamp", "ledger projection evidence"),
        ViewFieldSchema("last_ledger_at", "最近账本事实时间", "ledger fact time", "ledger transaction evidence"),
        ViewFieldSchema("account_state_at", "最近外部账户状态时间", "account observation time", "account state evidence"),
        ViewFieldSchema("market_state_at", "组合估值所用市场状态时间", "market available_time", "market view evidence"),
        ViewFieldSchema("ledger_hash", "账本事实哈希", "hash time", "ledger facts hash"),
        ViewFieldSchema("state_hash", "组合视图状态哈希", "hash time", "portfolio projection evidence hash"),
    ),
    ("LedgerService", "mutable portfolio", "broker account client"),
)
FEATURE_VIEW_SCHEMA = ViewSchema(
    "FeatureView",
    "Feature/Model Plane",
    (
        ViewFieldSchema("as_of", "特征视图时间", "inherits input available_time", "feature pipeline evidence"),
        ViewFieldSchema("available_time", "特征输入可见时间", "max inherited input available_time", "feature input availability evidence"),
        ViewFieldSchema("values", "特征和模型输出摘要", "as of feature input window", "feature snapshots"),
        ViewFieldSchema("feature_hash", "特征状态哈希", "hash time", "feature values hash"),
    ),
    ("feature recompute service", "model internals", "calibration service"),
)
REFERENCE_VIEW_SCHEMA = ViewSchema(
    "ReferenceView",
    "Reference Plane",
    (
        ViewFieldSchema("as_of", "reference 版本时间", "point-in-time reference", "reference catalog version"),
        ViewFieldSchema("instrument_count", "可见 instrument 数量", "as of reference version", "reference catalog"),
        ViewFieldSchema("product_count", "可见 product 数量", "as of reference version", "reference catalog"),
        ViewFieldSchema("reference_count", "reference fact 数量", "as of reference version", "reference catalog"),
        ViewFieldSchema("settlement_count", "结算规则数量", "as of reference version", "reference catalog"),
        ViewFieldSchema("instrument_ids", "可见 instrument identity 摘要", "as of reference version", "reference catalog active instruments"),
        ViewFieldSchema("product_ids", "可见 product identity 摘要", "as of reference version", "reference catalog active products"),
        ViewFieldSchema("product_types", "可见产品类型分布", "as of reference version", "reference catalog product type counts"),
        ViewFieldSchema("listing_ids", "可见 listing identity 摘要", "as of reference version", "reference catalog active listings"),
        ViewFieldSchema("route_ids", "可见 execution route identity 摘要", "as of reference version", "reference catalog active routes"),
        ViewFieldSchema("contract_summaries", "合约摘要", "as of reference version", "instrument contract summary"),
        ViewFieldSchema("mapping_count", "provider symbol mapping 数量", "as of reference version", "reference mapping evidence"),
        ViewFieldSchema("version_effective_from", "reference 版本起始有效时间", "point-in-time reference interval", "reference active definition window"),
        ViewFieldSchema("version_effective_to", "reference 版本结束有效时间", "point-in-time reference interval", "reference active definition window"),
        ViewFieldSchema("integrity_issue_count", "reference 完整性问题数量", "as of reference version", "reference integrity evidence"),
        ViewFieldSchema("integrity_hash", "reference 完整性问题哈希", "hash time", "reference integrity evidence hash"),
        ViewFieldSchema("catalog_hash", "reference catalog 哈希", "as of reference version", "reference fingerprint"),
    ),
    ("reference sync client", "provider reference DTO"),
)
ORDER_VIEW_SCHEMA = ViewSchema(
    "OrderView",
    "Execution Plane",
    (
        ViewFieldSchema("working", "本策略相关 working order 摘要", "last known order state", "execution projection"),
        ViewFieldSchema("commands", "本策略相关 pending/dispatch order command 摘要", "last known outbox state", "outbox evidence projection"),
        ViewFieldSchema("last_state_at", "订单/命令状态更新时间", "last observed execution state", "order/outbox updated_at"),
        ViewFieldSchema("state_hash", "订单视图状态哈希", "hash time", "order view evidence hash"),
    ),
    ("submit method", "cancel method", "outbox writer", "gateway"),
)
INTENT_VIEW_SCHEMA = ViewSchema(
    "IntentView",
    "Execution Plane",
    (
        ViewFieldSchema("executions", "intent 进度投影", "last known intent state", "intent execution tracker view"),
        ViewFieldSchema("last_state_at", "intent 执行证据更新时间", "last observed execution state", "intent/order/execution evidence"),
        ViewFieldSchema("state_hash", "intent 视图状态哈希", "hash time", "intent view evidence hash"),
    ),
    ("intent state mutator", "execution tracker internals"),
)
BUDGET_VIEW_SCHEMA = ViewSchema(
    "BudgetView",
    "Risk/Governance Plane",
    (
        ViewFieldSchema("as_of", "预算视图时间", "decision_time", "risk/governance evidence time"),
        ViewFieldSchema("approved_capital", "已批准资本", "decision_time", "risk approval evidence"),
        ViewFieldSchema("remaining_capital", "剩余可用资本", "decision_time", "risk budget projection"),
        ViewFieldSchema("risk_state", "风险状态摘要", "decision_time", "risk/governance state"),
        ViewFieldSchema("strategy_positions", "策略持仓预算摘要", "decision_time", "risk projection"),
        ViewFieldSchema("reduce_only", "是否 reduce-only", "decision_time", "governance gate"),
        ViewFieldSchema("blocked_reason", "阻断原因", "decision_time", "risk/governance reason"),
        ViewFieldSchema("decision_count", "风险/预算决策数量", "decision_time", "risk/allocation decision evidence"),
        ViewFieldSchema("approved_count", "批准决策数量", "decision_time", "risk/allocation decision evidence"),
        ViewFieldSchema("resized_count", "缩量决策数量", "decision_time", "risk/allocation decision evidence"),
        ViewFieldSchema("rejected_count", "拒绝决策数量", "decision_time", "risk/allocation decision evidence"),
        ViewFieldSchema("risk_decision_hash", "风险决策哈希", "hash time", "risk decision evidence hash"),
        ViewFieldSchema("allocation_hash", "资金分配决策哈希", "hash time", "allocation decision evidence hash"),
        ViewFieldSchema("limit_hash", "风险限制配置哈希", "hash time", "risk limits evidence hash"),
        ViewFieldSchema("governance_hash", "治理状态哈希", "hash time", "runtime/governance evidence hash"),
        ViewFieldSchema("state_hash", "预算视图状态哈希", "hash time", "budget view evidence hash"),
    ),
    ("risk approval service", "limit mutator"),
)

CONTEXT_VIEW_SCHEMAS = (
    MARKET_VIEW_SCHEMA,
    PORTFOLIO_VIEW_SCHEMA,
    FEATURE_VIEW_SCHEMA,
    REFERENCE_VIEW_SCHEMA,
    ORDER_VIEW_SCHEMA,
    INTENT_VIEW_SCHEMA,
    BUDGET_VIEW_SCHEMA,
)
_VIEW_SCHEMA_BY_NAME = {item.view: item for item in CONTEXT_VIEW_SCHEMAS}


def context_view_schemas() -> tuple[ViewSchema, ...]:
    return CONTEXT_VIEW_SCHEMAS


def view_schema(view: str | type[object]) -> ViewSchema:
    name = view if isinstance(view, str) else view.__name__
    try:
        return _VIEW_SCHEMA_BY_NAME[name]
    except KeyError as exc:
        raise LookupError(f"unknown context view schema: {name}") from exc


def view_hash(value: object) -> str:
    return _hash(value)


def _feature_snapshot_value(snapshot: Any) -> FeatureValue:
    if is_dataclass(snapshot):
        values = tuple(
            (field.name, getattr(snapshot, field.name))
            for field in fields(snapshot)
            if field.name not in {"as_of", "available_time", "input_identity", "state_hash"}
        )
    else:
        values = tuple(sorted(
            (str(key), value) for key, value in vars(snapshot).items()
            if key not in {"as_of", "available_time", "input_identity", "state_hash"}
        ))
    as_of = getattr(snapshot, "as_of", None)
    return FeatureValue(
        "model_features",
        as_of,
        values,
        quality="computed",
        input_identity=getattr(snapshot, "input_identity", ""),
        state_hash=getattr(snapshot, "state_hash", ""),
        available_time=getattr(snapshot, "available_time", as_of),
    )


def _factor_value(snapshot: Any) -> FeatureValue:
    quality = getattr(getattr(snapshot, "quality", ""), "value", getattr(snapshot, "quality", "unknown"))
    return FeatureValue(
        getattr(snapshot, "factor_id"),
        getattr(snapshot, "as_of", None),
        tuple(getattr(snapshot, "values", ())),
        str(quality),
        getattr(snapshot, "input_identity", ""),
        getattr(snapshot, "state_hash", ""),
        getattr(snapshot, "available_time", getattr(snapshot, "as_of", None)),
    )


def _identity_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _optional_identity_value(value: Any | None) -> str | None:
    return None if value is None else _identity_value(value)


def _portfolio_cash(snapshot: Any, balances: tuple[BalanceView, ...], reporting_asset: str | None) -> Decimal:
    if hasattr(snapshot, "cash"):
        return getattr(snapshot, "cash")
    if hasattr(snapshot, "net_asset_value"):
        position_value = sum(
            (
                getattr(item, "market_value_reporting")
                for item in getattr(snapshot, "positions", ())
                if getattr(item, "market_value_reporting", None) is not None
            ),
            Decimal("0"),
        )
        return getattr(snapshot, "net_asset_value") - position_value
    if reporting_asset is None:
        return Decimal("0")
    return sum((item.total for item in balances if item.asset == reporting_asset), Decimal("0"))


def _portfolio_accounts(
    balances: tuple[BalanceView, ...],
    positions: tuple[PositionView, ...],
    account_states: tuple[Any, ...],
) -> tuple[str, ...]:
    accounts = {
        item.account
        for item in balances
        if item.account
    }
    accounts.update(item.account for item in positions if item.account)
    accounts.update(_identity_value(getattr(item, "account")) for item in account_states if hasattr(item, "account"))
    return tuple(sorted(accounts))


def _ledger_evidence(ledger: Any | None) -> tuple[int, int, datetime | None, str]:
    if ledger is None:
        return 0, 0, None, "none"
    transactions = tuple(getattr(ledger, "transactions", ()))
    entries = tuple(getattr(ledger, "entries", ()))
    times = tuple(getattr(item, "timestamp", None) for item in transactions if getattr(item, "timestamp", None) is not None)
    last_ledger_at = max(times) if times else None
    return len(transactions), len(entries), last_ledger_at, _hash(transactions) if transactions else "none"


def _account_state_at(account_states: tuple[Any, ...]) -> datetime | None:
    times = tuple(getattr(item, "timestamp", None) for item in account_states if getattr(item, "timestamp", None) is not None)
    return max(times) if times else None


def _market_state_at(market_view: Any | None) -> datetime | None:
    if market_view is None:
        return None
    return getattr(market_view, "available_time", getattr(market_view, "timestamp", None))


def _active_reference_items(items: tuple[Any, ...], as_of: datetime | None) -> tuple[Any, ...]:
    if as_of is None:
        return items
    return tuple(item for item in items if not hasattr(item, "active_at") or item.active_at(as_of))


def _reference_version_window(items: tuple[Any, ...]) -> tuple[datetime | None, datetime | None]:
    starts = tuple(getattr(item, "effective_from", None) for item in items if getattr(item, "effective_from", None) is not None)
    ends = tuple(getattr(item, "effective_to", None) for item in items if getattr(item, "effective_to", None) is not None)
    return (max(starts) if starts else None, min(ends) if ends else None)


def _product_type_counts(products: tuple[Any, ...]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for product in products:
        key = _value(getattr(product, "product_type", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(counts.items()))


def _contract_summaries(instruments: tuple[Any, ...]) -> tuple[tuple[str, str, str], ...]:
    values = []
    for instrument in instruments:
        values.append((
            _identity_value(getattr(instrument, "instrument_id", instrument)),
            _identity_value(getattr(instrument, "product_id", "")),
            _value(getattr(instrument, "instrument_type", "unknown")),
        ))
    return tuple(sorted(values))


def _budget_risk_state(risk_decisions: tuple[Any, ...], allocation_decisions: tuple[Any, ...]) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for decision in risk_decisions:
        values.append((
            f"risk:{getattr(decision, 'rule', 'unknown')}",
            f"{_value(getattr(decision, 'decision', 'unknown'))}:{getattr(decision, 'reason', '')}",
        ))
    for decision in allocation_decisions:
        values.append((
            f"allocation:{getattr(decision, 'strategy_id', 'unknown')}",
            f"{_value(getattr(decision, 'decision', 'unknown'))}:{getattr(decision, 'reason', '')}",
        ))
    return tuple(values)


def _decision_count(risk_decisions: tuple[Any, ...], allocation_decisions: tuple[Any, ...], value: str) -> int:
    return sum(
        1
        for decision in risk_decisions + allocation_decisions
        if _value(getattr(decision, "decision", "")) == value
    )


def _runtime_reduce_only(runtime_state: Any | None) -> bool:
    if runtime_state is None:
        return False
    if isinstance(runtime_state, dict):
        return str(runtime_state.get("status", "")) == "reduce_only" or bool(runtime_state.get("reduce_only"))
    return _value(getattr(runtime_state, "status", "")) == "reduce_only"


def _runtime_blocked_reason(runtime_state: Any | None) -> str | None:
    if runtime_state is None:
        return None
    if isinstance(runtime_state, dict):
        reason = runtime_state.get("reason")
        status = runtime_state.get("status")
        return str(reason) if reason else f"runtime status {status}" if status in {"reduce_only", "unknown_external_state", "failed_start"} else None
    reason = getattr(runtime_state, "reason", None)
    status = _value(getattr(runtime_state, "status", ""))
    return str(reason) if reason else f"runtime status {status}" if status in {"reduce_only", "unknown_external_state", "failed_start"} else None


def _governance_evidence(runtime_state: Any | None, kill_switch: Any | None) -> dict[str, Any]:
    return {
        "runtime_state": runtime_state,
        "kill_switch": {
            "triggered": getattr(kill_switch, "triggered", False),
            "reduce_only": getattr(kill_switch, "reduce_only", False),
        } if kill_switch is not None else None,
    }


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _client_order_id(order: Any) -> str | None:
    request = getattr(order, "request", order)
    value = getattr(request, "client_order_id", None)
    return str(value) if value is not None else None


def _client_order_id_from_outbox(record: Any) -> str:
    return str(getattr(getattr(getattr(record, "command"), "request"), "client_order_id"))


def _intent_id_from_order(order: Any) -> UUID | str:
    return getattr(getattr(order, "request", order), "intent_id", getattr(order, "intent_id", ""))


def _intent_id_from_outbox(record: Any) -> UUID | str:
    return getattr(getattr(getattr(record, "command"), "request"), "intent_id")


def _intent_id_from_execution_record(record: Any) -> UUID | str:
    order = getattr(record, "order")
    return _intent_id_from_order(order)


def _intent_execution_evidence(
    orders: tuple[Any, ...],
    outbox_records: tuple[Any, ...],
    execution_records: tuple[Any, ...],
) -> dict[UUID | str, dict[str, Any]]:
    evidence: dict[UUID | str, dict[str, Any]] = {}
    for record in outbox_records:
        intent_id = _intent_id_from_outbox(record)
        bucket = evidence.setdefault(intent_id, _empty_intent_evidence())
        bucket["scope_key"] = _scope_key_from_request(getattr(getattr(record, "command"), "request"))
        bucket["command_ids"].append(str(getattr(getattr(record, "command"), "command_id")))
        bucket["attempt_count"] += int(getattr(record, "attempts", 0))
        bucket["last_order_update_at"] = _max_time(bucket["last_order_update_at"], getattr(record, "updated_at", None))
        if getattr(record, "last_error", None):
            bucket["last_error"] = getattr(record, "last_error")
    for order in orders:
        intent_id = _intent_id_from_order(order)
        request = getattr(order, "request", order)
        bucket = evidence.setdefault(intent_id, _empty_intent_evidence())
        bucket["scope_key"] = _scope_key_from_request(request)
        client_order_id = _client_order_id(order)
        if client_order_id is not None:
            bucket["order_states"].append((client_order_id, _value(getattr(order, "status", ""))))
        bucket["working_quantity"] += _working_quantity(order)
        bucket["last_order_update_at"] = _max_time(bucket["last_order_update_at"], getattr(order, "updated_at", None))
        if getattr(order, "reason", None):
            bucket["last_error"] = getattr(order, "reason")
    for record in execution_records:
        intent_id = _intent_id_from_execution_record(record)
        request = getattr(getattr(record, "order"), "request")
        execution = getattr(record, "execution")
        bucket = evidence.setdefault(intent_id, _empty_intent_evidence())
        bucket["scope_key"] = _scope_key_from_request(request)
        bucket["filled_quantity"] += getattr(execution, "quantity", Decimal("0"))
        bucket["execution_event_count"] += 1
        bucket["last_execution_at"] = _max_time(
            bucket["last_execution_at"],
            getattr(record, "occurred_at", getattr(execution, "timestamp", None)),
        )
    return evidence


def _empty_intent_evidence() -> dict[str, Any]:
    return {
        "scope_key": "",
        "command_ids": [],
        "order_states": [],
        "working_quantity": Decimal("0"),
        "filled_quantity": Decimal("0"),
        "attempt_count": 0,
        "last_order_update_at": None,
        "last_execution_at": None,
        "execution_event_count": 0,
        "last_error": None,
    }


def _intent_progress_from_evidence(intent_id: UUID | str, evidence: dict[str, Any]) -> IntentProgressView:
    filled = evidence["filled_quantity"]
    working = evidence["working_quantity"]
    status = _intent_status_from_evidence(evidence)
    return IntentProgressView(
        intent_id,
        evidence["scope_key"],
        status,
        fulfilled_quantity=filled,
        working_quantity=working,
        filled_quantity=filled,
        attempt_count=evidence["attempt_count"],
        last_attempt_at=evidence["last_order_update_at"],
        last_error=evidence["last_error"],
        command_ids=tuple(sorted(set(evidence["command_ids"]))),
        order_states=tuple(sorted(set(evidence["order_states"]))),
        last_order_update_at=evidence["last_order_update_at"],
        last_execution_at=evidence["last_execution_at"],
        execution_event_count=evidence["execution_event_count"],
    )


def _merge_intent_execution_evidence(current: IntentProgressView, evidence: dict[str, Any]) -> IntentProgressView:
    return replace(
        current,
        working_quantity=evidence["working_quantity"] or current.working_quantity,
        filled_quantity=evidence["filled_quantity"] or current.filled_quantity,
        attempt_count=current.attempt_count + evidence["attempt_count"],
        last_attempt_at=_max_time(current.last_attempt_at, evidence["last_order_update_at"]),
        last_error=evidence["last_error"] or current.last_error,
        command_ids=tuple(sorted(set(current.command_ids) | set(evidence["command_ids"]))),
        order_states=tuple(sorted(set(current.order_states) | set(evidence["order_states"]))),
        last_order_update_at=_max_time(current.last_order_update_at, evidence["last_order_update_at"]),
        last_execution_at=_max_time(current.last_execution_at, evidence["last_execution_at"]),
        execution_event_count=current.execution_event_count + evidence["execution_event_count"],
    )


def _intent_status_from_evidence(evidence: dict[str, Any]) -> str:
    states = {status for _, status in evidence["order_states"]}
    if states <= {"filled"} and states:
        return "satisfied"
    if "rejected" in states or "failed_terminal" in states:
        return "failed"
    if "partially_filled" in states or evidence["filled_quantity"]:
        return "partially_satisfied"
    if states or evidence["command_ids"]:
        return "executing"
    return "pending"


def _scope_key_from_request(request: Any) -> str:
    instrument_id = getattr(request, "instrument_id", None)
    if instrument_id is None:
        legs = getattr(request, "legs", ())
        instrument_id = ",".join(str(getattr(item, "instrument_id", "")) for item in legs) or getattr(request, "client_order_id", "")
    resource = str(getattr(instrument_id, "value", instrument_id))
    return f"{getattr(request, 'strategy_id', '')}:order:{resource}"


def _working_quantity(order: Any) -> Decimal:
    status = _value(getattr(order, "status", ""))
    if status in {"rejected", "filled", "cancelled", "expired"}:
        return Decimal("0")
    request = getattr(order, "request", order)
    quantity = getattr(request, "quantity", getattr(order, "quantity", Decimal("0")))
    try:
        return Decimal(str(quantity))
    except Exception:
        return Decimal("0")


def _max_time(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _collection_values(value: Any, as_of: datetime | None = None) -> tuple[Any, ...]:
    if hasattr(value, "values"):
        try:
            return tuple(value.values(as_of))
        except TypeError:
            return tuple(value.values())
    return tuple(value)


def _hash(value: Any) -> str:
    return sha256(json.dumps(_primitive(value), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_primitive(item) for item in value]
    if isinstance(value, (Decimal, datetime, UUID, InstrumentId)):
        return str(value)
    return value

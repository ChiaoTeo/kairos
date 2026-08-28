#!/usr/bin/env python3
"""Generate mechanically exact structural stubs from built owner extensions."""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
from pathlib import Path
import re
from types import ModuleType
from typing import Callable, cast


ROOT = Path(__file__).resolve().parents[2]
OWNERS = ("account", "capital", "execution", "market", "reference", "risk")
HAND_TYPED_OWNERS = frozenset({"execution", "market", "reference", "risk"})
MODULE_FUNCTIONS: dict[str, tuple[str, ...]] = {
    owner: ("build_info", "decode_event", "indexed_environment_path")
    for owner in OWNERS
}
MODULE_FUNCTIONS["reference"] = ("build_info", "decode_event")
RETURN_TYPES = {
    ("account", "AccountClient", "control"): "AccountControlClient",
    ("account", "AccountClient", "current"): "AccountCurrentView | None",
    ("account", "AccountClient", "events"): "AccountLiveSubscription",
    ("account", "AccountControlClient", "advance_time"): "AdvanceAccountTimeResponse",
    ("account", "AccountControlClient", "apply_simulated_capital_mutation"): "AccountCommandStatus",
    ("account", "AccountControlClient", "apply_simulated_settlement"): "AccountCommandStatus",
    ("account", "AccountControlClient", "health"): "AccountHealth",
    ("account", "AccountControlClient", "mark_to_market"): "AccountCommandStatus",
    ("account", "AccountControlClient", "query_simulated_capital_mutation"): "SimulatedCapitalMutationStatusResponse",
    ("account", "AccountControlClient", "reconcile"): "AccountRefreshResponse",
    ("account", "AccountControlClient", "refresh"): "AccountRefreshResponse",
    ("account", "AccountControlClient", "socket_path"): "Path",
    ("account", "AccountCurrentView", "close"): "None",
    ("account", "AccountCurrentView", "path"): "Path",
    ("account", "AccountCurrentView", "snapshot"): "AccountCurrentSnapshot",
    ("account", "AccountEvent", "data"): "AccountBalanceEvent | AccountBalanceRemovedEvent | AccountPositionEvent | AccountPositionRemovedEvent | AccountEarnHoldingEvent | AccountEarnHoldingRemovedEvent | AccountValuationEvent | AccountStatusEvent | AccountObservedOrderEvent | AccountObservedOrderRemovedEvent",
    ("account", "AccountEvent", "instance_id"): "InstanceIdRead | None",
    ("account", "AccountEvent", "launch_id"): "LaunchIdRead | None",
    ("account", "AccountEvent", "occurred_at_unix_nanos"): "UnixNanosRead",
    ("account", "AccountEvent", "producer"): "ProducerIdRead",
    ("account", "AccountEvent", "producer_incarnation"): "int",
    ("account", "AccountEvent", "sequence"): "SequenceRead",
    ("account", "AccountEvent", "simulation_balance"): "AccountEvent",
    ("account", "AccountEvent", "simulation_status"): "AccountEvent",
    ("account", "AccountEvent", "simulation_valuation"): "AccountEvent",
    ("account", "AccountEvent", "stream_id"): "str",
    ("account", "AccountLiveEventView", "data"): "AccountBalanceEvent | AccountBalanceRemovedEvent | AccountPositionEvent | AccountPositionRemovedEvent | AccountEarnHoldingEvent | AccountEarnHoldingRemovedEvent | AccountValuationEvent | AccountStatusEvent | AccountObservedOrderEvent | AccountObservedOrderRemovedEvent",
    ("account", "AccountLiveSubscription", "close"): "None",
    ("account", "AccountLiveSubscription", "poll_visit"): "int",
    ("account", "AccountSegmentsRequest", "segments"): "list[SegmentKeyRead]",
    ("account", "AdvanceAccountTimeRequest", "event_time_unix_nanos"): "UnixNanosRead",
    ("capital", "CancelFundingObjectiveRequest", "expected_version"): "int",
    ("capital", "CancelFundingObjectiveRequest", "observed_at_unix_nanos"): "UnixNanosRead",
    ("capital", "CapitalClient", "control"): "CapitalControlClient",
    ("capital", "CapitalClient", "current"): "CapitalCurrentView | None",
    ("capital", "CapitalClient", "events"): "CapitalLiveSubscription",
    ("capital", "CapitalControlClient", "cancel_funding_objective"): "CapitalControlResponse",
    ("capital", "CapitalControlClient", "health"): "CapitalHealth",
    ("capital", "CapitalControlClient", "observe_capital_demand"): "CapitalDemandResponse",
    ("capital", "CapitalControlClient", "publish_funding_objective"): "CapitalControlResponse",
    ("capital", "CapitalControlClient", "query_capital_availability"): "CapitalAvailabilityResponse",
    ("capital", "CapitalControlClient", "reconcile_capital_plan"): "ReconcileCapitalPlanResponse",
    ("capital", "CapitalControlClient", "socket_path"): "Path",
    ("capital", "CapitalCurrentSnapshot", "availability"): "CapitalAvailability",
    ("capital", "CapitalCurrentView", "close"): "None",
    ("capital", "CapitalCurrentView", "path"): "Path",
    ("capital", "CapitalCurrentView", "snapshot"): "CapitalCurrentSnapshot",
    ("capital", "CapitalEvent", "instance_id"): "InstanceIdRead | None",
    ("capital", "CapitalEvent", "launch_id"): "LaunchIdRead | None",
    ("capital", "CapitalEvent", "occurred_at_unix_nanos"): "UnixNanosRead",
    ("capital", "CapitalEvent", "data"): "CapitalAvailabilityEventPayload | CapitalPlanEventPayload | CapitalAvailability | CapitalDemand | CapitalFacts | CapitalPolicy | CapitalRoute | FundingObjective",
    ("capital", "CapitalEvent", "producer"): "ProducerIdRead",
    ("capital", "CapitalEvent", "producer_incarnation"): "int",
    ("capital", "CapitalEvent", "sequence"): "SequenceRead",
    ("capital", "CapitalEvent", "stream_id"): "str",
    ("capital", "CapitalLiveEventView", "data"): "CapitalAvailabilityEventPayload | CapitalPlanEventPayload | CapitalAvailability | CapitalDemand | CapitalFacts | CapitalPolicy | CapitalRoute | FundingObjective",
    ("capital", "CapitalLiveSubscription", "close"): "None",
    ("capital", "CapitalLiveSubscription", "poll_visit"): "int",
    ("capital", "ObserveCapitalDemandRequest", "account_watermark"): "SequenceRead",
    ("capital", "ObserveCapitalDemandRequest", "causal_references"): "list[str]",
    ("capital", "ObserveCapitalDemandRequest", "confidence_bps"): "BasisPointsRead",
    ("capital", "ObserveCapitalDemandRequest", "destination"): "FundingLocation",
    ("capital", "ObserveCapitalDemandRequest", "destination_lease_fence"): "str",
    ("capital", "ObserveCapitalDemandRequest", "expires_at_unix_nanos"): "UnixNanosRead",
    ("capital", "ObserveCapitalDemandRequest", "idempotency_key"): "IdempotencyKeyRead",
    ("capital", "ObserveCapitalDemandRequest", "instance_id"): "InstanceIdRead",
    ("capital", "ObserveCapitalDemandRequest", "launch_id"): "LaunchIdRead",
    ("capital", "ObserveCapitalDemandRequest", "observed_at_unix_nanos"): "UnixNanosRead",
    ("capital", "ObserveCapitalDemandRequest", "priority"): "str",
    ("capital", "ObserveCapitalDemandRequest", "required_by_unix_nanos"): "UnixNanosRead",
        ("capital", "ObserveCapitalDemandRequest", "risk_watermark"): "SequenceRead",
        ("capital", "CapitalAvailability", "active_demand_ids"): "list[CapitalDemandIdRead]",
        ("capital", "CapitalAvailability", "active_objective_ids"): "list[FundingObjectiveIdRead]",
        ("capital", "CapitalAvailabilityResponse", "active_demand_ids"): "list[CapitalDemandIdRead]",
        ("capital", "CapitalAvailabilityResponse", "active_objective_ids"): "list[FundingObjectiveIdRead]",
        ("capital", "CapitalPlan", "demand_ids"): "list[CapitalDemandIdRead]",
        ("capital", "CapitalPlan", "objective_ids"): "list[FundingObjectiveIdRead]",
        ("capital", "FundingHorizon", "demand_ids"): "list[CapitalDemandIdRead]",
        ("capital", "FundingHorizon", "objective_ids"): "list[FundingObjectiveIdRead]",
    ("capital", "PublishFundingObjectiveRequest", "confidence_bps"): "BasisPointsRead",
    ("capital", "PublishFundingObjectiveRequest", "destination"): "FundingLocation",
    ("capital", "PublishFundingObjectiveRequest", "expires_at_unix_nanos"): "UnixNanosRead",
    ("capital", "PublishFundingObjectiveRequest", "observed_at_unix_nanos"): "UnixNanosRead",
    ("capital", "PublishFundingObjectiveRequest", "priority"): "str",
    ("capital", "PublishFundingObjectiveRequest", "required_by_unix_nanos"): "UnixNanosRead",
    ("capital", "PublishFundingObjectiveRequest", "strategy_decision_id"): "StrategyDecisionIdRead",
    ("capital", "PublishFundingObjectiveRequest", "version"): "int",
}
RETURN_TYPES.update(
    {
        ("execution", "ExecutionClient", "control"): "ExecutionControlClient",
        ("execution", "ExecutionClient", "current"): "ExecutionCurrentView | None",
        ("execution", "ExecutionClient", "events"): "ExecutionLiveSubscription",
        ("execution", "ExecutionEvent", "data"): "ExecutionIntentUpdate | ExecutionPlanCreatedValue | ExecutionOrderUpdate | ExecutionFillValue",
        ("execution", "ExecutionEvent", "producer"): "ProducerIdRead",
        ("execution", "ExecutionEvent", "sequence"): "SequenceRead",
        ("execution", "ExecutionLiveEventView", "data"): "ExecutionIntentUpdate | ExecutionPlanCreatedValue | ExecutionOrderUpdate | ExecutionFillValue",
        ("execution", "ExecutionLiveSubscription", "close"): "None",
        ("execution", "ExecutionLiveSubscription", "poll_visit"): "int",
        ("execution", "ExecutionEventMetadata", "occurred_at_unix_nanos"): "UnixNanosRead",
        ("execution", "ExecutionEventMetadata", "producer"): "ProducerIdRead",
        ("execution", "ExecutionEventMetadata", "sequence"): "SequenceRead",
        ("execution", "ExecutionUnknownRemoteOrderCurrent", "fill_price"): "PriceLike | None",
        ("execution", "ExecutionUnknownRemoteOrderCurrent", "fill_quantity"): "QuantityLike | None",
        ("market", "MarketClient", "control"): "MarketControlClient",
        ("market", "MarketClient", "current"): "MarketCurrentView | None",
        ("market", "MarketClient", "events"): "MarketLiveSubscription",
        ("market", "MarketCurrentView", "get"): "MarketQuoteCurrent | MarketBarCurrent | MarketGreeksCurrent | MarketRateCurrent | MarketTicker24hCurrent | MarketMarkPriceCurrent | MarketFundingRateCurrent | MarketOpenInterestCurrent | MarketIndexPriceCurrent | MarketOrderBookCurrent | MarketFreshnessCurrent | None",
        ("market", "MarketEvent", "data"): "MarketQuoteCurrent | MarketTradeEventPayload | MarketBarCurrent | MarketGreeksCurrent | MarketRateCurrent | MarketTicker24hCurrent | MarketMarkPriceCurrent | MarketFundingRateCurrent | MarketOpenInterestCurrent | MarketIndexPriceCurrent | MarketOrderBookCurrent | MarketOrderBookDeltaEventPayload | MarketOrderBookResyncEventPayload",
        ("market", "MarketEvent", "occurred_at_unix_nanos"): "UnixNanosRead",
        ("market", "MarketEvent", "sequence"): "SequenceRead",
        ("market", "MarketEventMetadata", "occurred_at_unix_nanos"): "UnixNanosRead",
        ("market", "MarketEventMetadata", "sequence"): "SequenceRead",
        ("market", "MarketLiveEventView", "data"): "MarketQuoteCurrent | MarketTradeEventPayload | MarketBarCurrent | MarketGreeksCurrent | MarketRateCurrent | MarketTicker24hCurrent | MarketMarkPriceCurrent | MarketFundingRateCurrent | MarketOpenInterestCurrent | MarketIndexPriceCurrent | MarketOrderBookCurrent | MarketOrderBookDeltaEventPayload | MarketOrderBookResyncEventPayload",
        ("market", "MarketLiveSubscription", "close"): "None",
        ("market", "MarketLiveSubscription", "poll_visit"): "int",
        ("market", "MarketMarkPriceCurrent", "estimated_settlement_price"): "PriceLike | None",
        ("market", "Options", "where"): "Options",
        ("risk", "RiskClient", "control"): "RiskControlClient",
        ("risk", "RiskClient", "current"): "RiskCurrentView | None",
        ("risk", "RiskClient", "events"): "RiskLiveSubscription",
        ("risk", "RiskEvent", "data"): "RiskDecisionEventPayload | RiskReservationEventPayload | RiskCircuitEventPayload | None",
        ("risk", "RiskEvent", "occurred_at_unix_nanos"): "UnixNanosRead",
        ("risk", "RiskEvent", "sequence"): "SequenceRead",
        ("risk", "RiskEventMetadata", "occurred_at_unix_nanos"): "UnixNanosRead",
        ("risk", "RiskEventMetadata", "sequence"): "SequenceRead",
        ("risk", "RiskLiveEventView", "data"): "RiskDecisionEventPayload | RiskReservationEventPayload | RiskCircuitEventPayload | None",
        ("risk", "RiskLiveSubscription", "close"): "None",
        ("risk", "RiskLiveSubscription", "poll_visit"): "int",
        ("reference", "ReferenceLiveEventView", "data"): "ReferenceExchange | ReferenceAsset | ReferenceInstrument | ReferenceListing | ReferenceMarket",
        ("reference", "ReferenceLiveSubscription", "close"): "None",
        ("reference", "ReferenceLiveSubscription", "poll_visit"): "int",
        ("reference", "ReferenceExchange", "id"): "ExchangeIdRead",
        ("reference", "ReferenceAsset", "id"): "AssetIdRead",
        ("reference", "ReferenceInstrumentRef", "id"): "InstrumentIdRead",
        ("reference", "ReferenceInstrument", "id"): "InstrumentIdRead",
        ("reference", "ReferenceInstrument", "ref"): "ReferenceInstrumentRef",
        ("reference", "ReferenceListing", "id"): "ListingIdRead",
        ("reference", "ReferenceMarket", "id"): "MarketIdRead",
        ("reference", "ReferenceMarket", "base_asset"): "AssetIdRead | None",
        ("reference", "ReferenceMarket", "quote_asset"): "AssetIdRead | None",
    }
)
MODULE_RETURN_TYPES = {
    (owner, "build_info"): "NativeBuildInfo" for owner in OWNERS
}
MODULE_RETURN_TYPES.update(
    {
        (owner, "decode_event"): f"{owner.title()}Event" for owner in OWNERS
    }
)

_EXACT_DECIMAL_INPUT = "Decimal | str | int"
_PRICE_INPUT = f"Price | PriceLike | {_EXACT_DECIMAL_INPUT}"
_QUANTITY_INPUT = f"Quantity | QuantityLike | {_EXACT_DECIMAL_INPUT}"
_SIGNED_QUANTITY_INPUT = (
    f"SignedQuantity | SignedQuantityLike | {_EXACT_DECIMAL_INPUT}"
)
_DECIMAL_VALUE_INPUT = "DecimalValue | Decimal | str | int"
_PRICE_VALUE_INPUT = f"PriceLike | {_DECIMAL_VALUE_INPUT}"
_QUANTITY_VALUE_INPUT = f"QuantityLike | {_DECIMAL_VALUE_INPUT}"
_MONEY_VALUE_INPUT = f"MoneyLike | {_DECIMAL_VALUE_INPUT}"
_RATE_VALUE_INPUT = f"RateLike | {_DECIMAL_VALUE_INPUT}"
PARAMETER_NAME_TYPES = {
    "account_id": "str",
    "account_ids": "list[str]",
    "account_watermark": "int",
    "active_only": "bool",
    "aeron_dir": "str",
    "asset": "str",
    "available": "str",
    "capital_group_id": "str",
    "causal_references": "list[str]",
    "channel": "str",
    "confidence_bps": "int",
    "control_socket": "str | Path",
    "demand_id": "str",
    "destination": "FundingLocation",
    "destination_lease_fence": "str",
    "equity": "str",
    "event_time_unix_nanos": "int",
    "expected_version": "int",
    "expires_at_unix_nanos": "int",
    "fee_amount": _SIGNED_QUANTITY_INPUT,
    "fee_asset": "str",
    "fill_id": "str",
    "freshness": "str",
    "idempotency_key": "str",
    "instance_id": "str",
    "instrument_id": "str",
    "kind": "str",
    "launch_id": "str",
    "mark_price": _PRICE_INPUT,
    "mutation_id": "str",
    "objective_id": "str",
    "observed_at_unix_nanos": "int",
    "observed_shortfall": _QUANTITY_INPUT,
    "occurred_at_unix_nanos": "int",
    "order_id": "str",
    "plan_id": "str",
    "priority": "str",
    "producer_incarnation": "int",
    "product_id": "str",
    "quantity": _QUANTITY_INPUT,
    "quote_asset": "str",
    "request_id": "str",
    "required_by_unix_nanos": "int",
    "risk_watermark": "int",
    "root": "str | Path",
    "segment": "str",
    "segment_key": "str",
    "segments": "list[str]",
    "sequence": "int",
    "settlement_asset": "str",
    "settlement_delta": _SIGNED_QUANTITY_INPUT,
    "side": "str",
    "socket_path": "str | Path",
    "status": "str",
    "strategy_decision_id": "str",
    "strategy_id": "str",
    "stream_id": "int",
    "timeout": "float",
    "total": "str",
    "trading_enabled": "bool",
    "version": "int",
    "view_root": "str | Path",
    "workspace_id": "str",
}
PARAMETER_TYPES = {
    ("account", None, "decode_event", "payload"): "bytes",
    ("account", "AccountControlClient", "advance_time", "request"): "AdvanceAccountTimeRequest",
    ("account", "AccountControlClient", "apply_simulated_capital_mutation", "request"): "SimulatedCapitalMutation",
    ("account", "AccountControlClient", "apply_simulated_settlement", "request"): "SimulatedSettlement",
    ("account", "AccountControlClient", "mark_to_market", "request"): "MarkToMarketRequest",
    ("account", "AccountControlClient", "query_simulated_capital_mutation", "request"): "SimulatedCapitalMutationQuery",
    ("account", "AccountControlClient", "reconcile", "request"): "AccountSegmentsRequest",
    ("account", "AccountControlClient", "refresh", "request"): "AccountSegmentsRequest",
    ("account", "SimulatedCapitalMutation", "__init__", "amount"): _QUANTITY_INPUT,
    ("account", "SimulatedSettlement", "__init__", "price"): _PRICE_INPUT,
    ("capital", None, "decode_event", "payload"): "bytes",
    ("capital", "CapitalControlClient", "cancel_funding_objective", "request"): "CancelFundingObjectiveRequest",
    ("capital", "CapitalControlClient", "observe_capital_demand", "request"): "ObserveCapitalDemandRequest",
    ("capital", "CapitalControlClient", "publish_funding_objective", "request"): "PublishFundingObjectiveRequest",
    ("capital", "CapitalControlClient", "query_capital_availability", "request"): "QueryCapitalAvailabilityRequest",
    ("capital", "CapitalControlClient", "reconcile_capital_plan", "request"): "ReconcileCapitalPlanRequest",
    ("capital", "CapitalCurrentSnapshot", "availability", "location"): "tuple[str, str, str, str]",
    ("capital", "FundingLocation", "__init__", "broker"): "str",
    ("capital", "FundingLocation", "__init__", "location"): "FundingLocation",
    ("capital", "ObserveCapitalDemandRequest", "__init__", "destination"): "FundingLocation",
    ("capital", "PublishFundingObjectiveRequest", "__init__", "desired_available"): _QUANTITY_INPUT,
    ("capital", "PublishFundingObjectiveRequest", "__init__", "destination"): "FundingLocation",
    ("capital", "QueryCapitalAvailabilityRequest", "__init__", "location"): "FundingLocation",
}
PARAMETER_TYPES.update(
    {
        # Execution accepts compatibility strings at the native construction
        # boundary, but composite requests and semantic decimals stay typed.
        ("execution", None, "decode_event", "payload"): "bytes",
        ("execution", "CancelOrderRequest", "__init__", "reason"): "str",
        ("execution", "ExecutionControlClient", "order_audit", "query"): "ExecutionOrderAuditQuery",
        ("execution", "ExecutionEvent", "simulation_fill", "intent_id"): "str",
        ("execution", "ExecutionEvent", "simulation_fill", "quantity"): _QUANTITY_VALUE_INPUT,
        ("execution", "ExecutionEvent", "simulation_fill", "price"): _PRICE_VALUE_INPUT,
        ("execution", "ExecutionEvent", "simulation_intent_update", "intent_id"): "str",
        ("execution", "ExecutionEvent", "simulation_intent_update", "previous_status"): "str",
        ("execution", "ExecutionEvent", "simulation_intent_update", "order_ids"): "Sequence[str]",
        ("execution", "ExecutionEvent", "simulation_intent_update", "reason"): "str",
        ("execution", "ExecutionOrderAuditQuery", "__init__", "remote_order_id"): "str",
        ("execution", "ExecutionOrderAuditQuery", "__init__", "lifecycle"): "str",
        ("execution", "ExecutionOrderAuditQuery", "__init__", "since_unix_nanos"): "int",
        ("execution", "ExecutionOrderAuditQuery", "__init__", "until_unix_nanos"): "int",
        ("execution", "ExecutionOrderAuditQuery", "__init__", "limit"): "int",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "time_in_force"): "str",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "reduce_only"): "bool",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "post_only"): "bool",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "position_side"): "str",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "wallet_type"): "str",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "trading_session"): "str",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "tokenize"): "bool",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "split"): "SplitOrderPolicyRequest",
        ("execution", "ExecutionOrderOptionsRequest", "__init__", "maker"): "MakerExecutionPolicyRequest",
        ("execution", "ExecutionRoutesQuery", "__init__", "market_id"): "str",
        ("execution", "ExecutionRoutesQuery", "__init__", "broker_id"): "str",
        ("execution", "IntentAdmissionEvidenceRequest", "__init__", "source"): "str",
        ("execution", "IntentAdmissionEvidenceRequest", "__init__", "decision_id"): "str",
        ("execution", "IntentAdmissionEvidenceRequest", "__init__", "outcome"): "str",
        ("execution", "IntentAdmissionEvidenceRequest", "__init__", "original_intent"): "ExecutionIntentRequest",
        ("execution", "IntentAdmissionEvidenceRequest", "__init__", "effective_intent"): "ExecutionIntentRequest",
        ("execution", "ReconcileExecutionRequest", "__init__", "execution_route_id"): "str",
        ("execution", "ReconcileExecutionRequest", "__init__", "reason"): "str",
        ("execution", "SubmitIntentRequest", "__init__", "intent"): "ExecutionIntentRequest",
        ("execution", "SubmitIntentRequest", "__init__", "command_id"): "str",
        ("execution", "SubmitIntentRequest", "__init__", "caller_id"): "str",
        ("execution", "SubmitIntentRequest", "__init__", "admission_evidence"): "IntentAdmissionEvidenceRequest",

        ("market", None, "decode_event", "payload"): "bytes",
        ("market", "ExpiryRange", "__init__", "from_unix_nanos"): "int",
        ("market", "ExpiryRange", "__init__", "to_unix_nanos"): "int",
        ("market", "ExpiryRange", "__init__", "from_days"): "int",
        ("market", "ExpiryRange", "__init__", "to_days"): "int",
        ("market", "ExpiryRange", "between_unix_nanos", "from_unix_nanos"): "int | None",
        ("market", "ExpiryRange", "between_unix_nanos", "to_unix_nanos"): "int | None",
        ("market", "ExpiryRange", "next_days", "days"): "int",
        ("market", "ExpiryRange", "next_days", "from_days"): "int",
        ("market", "MarketControlClient", "data_routes", "market_id"): "str",
        ("market", "MarketControlClient", "data_routes", "observation_kind"): "str",
        ("market", "MarketControlClient", "data_routes", "provider"): "str",
        ("market", "MarketControlClient", "data_routes", "configured_only"): "bool",
        ("market", "MarketControlClient", "data_routes", "ready_only"): "bool",
        ("market", "MarketControlClient", "subscribe", "request"): "MarketSubscriptionRequest",
        ("market", "MarketControlClient", "unsubscribe", "subscription_id"): "str",
        ("market", "MarketCurrentView", "get", "key"): "MarketViewKey",
        ("market", "MarketSubscriptionRequest", "__init__", "target"): "MarketTarget",
        ("market", "MarketSubscriptionRequest", "__init__", "observations"): "Sequence[ObservationRequirement]",
        ("market", "MarketSubscriptionRequest", "__init__", "provider_preference"): "ProviderPreference",
        ("market", "MarketTarget", "consolidated_instrument", "network_id"): "str",
        ("market", "MarketTarget", "market", "market_id"): "str",
        ("market", "MarketTarget", "options", "underlying_market_id"): "str",
        ("market", "MarketTarget", "options", "underlying_instrument_id"): "str",
        ("market", "MarketTarget", "options", "expiry_from_unix_nanos"): "int",
        ("market", "MarketTarget", "options", "expiry_to_unix_nanos"): "int",
        ("market", "MarketTarget", "options", "strike_lower"): "str",
        ("market", "MarketTarget", "options", "strike_upper"): "str",
        ("market", "MarketTarget", "options", "option_right"): "str",
        ("market", "MarketTarget", "options", "limit"): "int",
        ("market", "MarketTarget", "options", "progressive"): "bool",
        ("market", "MarketViewKey", "__init__", "scope_key"): "str",
        ("market", "MarketViewKey", "__init__", "provider"): "str",
        ("market", "MarketViewKey", "__init__", "qualifier"): "str",
        ("market", "MarketViewKind", "__init__", "value"): "str",
        ("market", "ObservationRequirement", "__init__", "qualifier"): "str",
        ("market", "ObservationRequirement", "bar", "timeframe"): "str",
        ("market", "ObservationRequirement", "from_selector", "selector"): "str",
        ("market", "OptionFilter", "__init__", "expiry"): "ExpiryRange",
        ("market", "OptionFilter", "__init__", "strike"): "StrikeRange",
        ("market", "OptionFilter", "__init__", "right"): "OptionRight | str",
        ("market", "OptionFilter", "__init__", "limit"): "int",
        ("market", "OptionRight", "__init__", "value"): "str",
        ("market", "Options", "__init__", "underlying"): "str",
        ("market", "Options", "__init__", "filter"): "OptionFilter",
        ("market", "Options", "on", "underlying"): "str",
        ("market", "Options", "where", "filter"): "OptionFilter",
        ("market", "Options", "where", "expiry"): "ExpiryRange",
        ("market", "Options", "where", "strike"): "StrikeRange",
        ("market", "Options", "where", "right"): "OptionRight | str",
        ("market", "Options", "where", "limit"): "int",
        ("market", "Provider", "__init__", "value"): "str",
        ("market", "ProviderPreference", "prefer", "providers"): "str",
        ("market", "ProviderPreference", "require", "providers"): "str",
        ("market", "StrikeRange", "around_spot", "percent"): "str",
        ("market", "StrikeRange", "between", "lower"): "str",
        ("market", "StrikeRange", "between", "upper"): "str",

        ("risk", None, "decode_event", "payload"): "bytes",
        ("risk", None, "indexed_environment_path", "actor_id"): "str",
        ("risk", "AuthorizeRequest", "__init__", "reservation_id"): "str",
        ("risk", "AuthorizeRequest", "__init__", "exchange_id"): "str",
        ("risk", "AuthorizeRequest", "__init__", "proposal"): "TradeRiskProposal",
        ("risk", "AuthorizeRequest", "__init__", "at_unix_nanos"): "int",
        ("risk", "AuthorizeRequest", "__init__", "reservation_ttl_nanos"): "int",
        ("risk", "AuthorizeRequest", "__init__", "dependency_generation"): "int",
        ("risk", "AuthorizeRequest", "__init__", "dependency_event_sequence"): "int",
        ("risk", "AuthorizeRequest", "__init__", "context"): "RiskContext",
        ("risk", "CloseCircuitRequest", "__init__", "scope"): "RiskScope",
        ("risk", "CloseCircuitRequest", "__init__", "at_unix_nanos"): "int",
        ("risk", "ConsumeReservationRequest", "__init__", "reservation_id"): "str",
        ("risk", "ConsumeReservationRequest", "__init__", "at_unix_nanos"): "int",
        ("risk", "OpenCircuitRequest", "__init__", "scope"): "RiskScope",
        ("risk", "OpenCircuitRequest", "__init__", "at_unix_nanos"): "int",
        ("risk", "OpenCircuitRequest", "__init__", "reason"): "str",
        ("risk", "OpenCircuitRequest", "__init__", "reset_at_unix_nanos"): "int",
        ("risk", "PublishPolicyRequest", "__init__", "policy_id"): "str",
        ("risk", "PublishPolicyRequest", "__init__", "scope"): "RiskScope",
        ("risk", "PublishPolicyRequest", "__init__", "metric"): "str",
        ("risk", "PublishPolicyRequest", "__init__", "enforcement"): "str",
        ("risk", "PublishPolicyRequest", "__init__", "valid_from_unix_nanos"): "int",
        ("risk", "PublishPolicyRequest", "__init__", "valid_until_unix_nanos"): "int",
        ("risk", "PublishPolicyRequest", "__init__", "window_nanos"): "int",
        ("risk", "ReleaseReservationRequest", "__init__", "reservation_id"): "str",
        ("risk", "ReleaseReservationRequest", "__init__", "at_unix_nanos"): "int",
        ("risk", "ResizeReservationRequest", "__init__", "reservation_id"): "str",
        ("risk", "ResizeReservationRequest", "__init__", "at_unix_nanos"): "int",
        ("risk", "RiskClient", "__init__", "actor_id"): "str",
        ("risk", "RiskContext", "__init__", "account_snapshot_watermark"): "int",
        ("risk", "RiskContext", "__init__", "market_freshness_watermark"): "int",
        ("risk", "RiskContext", "__init__", "portfolio_version"): "int",
        ("risk", "RiskContext", "__init__", "market_is_fresh"): "bool",
        ("risk", "RiskContext", "__init__", "leverage_bps"): "int",
        ("risk", "RiskContext", "__init__", "price_deviation_bps"): "int",
        ("risk", "RiskCurrentView", "__init__", "actor_id"): "str",
        ("risk", "RiskEvent", "circuit_changed", "state"): "str",
        ("risk", "RiskEvent", "circuit_changed", "exchange_id"): "str",
        ("risk", "RiskEvent", "circuit_changed", "reason"): "str",
        ("risk", "RiskEvent", "circuit_changed", "opened_at_unix_nanos"): "int",
        ("risk", "RiskEvent", "circuit_changed", "reset_at_unix_nanos"): "int",
        ("risk", "RiskEvent", "decision_evaluated", "decision_id"): "str",
        ("risk", "RiskEvent", "decision_evaluated", "allowed"): "bool",
        ("risk", "RiskEvent", "decision_evaluated", "degraded"): "bool",
        ("risk", "RiskEvent", "decision_evaluated", "reason_codes"): "list[str]",
        ("risk", "RiskEvent", "decision_evaluated", "violations"): "list[str]",
        ("risk", "RiskEvent", "reservation_changed", "reservation_id"): "str",
        ("risk", "RiskScope", "__init__", "exchange_id"): "str",
        ("risk", "TradeRiskProposal", "__init__", "initial_margin_rate_bps"): "int",
        ("risk", "TradeRiskProposal", "__init__", "account_segment"): "str",
        ("risk", "TradeRiskProposal", "__init__", "collateral_asset"): "str",
        ("risk", "TradeRiskProposal", "__init__", "margin_rule_id"): "str",
        ("risk", "TradeRiskProposal", "__init__", "reduce_only"): "bool",
    }
)

for method_name, request_type in {
    "advance_time": "AdvanceRiskTimeRequest",
    "authorize_and_reserve": "AuthorizeRequest",
    "close_circuit": "CloseCircuitRequest",
    "consume_reservation": "ConsumeReservationRequest",
    "open_circuit": "OpenCircuitRequest",
    "post_trade_check": "AuthorizeRequest",
    "pre_trade_check": "AuthorizeRequest",
    "publish_policy": "PublishPolicyRequest",
    "release_reservation": "ReleaseReservationRequest",
    "resize_reservation": "ResizeReservationRequest",
}.items():
    PARAMETER_TYPES[("risk", "RiskControlClient", method_name, "request")] = request_type

for method_name in (
    "bar",
    "freshness",
    "funding_rate",
    "greeks",
    "index_price",
    "mark_price",
    "open_interest",
    "order_book",
    "quote",
    "rate",
    "ticker_24h",
):
    PARAMETER_TYPES[("market", "MarketCurrentView", method_name, "scope_key")] = "str"
    PARAMETER_TYPES[("market", "MarketCurrentView", method_name, "provider")] = "str"
    PARAMETER_TYPES[("market", "MarketCurrentView", method_name, "qualifier")] = "str"

for method_name, fields in {
    "simulation_bar": {
        "market_id": "str",
        "provider": "str",
        "bar_spec_id": "str",
        "open": _PRICE_VALUE_INPUT,
        "high": _PRICE_VALUE_INPUT,
        "low": _PRICE_VALUE_INPUT,
        "close": _PRICE_VALUE_INPUT,
        "volume": _QUANTITY_VALUE_INPUT,
    },
    "simulation_quote": {
        "market_id": "str",
        "provider": "str",
        "bid_price": _PRICE_VALUE_INPUT,
        "bid_quantity": _QUANTITY_VALUE_INPUT,
        "ask_price": _PRICE_VALUE_INPUT,
        "ask_quantity": _QUANTITY_VALUE_INPUT,
    },
}.items():
    for field, annotation in fields.items():
        PARAMETER_TYPES[("market", "MarketEvent", method_name, field)] = annotation
for owner in OWNERS:
    class_prefix = owner.title()
    PARAMETER_TYPES[
        (owner, f"{class_prefix}LiveSubscription", "__init__", "max_payload_len")
    ] = "int"
    PARAMETER_TYPES[
        (owner, f"{class_prefix}LiveSubscription", "poll_visit", "visitor")
    ] = f"Callable[[{class_prefix}LiveEventView], None]"
    PARAMETER_TYPES[
        (owner, f"{class_prefix}LiveSubscription", "poll_visit", "fragment_limit")
    ] = "int"
MODULE_RETURN_TYPES.update(
    {
        (owner, "indexed_environment_path"): "Path"
        for owner in OWNERS
        if owner != "reference"
    }
)
STUB_IMPORTS = {
    "account": (
        "from collections.abc import Callable",
        "from decimal import Decimal",
        "from kairospy.primitives.decimal import Money, MoneyLike, Price, PriceLike, Quantity, QuantityLike, SignedQuantity, SignedQuantityLike",
        "from kairospy.primitives.account import AccountIdRead, BrokerIdRead, SegmentKeyRead",
        "from kairospy.primitives.capital import EarnProductIdRead",
        "from kairospy.primitives.execution import OrderIdRead",
        "from kairospy.primitives.integration import RemoteOrderIdRead",
        "from kairospy.primitives.reference import AssetIdRead, InstrumentIdRead, MarketIdRead",
        "from kairospy.primitives.runtime import EventIdRead, InstanceIdRead, LaunchIdRead, ProducerIdRead, RequestIdRead, WorkspaceIdRead",
        "from kairospy.primitives.time import BasisPointsRead, DurationNanosRead, GenerationRead, SequenceRead, UnixNanosRead",
    ),
    "capital": (
        "from collections.abc import Callable",
        "from decimal import Decimal",
        "from kairospy.primitives.decimal import Quantity, QuantityLike",
        "from kairospy.primitives.account import AccountIdRead, BrokerIdRead, SegmentKeyRead",
        "from kairospy.primitives.capital import CapitalDemandIdRead, CapitalGroupIdRead, CapitalOperationIdRead, CapitalPlanIdRead, CapitalReservationIdRead, CapitalRouteIdRead, CapitalSourceAuthorityRead, EarnProductIdRead, FundingObjectiveIdRead",
        "from kairospy.primitives.reference import AssetIdRead",
        "from kairospy.primitives.runtime import EventIdRead, IdempotencyKeyRead, InstanceIdRead, LaunchIdRead, ProducerIdRead, RequestIdRead, StrategyDecisionIdRead, StrategyIdRead, WorkspaceIdRead",
        "from kairospy.primitives.time import BasisPointsRead, DurationNanosRead, GenerationRead, SequenceRead, UnixNanosRead",
    ),
    "execution": (
        "from collections.abc import Callable, Sequence",
        "from decimal import Decimal",
        "from kairospy.primitives.decimal import DecimalValue, MoneyLike, PriceLike, QuantityLike, RateLike, SignedQuantityLike",
        "from kairospy.primitives.account import AccountIdRead, BrokerIdRead, SegmentKeyRead",
        "from kairospy.primitives.execution import ClientOrderIdRead, ExecutionChannelCodeRead, ExecutionRouteIdRead, FillIdRead, IntentIdRead, LegIdRead, OrderEntrySymbolRead, OrderIdRead, OrderOptionCodeRead",
        "from kairospy.primitives.integration import RemoteOrderIdRead",
        "from kairospy.primitives.reference import AssetIdRead, InstrumentIdRead, MarketIdRead, SymbolRead",
        "from kairospy.primitives.risk import DecisionIdRead, ReservationIdRead",
        "from kairospy.primitives.runtime import EventIdRead, IdempotencyKeyRead, InstanceIdRead, LaunchIdRead, ProducerIdRead, StrategyDecisionIdRead, StrategyIdRead, WorkspaceIdRead",
        "from kairospy.primitives.time import BasisPointsRead, DurationNanosRead, GenerationRead, SequenceRead, UnixNanosRead",
    ),
    "market": (
        "from collections.abc import Callable, Sequence",
        "from decimal import Decimal",
        "from kairospy.primitives.decimal import DecimalValue, MoneyLike, PriceDeltaLike, PriceLike, QuantityLike, RateLike, SignedQuantityLike",
        "from kairospy.primitives.reference import InstrumentIdRead, MarketIdRead",
        "from kairospy.primitives.market import SubscriptionIdRead, SubscriptionSymbolRead",
        "from kairospy.primitives.runtime import ActorIdRead, EventIdRead, InstanceIdRead, LaunchIdRead, ProducerIdRead, RequestIdRead, StrategyIdRead, WorkspaceIdRead",
        "from kairospy.primitives.time import DurationNanosRead, GenerationRead, SequenceRead, UnixNanosRead",
    ),
    "reference": (
        "from os import PathLike",
        "from collections.abc import Callable",
        "from typing import Literal, TypeAlias",
        "from kairospy.primitives.decimal import MoneyLike, PriceLike, QuantityLike, RateLike",
        "from kairospy.primitives.reference import AssetClass, AssetIdRead, ExchangeIdRead, InstrumentIdRead, InstrumentKind, IssuerIdRead, ListingIdRead, MarketIdRead, ReferenceStatus, SymbolRead",
        "from kairospy.primitives.runtime import EventIdRead, InstanceIdRead, LaunchIdRead, ProducerIdRead, WorkspaceIdRead",
        "from kairospy.primitives.time import GenerationRead, Sequence, SequenceRead, UnixNanosRead",
    ),
    "risk": (
        "from collections.abc import Callable",
        "from decimal import Decimal",
        "from kairospy.primitives.decimal import DecimalValue, MoneyLike, RateLike",
        "from kairospy.primitives.account import AccountIdRead",
        "from kairospy.primitives.reference import ExchangeIdRead, InstrumentIdRead",
        "from kairospy.primitives.risk import DecisionIdRead, PolicyIdRead, ReservationIdRead",
        "from kairospy.primitives.runtime import ActorIdRead, EventIdRead, IdempotencyKeyRead, InstanceIdRead, LaunchIdRead, ProducerIdRead, RequestIdRead, StrategyIdRead, WorkspaceIdRead",
        "from kairospy.primitives.time import DurationNanosRead, GenerationRead, SequenceRead, UnixNanosRead",
    ),
}
STUB_POSTLUDE = {
    "reference": (
        "ReferenceEventKind: TypeAlias = Literal['exchange_upserted', 'exchange_updated', 'asset_upserted', 'asset_updated', 'instrument_upserted', 'instrument_updated', 'listing_upserted', 'listing_updated', 'market_upserted', 'market_updated']",
        "ReferenceEventPayload: TypeAlias = ReferenceExchange | ReferenceAsset | ReferenceInstrument | ReferenceListing | ReferenceMarket",
    )
}
COMMON_IDENTITY_PROPERTY_TYPES = {
    "actor_id": "ActorIdRead",
    "causation_id": "EventIdRead | None",
    "correlation_id": "EventIdRead | None",
    "event_id": "EventIdRead",
    "idempotency_key": "IdempotencyKeyRead",
    "instance_id": "InstanceIdRead",
    "launch_id": "LaunchIdRead",
    "producer": "ProducerIdRead",
    "request_id": "RequestIdRead",
    "strategy_decision_id": "StrategyDecisionIdRead",
    "workspace_id": "WorkspaceIdRead",
}
IDENTITY_PROPERTY_TYPES = {
    "account": {
        "account_id": "AccountIdRead",
        "broker": "BrokerIdRead",
        "segment_key": "SegmentKeyRead",
        "instrument_id": "InstrumentIdRead",
        "order_id": "OrderIdRead",
        "asset": "AssetIdRead",
        "asset_id": "AssetIdRead",
        "market_id": "MarketIdRead",
        "request_id": "RequestIdRead",
        "product_id": "EarnProductIdRead",
        "remote_order_id": "RemoteOrderIdRead",
    },
    "capital": {
        "account_id": "AccountIdRead",
        "broker": "BrokerIdRead",
        "segment": "SegmentKeyRead",
        "capital_group_id": "CapitalGroupIdRead",
        "objective_id": "FundingObjectiveIdRead",
        "demand_id": "CapitalDemandIdRead",
        "plan_id": "CapitalPlanIdRead",
        "reservation_id": "CapitalReservationIdRead",
        "operation_id": "CapitalOperationIdRead",
        "route_id": "CapitalRouteIdRead",
        "earn_product_id": "EarnProductIdRead",
        "selected_earn_product_id": "EarnProductIdRead",
        "product_id": "EarnProductIdRead",
        "required_source_authority": "CapitalSourceAuthorityRead",
        "asset": "AssetIdRead",
        "request_id": "RequestIdRead",
        "strategy_id": "StrategyIdRead",
    },
    "execution": {
        "account_id": "AccountIdRead",
        "broker": "BrokerIdRead",
        "broker_id": "BrokerIdRead",
        "collateral_asset": "AssetIdRead",
        "decision_id": "DecisionIdRead",
        "execution_route_id": "ExecutionRouteIdRead",
        "route_id": "ExecutionRouteIdRead",
        "fee_currency": "AssetIdRead",
        "fill_id": "FillIdRead",
        "instrument_id": "InstrumentIdRead",
        "intent_id": "IntentIdRead",
        "leg_id": "LegIdRead",
        "market_id": "MarketIdRead",
        "order_id": "OrderIdRead",
        "reservation_id": "ReservationIdRead",
        "segment": "SegmentKeyRead",
        "segment_key": "SegmentKeyRead",
        "strategy_id": "StrategyIdRead",
        "symbol": "SymbolRead",
        "client_order_id": "ClientOrderIdRead",
        "remote_order_id": "RemoteOrderIdRead",
        "destination_market_id": "MarketIdRead",
        "execution_channel": "ExecutionChannelCodeRead",
        "order_entry_symbol": "OrderEntrySymbolRead",
        "order_option": "OrderOptionCodeRead",
    },
    "market": {
        "instrument_id": "InstrumentIdRead",
        "market_id": "MarketIdRead",
        "subscription_id": "SubscriptionIdRead",
        "strategy_id": "StrategyIdRead",
        "symbol": "SubscriptionSymbolRead",
    },
    "reference": {
        "asset_id": "AssetIdRead",
        "base_asset_id": "AssetIdRead | None",
        "quote_asset_id": "AssetIdRead | None",
        "exchange_id": "ExchangeIdRead",
        "instrument_id": "InstrumentIdRead",
        "underlying_instrument_id": "InstrumentIdRead | None",
        "listing_id": "ListingIdRead | None",
        "market_id": "MarketIdRead | None",
        "symbol": "SymbolRead",
    },
    "risk": {
        "account_id": "AccountIdRead",
        "decision_id": "DecisionIdRead",
        "exchange_id": "ExchangeIdRead",
        "instrument_id": "InstrumentIdRead",
        "policy_id": "PolicyIdRead",
        "request_id": "RequestIdRead",
        "reservation_id": "ReservationIdRead",
        "strategy_id": "StrategyIdRead",
    },
}
PROPERTY_TYPES = {
    ("account", class_name, field): value_type
    for class_name, fields in {
        "AccountBalanceCurrent": {
            "available": "QuantityLike",
            "reserved": "QuantityLike",
            "total": "QuantityLike",
        },
        "AccountBalanceEvent": {
            "available": "QuantityLike | None",
            "borrowed": "QuantityLike | None",
            "interest": "QuantityLike | None",
            "locked": "QuantityLike | None",
            "total": "QuantityLike",
        },
        "AccountCollateralCurrent": {
            "available": "QuantityLike | None",
            "borrowed": "QuantityLike | None",
            "interest": "QuantityLike | None",
            "locked": "QuantityLike | None",
            "total": "QuantityLike",
        },
        "AccountEarnHoldingCurrent": {
            "principal": "QuantityLike",
            "redeemable": "QuantityLike | None",
        },
        "AccountEarnHoldingEvent": {
            "principal": "QuantityLike",
            "redeemable": "QuantityLike | None",
        },
        "AccountObservedOrderCurrent": {
            "filled_quantity": "QuantityLike",
            "quantity": "QuantityLike",
        },
        "AccountObservedOrderEvent": {
            "filled_quantity": "QuantityLike",
            "quantity": "QuantityLike",
        },
        "AccountPositionCurrent": {
            "average_price": "PriceLike | None",
            "market_value": "MoneyLike | None",
            "quantity": "SignedQuantityLike",
            "unrealized_pnl": "MoneyLike | None",
        },
        "AccountPositionEvent": {
            "average_price": "PriceLike | None",
            "mark_price": "PriceLike | None",
            "quantity": "SignedQuantityLike",
            "realized_pnl": "MoneyLike | None",
            "unrealized_pnl": "MoneyLike | None",
        },
        "AccountSegmentCurrent": {"equity": "MoneyLike | None"},
        "AccountValuationEvent": {
            "equity": "MoneyLike | None",
            "initial_equity": "MoneyLike | None",
            "net_profit": "MoneyLike | None",
        },
    }.items()
    for field, value_type in fields.items()
}
PROPERTY_TYPES.update(
    {
        ("capital", class_name, field): "QuantityLike" + optional
        for class_name, fields in {
            "PublishFundingObjectiveRequest": {"desired_available": ""},
            "ObserveCapitalDemandRequest": {"observed_shortfall": ""},
            "CapitalAvailabilityResponse": {
                "policy_minimum": "",
                "policy_default_target": "",
                "policy_maximum": "",
                "desired_target": "",
                "observed_available": "",
                "effective_target": "",
                "deficit": "",
            },
            "FundingHorizon": {"desired_available": ""},
            "CapitalAvailability": {
                "desired_target": "",
                "observed_available": "",
                "effective_target": "",
                "deficit": "",
            },
            "FundingObjective": {"desired_available": ""},
            "CapitalDemand": {"observed_shortfall": ""},
            "CapitalPolicy": {
                "minimum": "",
                "default_target": "",
                "maximum": "",
                "stress_buffer": "",
                "minimum_movement": "",
                "hysteresis": "",
            },
            "CapitalEarnHolding": {"principal": "", "redeemable_amount": ""},
            "CapitalFacts": {"observed_available": "", "risk_capacity": ""},
            "CapitalPlan": {
                "amount": "",
                "source_observed_available": "",
                "destination_observed_available": "",
                "redemption_observed_available": " | None",
                "earn_principal_before": "",
            },
            "CapitalRoute": {"per_operation_limit": "", "daily_limit": ""},
            "CapitalReservation": {"amount": ""},
        }.items()
        for field, optional in fields.items()
    }
)


def _split_generic(value: str) -> tuple[str, str] | None:
    value = value.strip()
    opening = value.find("<")
    if opening < 0 or not value.endswith(">"):
        return None
    return value[:opening].strip(), value[opening + 1 : -1].strip()


def _rust_type_to_python(value: str) -> str | None:
    """Map mechanically representable PyO3 output types.

    Business identities and semantic decimals are deliberately handled by the
    explicit owner maps above; this function only preserves representation,
    container shape, and optionality from the Rust companion.
    """

    value = value.strip()
    while value.startswith("&"):
        value = value[1:].strip()
        if value.startswith("'"):
            value = value.split(maxsplit=1)[1]
    direct = {
        "bool": "bool",
        "String": "str",
        "str": "str",
        "PathBuf": "Path",
        "f32": "float",
        "f64": "float",
        "i8": "int",
        "i16": "int",
        "i32": "int",
        "i64": "int",
        "isize": "int",
        "u8": "int",
        "u16": "int",
        "u32": "int",
        "u64": "int",
        "usize": "int",
    }
    if value in direct:
        return direct[value]
    generic = _split_generic(value)
    if generic is not None:
        outer, inner = generic
        mapped = _rust_type_to_python(inner)
        if mapped is None:
            return None
        if outer == "Option":
            return f"{mapped} | None"
        if outer == "Vec":
            return f"list[{mapped}]"
        if outer in {"Py", "Bound"}:
            return mapped
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        if value in {"NativeDecimal", "PyAny", "Self"}:
            return None
        return value.removeprefix("Native")
    return None


def _rust_property_types(owner: str) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    source_root = ROOT / "crates" / "modules" / owner / "contract" / "py" / "src"
    for source_path in sorted(source_root.glob("*.rs")):
        lines = source_path.read_text(encoding="utf-8").splitlines()
        pending_pyclass = False
        python_name: str | None = None
        current_class: str | None = None
        depth = 0
        pending_getter = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#[pyclass"):
                pending_pyclass = True
                python_name = None
            if pending_pyclass:
                name_match = re.search(r'name\s*=\s*"([^"]+)"', stripped)
                if name_match is not None:
                    python_name = name_match.group(1)
                struct_match = re.match(
                    r"(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)", stripped
                )
                if struct_match is not None:
                    current_class = python_name or struct_match.group(1)
                    depth = line.count("{") - line.count("}")
                    pending_pyclass = False
                    pending_getter = False
                    continue
            if current_class is None:
                continue
            depth += line.count("{") - line.count("}")
            if "#[pyo3(get)]" in stripped:
                pending_getter = True
                continue
            if pending_getter:
                field_match = re.match(
                    r"(?:pub\s+)?(\w+)\s*:\s*(.+),$", stripped
                )
                if field_match is not None:
                    mapped = _rust_type_to_python(field_match.group(2))
                    if mapped is not None:
                        mapped = _semantic_integer_property_type(
                            field_match.group(1), mapped
                        )
                        result[(owner, current_class, field_match.group(1))] = mapped
                    pending_getter = False
            if depth <= 0:
                current_class = None
                pending_getter = False
    return result


def _rust_method_return_types(owner: str) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    source_root = ROOT / "crates" / "modules" / owner / "contract" / "py" / "src"
    for source_path in sorted(source_root.glob("*.rs")):
        lines = source_path.read_text(encoding="utf-8").splitlines()
        class_names: dict[str, str] = {}
        pending_pyclass = False
        python_name: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#[pyclass"):
                pending_pyclass = True
                python_name = None
            if not pending_pyclass:
                continue
            name_match = re.search(r'name\s*=\s*"([^"]+)"', stripped)
            if name_match is not None:
                python_name = name_match.group(1)
            struct_match = re.match(
                r"(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)", stripped
            )
            if struct_match is not None:
                rust_name = struct_match.group(1)
                class_names[rust_name] = python_name or rust_name
                pending_pyclass = False

        in_pymethods = False
        current_class: str | None = None
        depth = 0
        pending_new = False
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped.startswith("#[pymethods]"):
                in_pymethods = True
                current_class = None
                pending_new = False
                index += 1
                continue
            if in_pymethods and current_class is None:
                impl_match = re.match(r"impl\s+(\w+)", stripped)
                if impl_match is not None:
                    current_class = class_names.get(impl_match.group(1))
                    if current_class is None:
                        in_pymethods = False
                    else:
                        depth = lines[index].count("{") - lines[index].count("}")
                index += 1
                continue
            if current_class is None:
                index += 1
                continue
            if stripped.startswith("#[new]"):
                pending_new = True
            function_match = re.search(r"\bfn\s+(\w+)\s*\(", stripped)
            if function_match is not None:
                signature_lines = [stripped]
                paren_depth = stripped.count("(") - stripped.count(")")
                cursor = index + 1
                signature_end = index
                while cursor < len(lines) and (paren_depth > 0 or "{" not in " ".join(signature_lines)):
                    part = lines[cursor].strip()
                    signature_lines.append(part)
                    signature_end = cursor
                    paren_depth += part.count("(") - part.count(")")
                    if paren_depth <= 0 and ("{" in part or part.endswith(";")):
                        break
                    cursor += 1
                signature = " ".join(signature_lines)
                method_name = "__init__" if pending_new else function_match.group(1)
                if "->" not in signature:
                    mapped = "None"
                else:
                    rust_return = signature.split("->", 1)[1].split("{", 1)[0].strip()
                    rust_return = rust_return.split(" where ", 1)[0].strip()
                    mapped = (
                        current_class
                        if rust_return == "Self"
                        else _rust_type_to_python(rust_return)
                    )
                    if mapped is None:
                        generic = _split_generic(rust_return)
                        if generic is not None and generic[0] in {"PyResult", "Result"}:
                            inner = generic[1].split(",", 1)[0].strip()
                            if inner == "Self":
                                mapped = current_class
                            elif inner == "()":
                                mapped = "None"
                            else:
                                mapped = _rust_type_to_python(inner)
                if mapped is not None:
                    result[(owner, current_class, method_name)] = mapped
                pending_new = False
                # A multi-line signature commonly contains the function's opening
                # brace. Count every consumed line so the function's closing brace
                # cannot be mistaken for the end of the surrounding impl block.
                depth += sum(
                    lines[line_index].count("{") - lines[line_index].count("}")
                    for line_index in range(index, signature_end + 1)
                )
                index = signature_end + 1
            else:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            if depth <= 0:
                current_class = None
                in_pymethods = False
                pending_new = False
    return result


def _semantic_integer_property_type(name: str, mapped: str) -> str:
    optional = mapped.endswith(" | None")
    base = mapped.removesuffix(" | None")
    if base != "int":
        return mapped
    semantic: str | None = None
    if name.endswith("_unix_nanos"):
        semantic = "UnixNanosRead"
    elif name.endswith("_nanos"):
        semantic = "DurationNanosRead"
    elif name.endswith("_bps"):
        semantic = "BasisPointsRead"
    elif name in {
        "applied_event_sequence",
        "dependency_event_sequence",
        "event_sequence",
        "journal_sequence",
        "sequence",
    } or name.endswith("_watermark"):
        semantic = "SequenceRead"
    elif name in {"dependency_generation", "generation", "risk_generation"}:
        semantic = "GenerationRead"
    if semantic is None:
        return mapped
    return semantic + (" | None" if optional else "")
PROPERTY_TYPES.update(
    {
        ("reference", "ReferenceInstrument", "strike"): "PriceLike | None",
        ("reference", "ReferenceListing", "listing_id"): "ListingIdRead",
        ("reference", "ReferenceMarket", "price_tick"): "PriceLike | None",
        ("reference", "ReferenceMarket", "quantity_tick"): "QuantityLike | None",
        ("reference", "ReferenceEvent", "data"): "ReferenceExchange | ReferenceAsset | ReferenceInstrument | ReferenceListing | ReferenceMarket",
        ("reference", "ReferenceMarket", "minimum_quantity"): "QuantityLike | None",
        ("reference", "ReferenceMarket", "minimum_notional"): "MoneyLike | None",
        ("reference", "ReferenceMarket", "contract_size"): "RateLike | None",
        ("reference", "ReferenceTradingRules", "price_increment"): "PriceLike | None",
        ("reference", "ReferenceTradingRules", "quantity_increment"): "QuantityLike | None",
        ("reference", "ReferenceTradingRules", "minimum_quantity"): "QuantityLike | None",
        ("reference", "ReferenceTradingRules", "minimum_notional"): "MoneyLike | None",
        ("reference", "ReferenceTradingRules", "contract_multiplier"): "RateLike | None",
    }
)


class _RenderedAnnotation:
    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


def _signature(
    value: object,
    *,
    owner: str | None = None,
    class_name: str | None = None,
    method_name: str | None = None,
    existing_parameter_types: dict[tuple[str | None, str, str], str] | None = None,
) -> str:
    try:
        signature = inspect.signature(cast(Callable[..., object], value))
    except (TypeError, ValueError):
        return "(*args, **kwargs)"
    if owner is None or method_name is None:
        return str(signature)
    parameters = []
    for parameter in signature.parameters.values():
        annotation = PARAMETER_TYPES.get(
            (owner, class_name, method_name, parameter.name),
            (
                existing_parameter_types or {}
            ).get(
                (class_name, method_name, parameter.name),
                PARAMETER_NAME_TYPES.get(parameter.name),
            ),
        )
        if annotation is not None and parameter.default is None:
            if "None" not in annotation:
                annotation += " | None"
        if annotation is not None:
            parameter = parameter.replace(annotation=_RenderedAnnotation(annotation))
        parameters.append(parameter)
    return (
        str(signature.replace(parameters=parameters))
        .replace(" = Ellipsis", " = ...")
        .replace("=Ellipsis", "=...")
    )


def _classes(module: ModuleType) -> list[tuple[str, type[object]]]:
    result: list[tuple[str, type[object]]] = []
    for name, value in vars(module).items():
        if name.startswith("_") or not inspect.isclass(value):
            continue
        if getattr(value, "__module__", "") == module.__name__ or issubclass(
            value, Exception
        ):
            result.append((name, value))
    return sorted(result)


def _existing_annotations(
    owner: str,
) -> tuple[
    dict[tuple[str | None, str], str],
    dict[tuple[str | None, str, str], str],
]:
    target = ROOT / "kairospy" / f"_native_{owner}_contract.pyi"
    if not target.exists():
        return {}, {}
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    returns: dict[tuple[str | None, str], str] = {}
    parameters: dict[tuple[str | None, str, str], str] = {}
    containers: list[tuple[str | None, list[ast.stmt]]] = [(None, tree.body)]
    containers.extend(
        (node.name, node.body)
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    )
    for class_name, body in containers:
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            annotation = (
                "" if node.returns is None else ast.unparse(node.returns)
            )
            if annotation not in {"", "Any", "object"}:
                returns[(class_name, node.name)] = annotation
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]:
                if argument.arg in {"self", "cls"} or argument.annotation is None:
                    continue
                annotation = ast.unparse(argument.annotation)
                if annotation not in {"Any", "object"}:
                    parameters[(class_name, node.name, argument.arg)] = annotation
    return returns, parameters


def _render_class(
    owner: str,
    name: str,
    value: type[object],
    rust_property_types: dict[tuple[str, str, str], str],
    rust_method_return_types: dict[tuple[str, str, str], str],
    existing_returns: dict[tuple[str | None, str], str],
    existing_parameter_types: dict[tuple[str | None, str, str], str],
) -> list[str]:
    base = ""
    if issubclass(value, ValueError):
        base = "(ValueError)"
    elif issubclass(value, RuntimeError):
        base = "(RuntimeError)"
    lines = [f"class {name}{base}:"]
    members: list[str] = []
    if not issubclass(value, Exception):
        signature = _signature(
            value,
            owner=owner,
            class_name=name,
            method_name="__init__",
            existing_parameter_types=existing_parameter_types,
        )
        if signature != "()":
            constructor = (
                f"(self, {signature[1:]}"
                if signature != "()"
                else "(self)"
            )
            members.append(f"    def __init__{constructor} -> None: ...")
        for member_name, member in inspect.getmembers(value):
            if member_name.startswith("_"):
                continue
            if inspect.isgetsetdescriptor(member):
                rust_type = rust_property_types.get((owner, name, member_name))
                identity_type = IDENTITY_PROPERTY_TYPES.get(owner, {}).get(
                    member_name, COMMON_IDENTITY_PROPERTY_TYPES.get(member_name)
                )
                if (
                    identity_type is not None
                    and rust_type is not None
                    and rust_type.endswith(" | None")
                    and not identity_type.endswith(" | None")
                ):
                    identity_type += " | None"
                result = RETURN_TYPES.get(
                    (owner, name, member_name),
                    PROPERTY_TYPES.get(
                        (owner, name, member_name),
                        identity_type
                        or existing_returns.get(
                            (name, member_name),
                            rust_type
                            or rust_method_return_types.get(
                                (owner, name, member_name), "object"
                            ),
                        ),
                    ),
                )
                members.extend(
                    (
                        "    @property",
                        f"    def {member_name}(self) -> {result}: ...",
                    )
                )
            elif inspect.ismethoddescriptor(member):
                result = RETURN_TYPES.get(
                    (owner, name, member_name),
                    existing_returns.get(
                        (name, member_name),
                        rust_method_return_types.get(
                            (owner, name, member_name), "object"
                        ),
                    ),
                )
                members.append(
                    "    def "
                    f"{member_name}{_signature(member, owner=owner, class_name=name, method_name=member_name, existing_parameter_types=existing_parameter_types)} "
                    f"-> {result}: ..."
                )
            elif inspect.isbuiltin(member):
                result = RETURN_TYPES.get(
                    (owner, name, member_name),
                    existing_returns.get(
                        (name, member_name),
                        rust_method_return_types.get(
                            (owner, name, member_name), "object"
                        ),
                    ),
                )
                members.extend(
                    (
                        "    @staticmethod",
                        "    def "
                        f"{member_name}{_signature(member, owner=owner, class_name=name, method_name=member_name, existing_parameter_types=existing_parameter_types)} "
                        f"-> {result}: ...",
                    )
                )
    lines.extend(members or ["    ..."])
    return lines


def render(owner: str) -> str:
    module = importlib.import_module(f"kairospy._native_{owner}_contract")
    rust_property_types = _rust_property_types(owner)
    rust_method_return_types = _rust_method_return_types(owner)
    existing_returns, existing_parameter_types = _existing_annotations(owner)
    lines = [
        "# Generated by scripts/generate/generate_owner_contract_stubs.py.",
        "# Do not edit by hand.",
        "from pathlib import Path",
        *STUB_IMPORTS.get(owner, ()),
        "",
    ]
    for name, value in _classes(module):
        lines.extend(
            _render_class(
                owner,
                name,
                value,
                rust_property_types,
                rust_method_return_types,
                existing_returns,
                existing_parameter_types,
            )
        )
        lines.append("")
    lines.extend(STUB_POSTLUDE.get(owner, ()))
    if STUB_POSTLUDE.get(owner):
        lines.append("")
    for name in MODULE_FUNCTIONS[owner]:
        value = getattr(module, name)
        result = MODULE_RETURN_TYPES.get(
            (owner, name), existing_returns.get((None, name), "object")
        )
        signature = _signature(
            value,
            owner=owner,
            class_name=None,
            method_name=name,
            existing_parameter_types=existing_parameter_types,
        )
        lines.append(f"def {name}{signature} -> {result}: ...")
    lines.append("")
    for name, value in sorted(vars(module).items()):
        if name.startswith("_") or inspect.isclass(value) or inspect.isroutine(value):
            continue
        if isinstance(value, bool):
            lines.append(f"{name}: bool")
        elif isinstance(value, int):
            lines.append(f"{name}: int")
        elif isinstance(value, str):
            lines.append(f"{name}: str")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("owners", nargs="*", choices=OWNERS, default=list(OWNERS))
    parser.add_argument(
        "--augment",
        action="store_true",
        help="append runtime classes missing from an existing hand-typed stub",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="rewrite hand-typed owners while preserving their audited annotations",
    )
    args = parser.parse_args()
    for owner in args.owners:
        target = ROOT / "kairospy" / f"_native_{owner}_contract.pyi"
        rendered = render(owner)
        if (
            not args.rewrite
            and (args.augment or owner in HAND_TYPED_OWNERS)
            and target.exists()
        ):
            source = target.read_text(encoding="utf-8")
            declared = {
                node.name
                for node in ast.parse(source).body
                if isinstance(node, ast.ClassDef)
            }
            module = importlib.import_module(f"kairospy._native_{owner}_contract")
            existing_returns, existing_parameter_types = _existing_annotations(owner)
            blocks = [
                "\n".join(
                    _render_class(
                        owner,
                        name,
                        value,
                        _rust_property_types(owner),
                        _rust_method_return_types(owner),
                        existing_returns,
                        existing_parameter_types,
                    )
                )
                for name, value in _classes(module)
                if name not in declared
            ]
            if blocks:
                rendered = source.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"
            else:
                rendered = source
        target.write_text(rendered, encoding="utf-8")
        print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

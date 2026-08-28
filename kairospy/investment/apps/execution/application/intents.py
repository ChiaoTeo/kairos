from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from kairospy.primitives.decimal import (
    Money,
    Price,
    Quantity,
    SignedQuantity,
)


@dataclass(frozen=True, slots=True)
class ExecutionBenchmark:
    """Explicit market observation used to evaluate one execution leg."""

    instrument_id: str
    market_id: str
    price: Price
    observed_at_unix_nanos: int
    leg_id: str | None = None
    kind: str = "arrival"

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", Price(self.price))
        if self.kind != "arrival":
            raise ValueError("execution benchmark kind must be arrival")
        if not self.instrument_id.strip() or not self.market_id.strip():
            raise ValueError("execution benchmark instrument_id and market_id are required")
        if self.leg_id is not None and not self.leg_id.strip():
            raise ValueError("execution benchmark leg_id cannot be blank")
        if self.observed_at_unix_nanos < 0:
            raise ValueError("execution benchmark observed_at_unix_nanos cannot be negative")


def _normalize_execution_benchmarks(
    values: Sequence[ExecutionBenchmark],
) -> tuple[ExecutionBenchmark, ...]:
    benchmarks = tuple(values)
    if any(not isinstance(value, ExecutionBenchmark) for value in benchmarks):
        raise TypeError("execution_benchmarks must contain ExecutionBenchmark values")
    explicit_leg_ids = [
        benchmark.leg_id
        for benchmark in benchmarks
        if benchmark.leg_id is not None
    ]
    if len(set(explicit_leg_ids)) != len(explicit_leg_ids):
        raise ValueError("execution benchmark leg_id values must be unique")
    return benchmarks


@dataclass(frozen=True, slots=True)
class TargetPositionRequest:
    """A strategy target translated into an Execution-owned Intent."""

    instrument_id: str
    quantity: Quantity
    algorithm: "ExecutionAlgorithmPolicy" = field(kw_only=True)
    account_id: str | None = None
    account_ids: tuple[str, ...] = ()
    segment_key: str = "spot"
    limit_price: Price | None = None
    reason: str = ""
    intent_id: str | None = None
    strategy_decision_id: str | None = None
    source_snapshot_id: str | None = None
    source_event_sequence: int | None = None
    source_event_time_unix_nanos: int | None = None
    split: "SplitOrderPolicy | None" = None
    maker: "MakerExecutionPolicy | None" = None
    execution_route_id: str | None = None
    execution_benchmarks: tuple[ExecutionBenchmark, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", Quantity(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", Price(self.limit_price))
        if isinstance(self.algorithm, MakerTakerHedgeAlgorithm):
            raise ValueError("maker-taker hedge requires a pair arbitrage Intent")
        if isinstance(self.algorithm, PassiveLimitAlgorithm):
            raise ValueError("passive-limit requires a quote provisioning Intent")
        if isinstance(self.algorithm, TwapAlgorithm) and self.split is not None:
            raise ValueError("TWAP cannot be combined with split order policy")
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if self.account_id is not None and not self.account_id.strip():
            raise ValueError("account_id cannot be blank")
        if any(
            not isinstance(account, str) or not account.strip()
            for account in self.account_ids
        ):
            raise ValueError("account_ids must contain non-empty strings")
        object.__setattr__(self, "account_ids", tuple(self.account_ids))
        if not self.segment_key.strip():
            raise ValueError("segment_key is required")
        if self.intent_id is not None and not self.intent_id.strip():
            raise ValueError("intent_id cannot be blank")
        if (
            self.strategy_decision_id is not None
            and not self.strategy_decision_id.strip()
        ):
            raise ValueError("strategy_decision_id cannot be blank")
        if self.source_snapshot_id is not None and not self.source_snapshot_id.strip():
            raise ValueError("source_snapshot_id cannot be blank")
        if (
            self.source_event_time_unix_nanos is not None
            and self.source_event_time_unix_nanos < 0
        ):
            raise ValueError("source_event_time_unix_nanos cannot be negative")
        object.__setattr__(
            self,
            "execution_benchmarks",
            _normalize_execution_benchmarks(self.execution_benchmarks),
        )


@dataclass(frozen=True, slots=True)
class ArbitrageLegRequest:
    instrument_id: str
    side: str
    quantity: Quantity
    account_id: str
    segment_key: str = "spot"
    limit_price: Price | None = None
    split: "SplitOrderPolicy | None" = None
    maker: "MakerExecutionPolicy | None" = None
    execution_route_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", Quantity.positive(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", Price(self.limit_price))
        if (
            not self.instrument_id.strip()
            or not self.account_id.strip()
            or not self.segment_key.strip()
        ):
            raise ValueError("arbitrage leg identity is required")
        if self.side not in {"Buy", "Sell", "buy", "sell"}:
            raise ValueError("arbitrage leg side must be Buy or Sell")


@dataclass(frozen=True, slots=True)
class PairArbitrageRequest:
    first: ArbitrageLegRequest
    second: ArbitrageLegRequest
    algorithm: "ExecutionAlgorithmPolicy" = field(kw_only=True)
    reason: str = ""
    intent_id: str | None = None
    strategy_decision_id: str | None = None
    completion_policy: str = "AllLegsSatisfied"
    failure_policy: str = "Compensate"
    max_wait_nanos: int | None = None
    min_edge_bps: int | None = None
    max_slippage_bps: int | None = None
    estimated_fee_bps: int | None = None
    execution_benchmarks: tuple[ExecutionBenchmark, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.algorithm, TwapAlgorithm):
            raise ValueError("TWAP requires a single-leg Intent")
        if isinstance(self.algorithm, PassiveLimitAlgorithm):
            raise ValueError("passive-limit requires a quote provisioning Intent")
        if self.intent_id is not None and not self.intent_id.strip():
            raise ValueError("intent_id cannot be blank")
        if (
            self.strategy_decision_id is not None
            and not self.strategy_decision_id.strip()
        ):
            raise ValueError("strategy_decision_id cannot be blank")
        object.__setattr__(
            self,
            "execution_benchmarks",
            _normalize_execution_benchmarks(self.execution_benchmarks),
        )


@dataclass(frozen=True, slots=True)
class OptionSpreadLegRequest:
    """One canonical option leg in an Execution-owned spread intent."""

    leg_id: str
    instrument_id: str
    side: str
    quantity: Quantity
    market_id: str | None = None
    limit_price: Price | None = None
    execution_route_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", Quantity.positive(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", Price(self.limit_price))
        if not self.leg_id.strip() or not self.instrument_id.strip():
            raise ValueError("option spread leg identity is required")
        normalized_side = self.side.lower()
        if normalized_side not in {"buy", "sell"}:
            raise ValueError("option spread leg side must be Buy or Sell")
        object.__setattr__(self, "side", normalized_side.capitalize())


@dataclass(frozen=True, slots=True)
class OptionSpreadRequest:
    """A fixed-risk two-leg option package, never two unrelated orders."""

    short_leg: OptionSpreadLegRequest
    long_leg: OptionSpreadLegRequest
    minimum_net_credit: Money
    maximum_loss: Money
    algorithm: "ExecutionAlgorithmPolicy" = field(kw_only=True)
    account_id: str = "main"
    reason: str = ""
    intent_id: str | None = None
    strategy_decision_id: str | None = None
    source_snapshot_id: str | None = None
    source_event_sequence: int | None = None
    source_event_time_unix_nanos: int | None = None
    deadline_unix_nanos: int | None = None
    maximum_quote_age_nanos: int = 300_000_000_000
    completion_policy: str = "AllOrNothing"
    failure_policy: str = "CancelRemaining"
    execution_benchmarks: tuple[ExecutionBenchmark, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum_net_credit", Money(self.minimum_net_credit))
        object.__setattr__(self, "maximum_loss", Money(self.maximum_loss))
        if not isinstance(self.algorithm, ImmediateAlgorithm):
            raise ValueError("option spread currently requires Immediate algorithm")
        if not self.account_id.strip():
            raise ValueError("option spread account_id is required")
        if self.short_leg.instrument_id == self.long_leg.instrument_id:
            raise ValueError("option spread legs must use different instruments")
        if self.short_leg.side != "Sell" or self.long_leg.side != "Buy":
            raise ValueError("credit spread requires a Sell short leg and Buy long leg")
        if self.short_leg.quantity != self.long_leg.quantity:
            raise ValueError("package option spread legs must have equal quantity")
        if self.minimum_net_credit.value < 0:
            raise ValueError("minimum_net_credit cannot be negative")
        if self.maximum_loss.value <= 0:
            raise ValueError("maximum_loss must be positive")
        if self.maximum_quote_age_nanos <= 0:
            raise ValueError("maximum_quote_age_nanos must be positive")
        for name in (
            "source_event_sequence",
            "source_event_time_unix_nanos",
            "deadline_unix_nanos",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.intent_id is not None and not self.intent_id.strip():
            raise ValueError("intent_id cannot be blank")
        if (
            self.strategy_decision_id is not None
            and not self.strategy_decision_id.strip()
        ):
            raise ValueError("strategy_decision_id cannot be blank")
        if self.source_snapshot_id is not None and not self.source_snapshot_id.strip():
            raise ValueError("source_snapshot_id cannot be blank")
        if self.completion_policy != "AllOrNothing":
            raise ValueError("first option spread version requires AllOrNothing")
        if self.failure_policy != "CancelRemaining":
            raise ValueError("first option spread version requires CancelRemaining")
        object.__setattr__(
            self,
            "execution_benchmarks",
            _normalize_execution_benchmarks(self.execution_benchmarks),
        )


@dataclass(frozen=True, slots=True)
class SplitOrderPolicy:
    """Deterministic child-order sizing for one execution leg."""

    max_child_quantity: Quantity | None = None
    child_count: int | None = None
    min_child_quantity: Quantity | None = None

    def __post_init__(self) -> None:
        if self.max_child_quantity is not None:
            object.__setattr__(
                self, "max_child_quantity", Quantity.positive(self.max_child_quantity)
            )
        if self.min_child_quantity is not None:
            object.__setattr__(
                self, "min_child_quantity", Quantity.positive(self.min_child_quantity)
            )
        if self.child_count is not None and self.child_count <= 0:
            raise ValueError("child_count must be positive")


@dataclass(frozen=True, slots=True)
class MakerExecutionPolicy:
    """Admission-only inventory and quote-freshness maker guardrails."""

    max_inventory_abs: Quantity | None = None
    target_inventory: SignedQuantity | None = None
    max_quote_age_millis: int | None = None

    def __post_init__(self) -> None:
        if self.max_inventory_abs is not None:
            object.__setattr__(self, "max_inventory_abs", Quantity(self.max_inventory_abs))
        if self.target_inventory is not None:
            object.__setattr__(self, "target_inventory", SignedQuantity(self.target_inventory))
        if self.max_quote_age_millis is not None and self.max_quote_age_millis <= 0:
            raise ValueError("max_quote_age_millis must be positive")


@dataclass(frozen=True, slots=True)
class HedgePolicy:
    leader_leg_id: str
    hedge_leg_id: str
    ratio_numerator: int = 1
    ratio_denominator: int = 1
    contract_multiplier_numerator: int = 1
    contract_multiplier_denominator: int = 1
    max_unhedged_quantity: Quantity = field(default_factory=lambda: Quantity("0"))
    max_unhedged_duration_nanos: int | None = None
    fallback_execution_route_ids: tuple[str, ...] = ()
    compensate_on_failure: bool = True
    max_compensation_attempts: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_unhedged_quantity", Quantity(self.max_unhedged_quantity)
        )
        if not self.leader_leg_id.strip() or not self.hedge_leg_id.strip():
            raise ValueError("hedge leg ids are required")
        if self.leader_leg_id == self.hedge_leg_id:
            raise ValueError("hedge legs must be different")
        if self.ratio_numerator <= 0 or self.ratio_denominator <= 0:
            raise ValueError("hedge ratio must be positive")
        if (
            self.contract_multiplier_numerator <= 0
            or self.contract_multiplier_denominator <= 0
        ):
            raise ValueError("contract multiplier must be positive")
        if (
            self.max_unhedged_duration_nanos is not None
            and self.max_unhedged_duration_nanos <= 0
        ):
            raise ValueError("max_unhedged_duration_nanos must be positive")
        if any(not route.strip() for route in self.fallback_execution_route_ids):
            raise ValueError("fallback execution route ids cannot be blank")
        if len(set(self.fallback_execution_route_ids)) != len(
            self.fallback_execution_route_ids
        ):
            raise ValueError("fallback execution route ids must be unique")
        object.__setattr__(
            self,
            "fallback_execution_route_ids",
            tuple(self.fallback_execution_route_ids),
        )
        if self.max_compensation_attempts <= 0:
            raise ValueError("max_compensation_attempts must be positive")


@dataclass(frozen=True, slots=True)
class ImmediateAlgorithm:
    """Explicit action-first execution of already planned children."""


@dataclass(frozen=True, slots=True)
class TwapAlgorithm:
    slice_count: int
    slice_interval_nanos: int

    def __post_init__(self) -> None:
        if self.slice_count <= 0:
            raise ValueError("TWAP slice_count must be positive")
        if self.slice_interval_nanos <= 0:
            raise ValueError("TWAP slice_interval_nanos must be positive")


@dataclass(frozen=True, slots=True)
class PassiveLimitAlgorithm:
    reprice_interval_nanos: int
    max_quote_age_nanos: int

    def __post_init__(self) -> None:
        if self.reprice_interval_nanos <= 0:
            raise ValueError("passive-limit reprice_interval_nanos must be positive")
        if self.max_quote_age_nanos <= 0:
            raise ValueError("passive-limit max_quote_age_nanos must be positive")


@dataclass(frozen=True, slots=True)
class MakerTakerHedgeAlgorithm:
    hedge: HedgePolicy


ExecutionAlgorithmPolicy = (
    ImmediateAlgorithm
    | TwapAlgorithm
    | PassiveLimitAlgorithm
    | MakerTakerHedgeAlgorithm
)


@dataclass(frozen=True, slots=True)
class QuoteProvisioningRequest:
    """Two-sided maker quote controlled by Execution's cadence and inventory guards."""

    instrument_id: str
    bid_price: Price
    bid_quantity: Quantity
    ask_price: Price
    ask_quantity: Quantity
    algorithm: "ExecutionAlgorithmPolicy" = field(kw_only=True)
    account_id: str = ""
    segment_key: str = "spot"
    market_id: str | None = None
    maker: MakerExecutionPolicy | None = None
    reason: str = ""
    intent_id: str | None = None
    strategy_decision_id: str | None = None
    execution_route_id: str | None = None
    execution_benchmarks: tuple[ExecutionBenchmark, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bid_price", Price(self.bid_price))
        object.__setattr__(self, "bid_quantity", Quantity.positive(self.bid_quantity))
        object.__setattr__(self, "ask_price", Price(self.ask_price))
        object.__setattr__(self, "ask_quantity", Quantity.positive(self.ask_quantity))
        if not isinstance(self.algorithm, PassiveLimitAlgorithm):
            raise ValueError(
                "quote provisioning requires PassiveLimit algorithm"
            )
        if (
            not self.instrument_id.strip()
            or not self.segment_key.strip()
            or not self.account_id.strip()
        ):
            raise ValueError("quote provisioning identity is required")
        if self.bid_price >= self.ask_price:
            raise ValueError("quote provisioning bid must be below ask")
        if (
            self.strategy_decision_id is not None
            and not self.strategy_decision_id.strip()
        ):
            raise ValueError("strategy_decision_id cannot be blank")
        object.__setattr__(
            self,
            "execution_benchmarks",
            _normalize_execution_benchmarks(self.execution_benchmarks),
        )


@dataclass(frozen=True, slots=True)
class QuoteRefreshRequest:
    """Fresh prices for an existing Execution-owned two-sided quote."""

    intent_id: str
    bid_price: Price
    ask_price: Price
    quote_observed_at_unix_nanos: int
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "bid_price", Price(self.bid_price))
        object.__setattr__(self, "ask_price", Price(self.ask_price))
        if not self.intent_id.strip():
            raise ValueError("intent_id is required")
        if self.bid_price >= self.ask_price:
            raise ValueError("quote refresh requires positive bid below ask")
        if self.quote_observed_at_unix_nanos < 0:
            raise ValueError("quote observation timestamp cannot be negative")


@dataclass(frozen=True, slots=True)
class PortfolioRebalanceTarget:
    instrument_id: str
    quantity: Quantity
    account_id: str
    segment_key: str = "spot"
    limit_price: Price | None = None
    split: "SplitOrderPolicy | None" = None
    maker: "MakerExecutionPolicy | None" = None
    execution_route_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", Quantity(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", Price(self.limit_price))
        if (
            not self.instrument_id.strip()
            or not self.account_id.strip()
            or not self.segment_key.strip()
        ):
            raise ValueError("portfolio target identity is required")


@dataclass(frozen=True, slots=True)
class PortfolioRebalanceRequest:
    targets: tuple[PortfolioRebalanceTarget, ...]
    algorithm: "ExecutionAlgorithmPolicy" = field(kw_only=True)
    reason: str = ""
    intent_id: str | None = None
    strategy_decision_id: str | None = None
    completion_policy: str = "BestEffort"
    failure_policy: str = "ContinueOtherLegs"
    execution_benchmarks: tuple[ExecutionBenchmark, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.algorithm, ImmediateAlgorithm):
            raise ValueError(
                "portfolio rebalance currently requires Immediate algorithm"
            )
        if not self.targets:
            raise ValueError("portfolio rebalance requires at least one target")
        object.__setattr__(self, "targets", tuple(self.targets))
        if (
            self.strategy_decision_id is not None
            and not self.strategy_decision_id.strip()
        ):
            raise ValueError("strategy_decision_id cannot be blank")
        object.__setattr__(
            self,
            "execution_benchmarks",
            _normalize_execution_benchmarks(self.execution_benchmarks),
        )

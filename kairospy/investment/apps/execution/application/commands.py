"""Execution application commands mapped once into owner-native contract values."""

from __future__ import annotations

from decimal import Decimal
import time

from kairospy.infrastructure.contracts.execution import (
    ExecutionControlClient,
    ExecutionControlRejectedError,
)
from kairospy.infrastructure.contracts.execution.types import (
    CancelOrderRequest as ContractCancelOrderRequest,
    ExecutionAlgorithmPolicyRequest,
    ExecutionBenchmarkRequest,
    ExecutionIntentRequest,
    ExecutionOrderOptionsRequest,
    IntentAdmissionEvidenceRequest,
    IntentLegRequest,
    MakerExecutionPolicyRequest,
    ReplaceOrderRequest as ContractReplaceOrderRequest,
    SplitOrderPolicyRequest,
    SubmitIntentRequest,
)
from kairospy.strategy import CommandHandle

from .admission import IntentAdmissionEvidence
from .intents import (
    ExecutionAlgorithmPolicy,
    ExecutionBenchmark,
    HedgePolicy,
    ImmediateAlgorithm,
    MakerExecutionPolicy,
    MakerTakerHedgeAlgorithm,
    OptionSpreadRequest,
    PairArbitrageRequest,
    PassiveLimitAlgorithm,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
    TwapAlgorithm,
)
from .models import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderRequest,
    ReplaceOrderRequest,
)


class ExecutionCommandClient:
    def __init__(
        self,
        client: ExecutionControlClient,
        *,
        workspace_id: str,
        launch_id: str,
        default_segment: str = "spot",
        allow_trading: bool = True,
        max_order_notional: Decimal | None = None,
        require_limit_orders: bool = False,
    ) -> None:
        if not workspace_id.strip() or not launch_id.strip():
            raise ValueError("Execution command scope requires workspace_id and launch_id")
        self.client = client
        self.workspace_id = workspace_id
        self.launch_id = launch_id
        self.default_segment = default_segment
        self.allow_trading = allow_trading
        self.max_order_notional = max_order_notional
        self.require_limit_orders = require_limit_orders

    def target_position(
        self,
        request: TargetPositionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        rejected = self._admit(
            request_id,
            instance_id,
            quantity=request.quantity,
            limit_price=request.limit_price,
        )
        if rejected is not None:
            return rejected
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        intent = _target_intent(
            request,
            intent_id=intent_id,
            strategy_id=strategy_id,
            launch_id=self.launch_id,
            instance_id=instance_id,
        )
        return self._submit(
            intent,
            evidence=admission_evidence,
            strategy_id=strategy_id,
            instance_id=instance_id,
            request_id=request_id,
        )

    def submit_order(
        self,
        request: OrderRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        limit_price = (
            request.limit_price if isinstance(request, LimitOrderRequest) else None
        )
        rejected = self._admit(
            request_id,
            instance_id,
            quantity=request.quantity,
            limit_price=limit_price,
        )
        if rejected is not None:
            return rejected
        instrument_id = str(getattr(request.instrument, "id", request.instrument))
        account_id = str(request.account)
        segment_key = str(request.segment or self.default_segment)
        options = ExecutionOrderOptionsRequest(
            time_in_force=request.time_in_force.value.upper(),
            reduce_only=request.reduce_only,
            post_only=request.post_only if isinstance(request, LimitOrderRequest) else False,
        )
        leg = IntentLegRequest(
            leg_id=f"{request_id}:leg",
            account_id=account_id,
            segment_key=segment_key,
            instrument_id=instrument_id,
            market_id=instrument_id,
            side=request.side.value,
            quantity=_decimal(request.quantity),
            limit_price=None if limit_price is None else _decimal(limit_price),
            options=options,
        )
        intent = ExecutionIntentRequest(
            intent_id=f"{strategy_id}:intent:{request_id}",
            strategy_id=strategy_id,
            launch_id=self.launch_id,
            instance_id=instance_id,
            instrument_id=instrument_id,
            account_ids=[account_id],
            segment_key=segment_key,
            target_quantity="0",
            reason=request.reason,
            intent_type="single_order",
            algorithm=ExecutionAlgorithmPolicyRequest.immediate(),
            order_options=options,
            legs=[leg],
        )
        return self._submit(
            intent,
            evidence=None,
            strategy_id=strategy_id,
            instance_id=instance_id,
            request_id=request_id,
        )

    def cancel_intent(
        self,
        intent_id: str,
        *,
        reason: str,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        return CommandHandle(
            request_id,
            "rejected",
            error="execution_cancel_intent is not part of ExecutionControlRpc",
        )

    def cancel_order(
        self,
        order_id: str,
        *,
        reason: str,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        try:
            value = self.client.cancel_order(
                order_id, ContractCancelOrderRequest(reason=reason)
            )
        except ExecutionControlRejectedError as error:
            return CommandHandle(request_id, "rejected", error=str(error))
        return _handle(request_id, value)

    def replace_order(
        self,
        order_id: str,
        request: ReplaceOrderRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        options = (
            None
            if request.time_in_force is None
            else ExecutionOrderOptionsRequest(
                time_in_force=request.time_in_force.value.upper()
            )
        )
        contract = ContractReplaceOrderRequest(
            quantity=None if request.quantity is None else _decimal(request.quantity),
            limit_price=(
                None if request.limit_price is None else _decimal(request.limit_price)
            ),
            options=options,
            reason=request.reason,
        )
        try:
            value = self.client.replace_order(order_id, contract)
        except ExecutionControlRejectedError as error:
            return CommandHandle(request_id, "rejected", error=str(error))
        return _handle(request_id, value)

    def cancel_all(
        self,
        *,
        instrument_id: str | None,
        account_id: str | None,
        reason: str,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        return CommandHandle(
            request_id,
            "rejected",
            error="execution bulk cancel is not part of ExecutionControlRpc",
        )

    def pair_arbitrage(
        self,
        request: PairArbitrageRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        rejected = self._admit(request_id, instance_id)
        if rejected is not None:
            return rejected
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        intent = _pair_intent(
            request,
            intent_id=intent_id,
            strategy_id=strategy_id,
            launch_id=self.launch_id,
            instance_id=instance_id,
        )
        return self._submit(
            intent,
            evidence=admission_evidence,
            strategy_id=strategy_id,
            instance_id=instance_id,
            request_id=request_id,
        )

    def option_spread(
        self,
        request: OptionSpreadRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        rejected = self._admit(request_id, instance_id)
        if rejected is not None:
            return rejected
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        intent = _option_intent(
            request,
            intent_id=intent_id,
            strategy_id=strategy_id,
            launch_id=self.launch_id,
            instance_id=instance_id,
        )
        return self._submit(
            intent,
            evidence=admission_evidence,
            strategy_id=strategy_id,
            instance_id=instance_id,
            request_id=request_id,
        )

    def portfolio_rebalance(
        self,
        request: PortfolioRebalanceRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        rejected = self._admit(request_id, instance_id)
        if rejected is not None:
            return rejected
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        intent = _portfolio_intent(
            request,
            intent_id=intent_id,
            strategy_id=strategy_id,
            launch_id=self.launch_id,
            instance_id=instance_id,
        )
        return self._submit(
            intent,
            evidence=admission_evidence,
            strategy_id=strategy_id,
            instance_id=instance_id,
            request_id=request_id,
        )

    def quote_provisioning(
        self,
        request: QuoteProvisioningRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        rejected = self._admit(request_id, instance_id)
        if rejected is not None:
            return rejected
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        intent = _quote_intent(
            request,
            intent_id=intent_id,
            strategy_id=strategy_id,
            launch_id=self.launch_id,
            instance_id=instance_id,
        )
        return self._submit(
            intent,
            evidence=admission_evidence,
            strategy_id=strategy_id,
            instance_id=instance_id,
            request_id=request_id,
        )

    def refresh_quote(
        self,
        request: QuoteRefreshRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for quote refresh",
            )
        return CommandHandle(
            request_id,
            "rejected",
            error="execution_refresh_quote is not part of ExecutionControlRpc",
        )

    def _admit(
        self,
        request_id: str,
        instance_id: str,
        *,
        quantity: Decimal | None = None,
        limit_price: Decimal | None = None,
    ) -> CommandHandle | None:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for execution intents",
            )
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        if self.require_limit_orders and limit_price is None:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch safety policy requires limit orders",
            )
        if (
            self.max_order_notional is not None
            and quantity is not None
            and limit_price is not None
            and abs(quantity * limit_price) > self.max_order_notional
        ):
            return CommandHandle(
                request_id,
                "rejected",
                error="intent exceeds launch max_order_notional",
            )
        return None

    def _submit(
        self,
        intent: ExecutionIntentRequest,
        *,
        evidence: IntentAdmissionEvidence | None,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        contract_evidence = None
        if evidence is not None:
            original = _intent_for_request(
                evidence.original_intent,
                intent_id=intent.intent_id,
                strategy_id=strategy_id,
                launch_id=self.launch_id,
                instance_id=instance_id,
            )
            contract_evidence = IntentAdmissionEvidenceRequest(
                source=evidence.source,
                decision_id=evidence.decision_id,
                outcome=evidence.outcome,
                original_intent=original,
                effective_intent=intent,
            )
        submission = SubmitIntentRequest(
            intent=intent,
            command_id=request_id,
            idempotency_key=request_id,
            caller_id=strategy_id,
            workspace_id=self.workspace_id,
            admission_evidence=contract_evidence,
        )
        try:
            value = self.client.submit_intent(submission)
        except ExecutionControlRejectedError as error:
            return CommandHandle(request_id, "rejected", error=str(error))
        return _handle(request_id, value)


def _intent_for_request(
    request: object,
    *,
    intent_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> ExecutionIntentRequest:
    if isinstance(request, TargetPositionRequest):
        return _target_intent(request, intent_id=intent_id, strategy_id=strategy_id, launch_id=launch_id, instance_id=instance_id)
    if isinstance(request, PairArbitrageRequest):
        return _pair_intent(request, intent_id=intent_id, strategy_id=strategy_id, launch_id=launch_id, instance_id=instance_id)
    if isinstance(request, OptionSpreadRequest):
        return _option_intent(request, intent_id=intent_id, strategy_id=strategy_id, launch_id=launch_id, instance_id=instance_id)
    if isinstance(request, PortfolioRebalanceRequest):
        return _portfolio_intent(request, intent_id=intent_id, strategy_id=strategy_id, launch_id=launch_id, instance_id=instance_id)
    if isinstance(request, QuoteProvisioningRequest):
        return _quote_intent(request, intent_id=intent_id, strategy_id=strategy_id, launch_id=launch_id, instance_id=instance_id)
    raise TypeError("unsupported original Execution Intent request")


def _target_intent(
    request: TargetPositionRequest,
    *,
    intent_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> ExecutionIntentRequest:
    account_ids = list(request.account_ids) or [request.account_id or "main"]
    options = _options(request.split, request.maker)
    return ExecutionIntentRequest(
        intent_id=intent_id,
        strategy_decision_id=request.strategy_decision_id,
        strategy_id=strategy_id,
        launch_id=launch_id,
        instance_id=instance_id,
        instrument_id=request.instrument_id,
        execution_route_id=request.execution_route_id,
        account_ids=account_ids,
        segment_key=request.segment_key,
        target_quantity=_decimal(request.quantity),
        limit_price=None if request.limit_price is None else _decimal(request.limit_price),
        source_snapshot_id=request.source_snapshot_id,
        source_event_sequence=request.source_event_sequence,
        source_event_time_unix_nanos=request.source_event_time_unix_nanos,
        reason=request.reason,
        intent_type="target_position",
        algorithm=_algorithm(request.algorithm),
        order_options=options,
        execution_benchmarks=_benchmarks(request.execution_benchmarks),
    )


def _pair_intent(
    request: PairArbitrageRequest,
    *,
    intent_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> ExecutionIntentRequest:
    legs: list[IntentLegRequest] = []
    account_ids: list[str] = []
    for index, leg in enumerate((request.first, request.second)):
        if leg.account_id not in account_ids:
            account_ids.append(leg.account_id)
        legs.append(
            IntentLegRequest(
                leg_id=f"leg-{index}",
                account_id=leg.account_id,
                segment_key=leg.segment_key,
                instrument_id=leg.instrument_id,
                execution_route_id=leg.execution_route_id,
                side=leg.side,
                quantity=_decimal(leg.quantity),
                limit_price=None if leg.limit_price is None else _decimal(leg.limit_price),
                options=_options(leg.split, leg.maker),
            )
        )
    return ExecutionIntentRequest(
        intent_id=intent_id,
        strategy_decision_id=request.strategy_decision_id,
        strategy_id=strategy_id,
        launch_id=launch_id,
        instance_id=instance_id,
        instrument_id=request.first.instrument_id,
        account_ids=account_ids,
        segment_key=request.first.segment_key,
        target_quantity="0",
        reason=request.reason,
        intent_type="pair_arbitrage",
        algorithm=_algorithm(request.algorithm),
        order_options=_options(None, None),
        completion_policy=request.completion_policy,
        failure_policy=request.failure_policy,
        legs=legs,
        execution_benchmarks=_benchmarks(request.execution_benchmarks),
        deadline_unix_nanos=(
            None if request.max_wait_nanos is None else time.time_ns() + request.max_wait_nanos
        ),
        min_edge_bps=request.min_edge_bps,
        max_slippage_bps=request.max_slippage_bps,
        estimated_fee_bps=request.estimated_fee_bps,
    )


def _option_intent(
    request: OptionSpreadRequest,
    *,
    intent_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> ExecutionIntentRequest:
    legs = [
        IntentLegRequest(
            leg_id=leg.leg_id,
            account_id=request.account_id,
            segment_key="options",
            instrument_id=leg.instrument_id,
            market_id=leg.market_id,
            execution_route_id=leg.execution_route_id,
            side=leg.side,
            quantity=_decimal(leg.quantity),
            limit_price=None if leg.limit_price is None else _decimal(leg.limit_price),
            options=_options(None, None),
        )
        for leg in (request.short_leg, request.long_leg)
    ]
    return ExecutionIntentRequest(
        intent_id=intent_id,
        strategy_decision_id=request.strategy_decision_id,
        strategy_id=strategy_id,
        launch_id=launch_id,
        instance_id=instance_id,
        instrument_id=request.short_leg.instrument_id,
        market_id=request.short_leg.market_id,
        execution_route_id=request.short_leg.execution_route_id,
        account_ids=[request.account_id],
        segment_key="options",
        target_quantity="0",
        source_snapshot_id=request.source_snapshot_id,
        source_event_sequence=request.source_event_sequence,
        source_event_time_unix_nanos=request.source_event_time_unix_nanos,
        reason=request.reason,
        intent_type="option_spread",
        algorithm=_algorithm(request.algorithm),
        order_options=_options(None, None),
        completion_policy=request.completion_policy,
        failure_policy=request.failure_policy,
        legs=legs,
        execution_benchmarks=_benchmarks(request.execution_benchmarks),
        deadline_unix_nanos=request.deadline_unix_nanos,
        minimum_net_credit=_decimal(request.minimum_net_credit),
        maximum_loss=_decimal(request.maximum_loss),
    )


def _portfolio_intent(
    request: PortfolioRebalanceRequest,
    *,
    intent_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> ExecutionIntentRequest:
    account_ids: list[str] = []
    legs: list[IntentLegRequest] = []
    for index, target in enumerate(request.targets):
        if target.account_id not in account_ids:
            account_ids.append(target.account_id)
        legs.append(
            IntentLegRequest(
                leg_id=f"target-{index}",
                account_id=target.account_id,
                segment_key=target.segment_key,
                instrument_id=target.instrument_id,
                execution_route_id=target.execution_route_id,
                side="buy",
                quantity=_decimal(target.quantity),
                limit_price=None if target.limit_price is None else _decimal(target.limit_price),
                target_position=True,
                options=_options(target.split, target.maker),
            )
        )
    first = request.targets[0]
    return ExecutionIntentRequest(
        intent_id=intent_id,
        strategy_decision_id=request.strategy_decision_id,
        strategy_id=strategy_id,
        launch_id=launch_id,
        instance_id=instance_id,
        instrument_id=first.instrument_id,
        account_ids=account_ids,
        segment_key=first.segment_key,
        target_quantity="0",
        reason=request.reason,
        intent_type="portfolio_rebalance",
        algorithm=_algorithm(request.algorithm),
        order_options=_options(None, None),
        completion_policy=request.completion_policy,
        failure_policy=request.failure_policy,
        legs=legs,
        execution_benchmarks=_benchmarks(request.execution_benchmarks),
    )


def _quote_intent(
    request: QuoteProvisioningRequest,
    *,
    intent_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> ExecutionIntentRequest:
    options = _options(None, request.maker, post_only=True)
    legs = [
        IntentLegRequest(
            leg_id="bid",
            account_id=request.account_id,
            segment_key=request.segment_key,
            instrument_id=request.instrument_id,
            market_id=request.market_id,
            execution_route_id=request.execution_route_id,
            side="buy",
            quantity=_decimal(request.bid_quantity),
            limit_price=_decimal(request.bid_price),
            options=options,
        ),
        IntentLegRequest(
            leg_id="ask",
            account_id=request.account_id,
            segment_key=request.segment_key,
            instrument_id=request.instrument_id,
            market_id=request.market_id,
            execution_route_id=request.execution_route_id,
            side="sell",
            quantity=_decimal(request.ask_quantity),
            limit_price=_decimal(request.ask_price),
            options=options,
        ),
    ]
    return ExecutionIntentRequest(
        intent_id=intent_id,
        strategy_decision_id=request.strategy_decision_id,
        strategy_id=strategy_id,
        launch_id=launch_id,
        instance_id=instance_id,
        instrument_id=request.instrument_id,
        market_id=request.market_id,
        execution_route_id=request.execution_route_id,
        account_ids=[request.account_id],
        segment_key=request.segment_key,
        target_quantity="0",
        reason=request.reason,
        intent_type="quote_provisioning",
        algorithm=_algorithm(request.algorithm),
        order_options=_options(None, request.maker),
        completion_policy="best_effort",
        failure_policy="continue_other_legs",
        legs=legs,
        execution_benchmarks=_benchmarks(request.execution_benchmarks),
    )


def _options(
    split: SplitOrderPolicy | None,
    maker: MakerExecutionPolicy | None,
    *,
    post_only: bool | None = None,
) -> ExecutionOrderOptionsRequest:
    native_split = (
        None
        if split is None
        else SplitOrderPolicyRequest(
            max_child_quantity=(
                None if split.max_child_quantity is None else _decimal(split.max_child_quantity)
            ),
            child_count=split.child_count,
            min_child_quantity=(
                None if split.min_child_quantity is None else _decimal(split.min_child_quantity)
            ),
        )
    )
    native_maker = (
        None
        if maker is None
        else MakerExecutionPolicyRequest(
            max_inventory_abs=(
                None if maker.max_inventory_abs is None else _decimal(maker.max_inventory_abs)
            ),
            target_inventory=(
                None if maker.target_inventory is None else _decimal(maker.target_inventory)
            ),
            max_quote_age_nanos=(
                None if maker.max_quote_age_millis is None else maker.max_quote_age_millis * 1_000_000
            ),
        )
    )
    return ExecutionOrderOptionsRequest(
        post_only=post_only,
        split=native_split,
        maker=native_maker,
    )


def _algorithm(policy: ExecutionAlgorithmPolicy) -> ExecutionAlgorithmPolicyRequest:
    if isinstance(policy, ImmediateAlgorithm):
        return ExecutionAlgorithmPolicyRequest.immediate()
    if isinstance(policy, TwapAlgorithm):
        return ExecutionAlgorithmPolicyRequest.twap(
            policy.slice_count, policy.slice_interval_nanos
        )
    if isinstance(policy, PassiveLimitAlgorithm):
        return ExecutionAlgorithmPolicyRequest.passive_limit(
            policy.reprice_interval_nanos, policy.max_quote_age_nanos
        )
    if isinstance(policy, MakerTakerHedgeAlgorithm):
        hedge: HedgePolicy = policy.hedge
        return ExecutionAlgorithmPolicyRequest.maker_taker_hedge(
            leader_leg_id=hedge.leader_leg_id,
            hedge_leg_id=hedge.hedge_leg_id,
            ratio=str(Decimal(hedge.ratio_numerator) / hedge.ratio_denominator),
            contract_multiplier=str(
                Decimal(hedge.contract_multiplier_numerator)
                / hedge.contract_multiplier_denominator
            ),
            max_unhedged_quantity=_decimal(hedge.max_unhedged_quantity),
            max_unhedged_duration_nanos=hedge.max_unhedged_duration_nanos,
            fallback_execution_route_ids=list(hedge.fallback_execution_route_ids),
            compensate_on_failure=hedge.compensate_on_failure,
            max_compensation_attempts=hedge.max_compensation_attempts,
        )
    raise TypeError("unsupported Execution algorithm")


def _benchmarks(
    values: tuple[ExecutionBenchmark, ...],
) -> list[ExecutionBenchmarkRequest]:
    return [
        ExecutionBenchmarkRequest(
            kind=value.kind,
            leg_id=value.leg_id,
            instrument_id=value.instrument_id,
            market_id=value.market_id,
            price=_decimal(value.price),
            observed_at_unix_nanos=value.observed_at_unix_nanos,
        )
        for value in values
    ]


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _handle(request_id: str, value: object) -> CommandHandle:
    status = str(getattr(value, "status"))
    result = {
        key: item
        for key in ("command_id", "intent_id", "order_id")
        if (item := getattr(value, key)) is not None
    }
    return CommandHandle(request_id, status, result=result)


__all__ = ["ExecutionCommandClient"]

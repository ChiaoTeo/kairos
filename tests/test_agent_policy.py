from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kairospy.application.agent import (
    AgentApplication,
    DecisionKind,
    DecisionResult,
    IntentCandidate,
    ReduceTargetQuantity,
    RequireMakerExecution,
    ShortenDeadline,
    TightenMaxSlippage,
    TightenSplitPolicy,
    TightenLimitPrice,
)
from kairospy.application.agent.policy import DecisionPolicy
from kairospy.application.execution import (
    ArbitrageLegRequest,
    OptionSpreadLegRequest,
    OptionSpreadRequest,
    PairArbitrageRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
)


def _candidate(request: object) -> IntentCandidate:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    return IntentCandidate(
        decision_id="decision",
        request_id="request",
        intent_id="intent",
        workspace_id="workspace",
        strategy_id="strategy",
        launch_id="launch",
        instance_id="instance",
        operation="target_position",
        request=request,
        exposure_effect="unknown",
        profile_hash="profile-hash",
        snapshot=AgentApplication(enabled=True)._snapshot(
            "execution.intent_review", now=now
        ),
        submitted_at=now,
        deadline=now + timedelta(seconds=5),
    )


def test_policy_approves_original_and_rejects_without_effective_request() -> None:
    request = TargetPositionRequest("BTCUSDT", Decimal("2"), account_id="main")
    policy = DecisionPolicy({})

    approved = policy.apply(
        _candidate(request),
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"),
    )
    rejected = policy.apply(
        _candidate(request),
        DecisionResult(DecisionKind.REJECT, 9000, (), (), "risk"),
    )

    assert approved.effective_request is request
    assert rejected.effective_request is None
    assert rejected.decision is DecisionKind.REJECT


def test_policy_applies_atomic_risk_monotonic_target_revisions() -> None:
    request = TargetPositionRequest(
        "BTCUSDT",
        Decimal("2"),
        account_id="main",
        limit_price=Decimal("100"),
    )
    policy = DecisionPolicy(
        {"allow_quantity_reduction": True, "max_price_adjustment_bps": 100}
    )
    outcome = policy.apply(
        _candidate(request),
        DecisionResult(
            DecisionKind.REVISE,
            8500,
            (),
            (),
            "smaller and cheaper",
            (ReduceTargetQuantity("1"), TightenLimitPrice("99.5")),
        ),
    )

    assert outcome.decision is DecisionKind.REVISE
    assert isinstance(outcome.effective_request, TargetPositionRequest)
    assert outcome.effective_request.quantity == Decimal("1")
    assert outcome.effective_request.limit_price == Decimal("99.5")
    assert request.quantity == Decimal("2")


def test_policy_rejects_direction_or_risk_expansion_atomically() -> None:
    request = TargetPositionRequest(
        "BTCUSDT",
        Decimal("2"),
        account_id="main",
        limit_price=Decimal("100"),
    )
    policy = DecisionPolicy(
        {"allow_quantity_reduction": True, "max_price_adjustment_bps": 100}
    )
    direction = policy.apply(
        _candidate(request),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "flip",
            (ReduceTargetQuantity("-1"),),
        ),
    )
    expansion = policy.apply(
        _candidate(request),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "more",
            (ReduceTargetQuantity("3"),),
        ),
    )
    aggressive_price = policy.apply(
        _candidate(request),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "chase",
            (TightenLimitPrice("100.5"),),
        ),
    )

    for outcome in (direction, expansion, aggressive_price):
        assert outcome.decision is DecisionKind.ABSTAIN
        assert outcome.effective_request is None


def test_policy_enforces_profile_code_allowlists() -> None:
    request = TargetPositionRequest("BTCUSDT", Decimal("2"), account_id="main")
    policy = DecisionPolicy({}, reason_codes=("liquidity",), risk_flags=("stale",))

    outcome = policy.apply(
        _candidate(request),
        DecisionResult(
            DecisionKind.APPROVE,
            7000,
            ("unconfigured",),
            (),
            "unknown reason",
        ),
    )

    assert outcome.decision is DecisionKind.ABSTAIN
    assert outcome.effective_request is None

    empty_profile = DecisionPolicy({}).apply(
        _candidate(request),
        DecisionResult(
            DecisionKind.APPROVE,
            7000,
            ("not_allowed_when_profile_is_empty",),
            (),
            "unknown reason",
        ),
    )
    assert empty_profile.decision is DecisionKind.ABSTAIN
    assert empty_profile.effective_request is None


def test_policy_tightens_deadline_and_slippage_without_changing_identity() -> None:
    option = OptionSpreadRequest(
        OptionSpreadLegRequest("short", "BTC-100-C", "Sell", Decimal("1")),
        OptionSpreadLegRequest("long", "BTC-110-C", "Buy", Decimal("1")),
        Decimal("1"),
        Decimal("9"),
        deadline_unix_nanos=200,
    )
    arbitrage = PairArbitrageRequest(
        ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
        ArbitrageLegRequest("BTCUSD", "Sell", Decimal("1"), "main"),
        max_slippage_bps=20,
    )
    policy = DecisionPolicy(
        {"allow_deadline_reduction": True, "allow_slippage_reduction": True}
    )

    shortened = policy.apply(
        _candidate(option),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "shorter",
            (ShortenDeadline(100),),
        ),
    )
    tightened = policy.apply(
        _candidate(arbitrage),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "less slippage",
            (TightenMaxSlippage(10),),
        ),
    )

    assert isinstance(shortened.effective_request, OptionSpreadRequest)
    assert shortened.effective_request.deadline_unix_nanos == 100
    assert shortened.effective_request.short_leg is option.short_leg
    assert isinstance(tightened.effective_request, PairArbitrageRequest)
    assert tightened.effective_request.max_slippage_bps == 10
    assert tightened.effective_request.first is arbitrage.first


def test_policy_tightens_split_and_requires_maker_execution() -> None:
    split_request = TargetPositionRequest(
        "BTCUSDT",
        Decimal("10"),
        account_id="main",
        split=SplitOrderPolicy(
            max_child_quantity=Decimal("5"),
            child_count=2,
            interval_millis=100,
        ),
    )
    maker_request = TargetPositionRequest("BTCUSDT", Decimal("1"), account_id="main")
    policy = DecisionPolicy(
        {"allow_split_tightening": True, "allow_require_maker": True}
    )

    split = policy.apply(
        _candidate(split_request),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "smaller children",
            (TightenSplitPolicy("2", 3, 200),),
        ),
    )
    maker = policy.apply(
        _candidate(maker_request),
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "maker only",
            (RequireMakerExecution(),),
        ),
    )

    assert isinstance(split.effective_request, TargetPositionRequest)
    assert split.effective_request.split is not None
    assert split.effective_request.split.max_child_quantity == Decimal("2")
    assert split.effective_request.split.child_count == 3
    assert split.effective_request.split.interval_millis == 200
    assert isinstance(maker.effective_request, TargetPositionRequest)
    assert maker.effective_request.maker is not None

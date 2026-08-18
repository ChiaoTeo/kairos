from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kairospy.application.agent import (
    AgentApplication,
    DecisionKind,
    DecisionResult,
    IntentCandidate,
    ReduceTargetQuantity,
    TightenLimitPrice,
)
from kairospy.application.agent.policy import DecisionPolicy
from kairospy.application.execution import TargetPositionRequest


def _candidate(request: object) -> IntentCandidate:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    return IntentCandidate(
        decision_id="decision",
        request_id="request",
        intent_id="intent",
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

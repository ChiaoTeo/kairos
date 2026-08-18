from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Mapping

from kairospy.application.execution.intents import (
    MakerExecutionPolicy,
    OptionSpreadRequest,
    PairArbitrageRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
)

from .models import (
    DecisionKind,
    DecisionResult,
    IntentCandidate,
    ReduceTargetQuantity,
    RequireMakerExecution,
    ShortenDeadline,
    TightenLimitPrice,
    TightenMaxSlippage,
    TightenSplitPolicy,
)


@dataclass(frozen=True, slots=True)
class DecisionPolicyOutcome:
    decision: DecisionKind
    effective_request: object | None
    reason: str | None = None


class DecisionPolicy:
    """Deterministic host validation for model-proposed Intent decisions."""

    def __init__(
        self,
        revisions: Mapping[str, object],
        *,
        reason_codes: tuple[str, ...] = (),
        risk_flags: tuple[str, ...] = (),
    ) -> None:
        self._revisions = dict(revisions)
        self._reason_codes = frozenset(reason_codes)
        self._risk_flags = frozenset(risk_flags)

    def apply(
        self, candidate: IntentCandidate, result: DecisionResult
    ) -> DecisionPolicyOutcome:
        unknown_reasons = set(result.reason_codes) - self._reason_codes
        unknown_flags = set(result.risk_flags) - self._risk_flags
        if self._reason_codes and unknown_reasons:
            return DecisionPolicyOutcome(
                DecisionKind.ABSTAIN,
                None,
                "Decision contains reason codes outside the Profile allowlist",
            )
        if self._risk_flags and unknown_flags:
            return DecisionPolicyOutcome(
                DecisionKind.ABSTAIN,
                None,
                "Decision contains risk flags outside the Profile allowlist",
            )
        if result.decision is DecisionKind.APPROVE:
            return DecisionPolicyOutcome(DecisionKind.APPROVE, candidate.request)
        if result.decision in {
            DecisionKind.REJECT,
            DecisionKind.ABSTAIN,
        }:
            return DecisionPolicyOutcome(result.decision, None, result.summary or None)
        effective = candidate.request
        try:
            for revision in result.revisions:
                effective = self._apply_revision(effective, revision)
        except (TypeError, ValueError) as error:
            return DecisionPolicyOutcome(DecisionKind.ABSTAIN, None, str(error))
        return DecisionPolicyOutcome(DecisionKind.REVISE, effective)

    def _apply_revision(self, request: object, revision: object) -> object:
        if isinstance(revision, ReduceTargetQuantity):
            self._require_enabled("allow_quantity_reduction")
            if not isinstance(request, TargetPositionRequest):
                raise TypeError("Quantity revision only supports target_position")
            quantity = _decimal(revision.quantity, "revision quantity")
            if request.quantity == 0:
                raise ValueError("A zero target quantity cannot be reduced")
            if quantity != 0 and (quantity > 0) != (request.quantity > 0):
                raise ValueError("Quantity revision cannot change target direction")
            if abs(quantity) >= abs(request.quantity):
                raise ValueError(
                    "Quantity revision must strictly reduce absolute target"
                )
            return replace(request, quantity=quantity)
        if isinstance(revision, TightenLimitPrice):
            max_adjustment = self._revisions.get("max_price_adjustment_bps")
            if not isinstance(max_adjustment, int):
                raise ValueError("Limit price revision is not enabled")
            if not isinstance(request, TargetPositionRequest):
                raise TypeError("Limit revision only supports target_position")
            if request.limit_price is None:
                raise ValueError("Limit revision requires an original limit price")
            price = _decimal(revision.limit_price, "revision limit_price")
            if price <= 0:
                raise ValueError("Revision limit_price must be positive")
            adjustment = abs(price - request.limit_price) * Decimal(10_000)
            adjustment /= request.limit_price
            if adjustment > max_adjustment:
                raise ValueError("Limit revision exceeds max_price_adjustment_bps")
            if request.quantity > 0 and price > request.limit_price:
                raise ValueError("Buy limit revision cannot increase limit price")
            if request.quantity < 0 and price < request.limit_price:
                raise ValueError("Sell limit revision cannot decrease limit price")
            return replace(request, limit_price=price)
        if isinstance(revision, ShortenDeadline):
            self._require_enabled("allow_deadline_reduction")
            if not isinstance(request, OptionSpreadRequest):
                raise TypeError("Deadline revision only supports option_spread")
            original = request.deadline_unix_nanos
            if original is None or revision.deadline_unix_nanos >= original:
                raise ValueError("Deadline revision must shorten an existing deadline")
            return replace(request, deadline_unix_nanos=revision.deadline_unix_nanos)
        if isinstance(revision, TightenMaxSlippage):
            self._require_enabled("allow_slippage_reduction")
            if not isinstance(request, PairArbitrageRequest):
                raise TypeError("Slippage revision only supports pair_arbitrage")
            original = request.max_slippage_bps
            if (
                original is None
                or revision.max_slippage_bps < 0
                or revision.max_slippage_bps >= original
            ):
                raise ValueError("Slippage revision must reduce an existing limit")
            return replace(request, max_slippage_bps=revision.max_slippage_bps)
        if isinstance(revision, TightenSplitPolicy):
            self._require_enabled("allow_split_tightening")
            if not isinstance(request, TargetPositionRequest):
                raise TypeError("Split revision only supports target_position")
            split = _tightened_split(request.split, revision)
            return replace(request, split=split)
        if isinstance(revision, RequireMakerExecution):
            self._require_enabled("allow_require_maker")
            if not revision.required:
                raise ValueError("RequireMakerExecution cannot disable maker execution")
            if not isinstance(request, TargetPositionRequest):
                raise TypeError("Maker revision only supports target_position")
            if request.maker is not None:
                raise ValueError("Intent already requires maker execution")
            return replace(request, maker=MakerExecutionPolicy())
        raise TypeError(f"Unsupported Intent revision: {type(revision).__name__}")

    def _require_enabled(self, field: str) -> None:
        if self._revisions.get(field) is not True:
            raise ValueError(f"Intent revision is disabled by Profile: {field}")


def _tightened_split(
    original: SplitOrderPolicy | None, revision: TightenSplitPolicy
) -> SplitOrderPolicy:
    max_child = (
        None
        if revision.max_child_quantity is None
        else _decimal(revision.max_child_quantity, "revision max_child_quantity")
    )
    if max_child is not None and max_child <= 0:
        raise ValueError("Split max_child_quantity must be positive")
    if original is not None and original.max_child_quantity is not None:
        if max_child is None or max_child > original.max_child_quantity:
            raise ValueError("Split revision cannot increase max child quantity")
    if original is not None and original.child_count is not None:
        if revision.child_count is None or revision.child_count < original.child_count:
            raise ValueError("Split revision cannot reduce child count")
    if original is not None and original.interval_millis is not None:
        if (
            revision.interval_millis is None
            or revision.interval_millis < original.interval_millis
        ):
            raise ValueError("Split revision cannot shorten child interval")
    return SplitOrderPolicy(
        max_child_quantity=max_child,
        child_count=revision.child_count,
        min_child_quantity=(None if original is None else original.min_child_quantity),
        interval_millis=revision.interval_millis,
    )


def _decimal(value: str, name: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be decimal-compatible") from error


__all__ = ["DecisionPolicy", "DecisionPolicyOutcome"]

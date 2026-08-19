from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Callable

from kairospy.application.execution.intents import (
    OptionSpreadRequest,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    TargetPositionRequest,
)
from kairospy.application.execution.admission import IntentAdmissionEvidence
from kairospy.strategy.results import CommandResult

from ..application import AgentApplication
from ..models import AgentMode, DecisionStatus, IntentCandidate
from .worker import AgentDecisionWorker, DecisionTask


IntentRequest = (
    TargetPositionRequest
    | PairArbitrageRequest
    | PortfolioRebalanceRequest
    | QuoteProvisioningRequest
    | OptionSpreadRequest
)


class AgentControlledExecutionCommands:
    """Concrete decorator for configured Strategy Intent operations."""

    def __init__(
        self,
        commands: object,
        *,
        agent: AgentApplication,
        worker: AgentDecisionWorker,
        launch_id: str,
        profile_hash: str,
        runtime: str,
        model: str | None,
        tool_profiles: tuple[str, ...],
        operations: tuple[str, ...],
        required_contexts: tuple[str, ...],
        max_decision_age_seconds: float,
        classify_exposure: Callable[[IntentRequest], str] | None = None,
        record_admission: Callable[[IntentAdmissionEvidence], None] | None = None,
    ) -> None:
        self._commands = commands
        self._agent = agent
        self._worker = worker
        self._launch_id = launch_id
        self._profile_hash = profile_hash
        self._runtime = runtime
        self._model = model
        self._tool_profiles = tool_profiles
        self._operations = frozenset(operations)
        self._required_contexts = required_contexts
        self._max_decision_age_seconds = max_decision_age_seconds
        self._classify_exposure = classify_exposure or (lambda request: "unknown")
        self._record_admission = record_admission

    def target_position(
        self, request: TargetPositionRequest, **identity
    ) -> CommandResult:
        return self._control("target_position", request, identity)

    def pair_arbitrage(
        self, request: PairArbitrageRequest, **identity
    ) -> CommandResult:
        return self._control("pair_arbitrage", request, identity)

    def portfolio_rebalance(
        self, request: PortfolioRebalanceRequest, **identity
    ) -> CommandResult:
        return self._control("portfolio_rebalance", request, identity)

    def quote_provisioning(
        self, request: QuoteProvisioningRequest, **identity
    ) -> CommandResult:
        return self._control("quote_provisioning", request, identity)

    def option_spread(self, request: OptionSpreadRequest, **identity) -> CommandResult:
        return self._control("option_spread", request, identity)

    def __getattr__(self, name: str):
        return getattr(self._commands, name)

    def _control(
        self,
        operation: str,
        request: IntentRequest,
        identity: dict[str, str],
    ) -> CommandResult:
        submit = getattr(self._commands, operation)
        if operation not in self._operations:
            return submit(request, **identity)
        request_id = identity["request_id"]
        strategy_id = identity["strategy_id"]
        instance_id = identity["instance_id"]
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        original = replace(request, intent_id=intent_id)
        exposure_effect = self._classify_exposure(original)
        if exposure_effect not in {"increase", "reduce", "neutral", "unknown"}:
            raise ValueError("Exposure classifier returned an unsupported value")
        try:
            snapshot = self._agent._snapshot(
                "execution.intent_review",
                required_contexts=self._required_contexts,
            )
        except ValueError as error:
            if exposure_effect == "reduce":
                return submit(original, **identity)
            return CommandResult(
                request_id,
                "rejected",
                {"intent_id": intent_id},
                error=str(error),
                error_code="agent_context_unavailable",
            )
        now = datetime.now(timezone.utc)
        decision_id = _decision_id(strategy_id, instance_id, request_id)
        candidate = IntentCandidate(
            decision_id=decision_id,
            request_id=request_id,
            intent_id=intent_id,
            strategy_id=strategy_id,
            launch_id=self._launch_id,
            instance_id=instance_id,
            operation=operation,
            request=original,
            exposure_effect=exposure_effect,
            profile_hash=self._profile_hash,
            snapshot=snapshot,
            submitted_at=now,
            deadline=now + timedelta(seconds=self._max_decision_age_seconds),
            runtime=self._runtime,
            model=self._model,
            tool_profiles=self._tool_profiles,
        )

        def submit_effective(effective: object) -> CommandResult:
            if not isinstance(effective, type(original)):
                raise TypeError("Agent effective request changed Intent request type")
            admission = None
            if (
                snapshot.mode is not AgentMode.SHADOW
                and self._record_admission is not None
            ):
                admission = IntentAdmissionEvidence(
                    decision_id=decision_id,
                    request_id=request_id,
                    intent_id=intent_id,
                    source="decision_agent",
                    outcome="approved" if effective == original else "revised",
                    original_intent=original,
                    effective_intent=effective,
                    submission_status="submitting",
                )
                self._record_admission(admission)
            try:
                submitted = submit(
                    effective,
                    admission_evidence=admission,
                    **identity,
                )
            except Exception:
                if admission is not None and self._record_admission is not None:
                    self._record_admission(
                        replace(admission, submission_status="indeterminate")
                    )
                raise
            if admission is not None and self._record_admission is not None:
                self._record_admission(
                    replace(admission, submission_status=submitted.status)
                )
            return submitted

        if snapshot.mode is AgentMode.SHADOW:
            return self._worker.submit_shadow(candidate, submit_effective)
        receipt = self._worker.submit(DecisionTask(candidate, submit_effective))
        if receipt.status in {DecisionStatus.FAILED, DecisionStatus.INTERRUPTED} and (
            exposure_effect == "reduce"
        ):
            return submit_effective(original)
        if receipt.status in {DecisionStatus.PENDING, DecisionStatus.RUNNING}:
            return CommandResult(
                request_id,
                "pending",
                {"intent_id": intent_id, "decision_id": decision_id},
            )
        if receipt.status in {DecisionStatus.APPROVED, DecisionStatus.REVISED}:
            return CommandResult(
                request_id,
                "duplicate",
                {"intent_id": intent_id, "decision_id": decision_id},
            )
        return CommandResult(
            request_id,
            "rejected",
            {"intent_id": intent_id, "decision_id": decision_id},
            error=receipt.reason
            or f"Decision admission failed: {receipt.status.value}",
            error_code="agent_decision_not_sent",
        )


def _decision_id(strategy_id: str, instance_id: str, request_id: str) -> str:
    digest = hashlib.sha256(
        f"{strategy_id}\0{instance_id}\0{request_id}".encode("utf-8")
    ).hexdigest()
    return f"decision:{digest}"


__all__ = ["AgentControlledExecutionCommands"]

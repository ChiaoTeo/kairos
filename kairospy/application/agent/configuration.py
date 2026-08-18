from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .models import AgentMode


_RUNTIMES = frozenset({"openai-agents", "fixture"})
_FAILURE_POLICIES = frozenset({"reject_new_exposure"})
_OPERATIONS = frozenset(
    {
        "target_position",
        "pair_arbitrage",
        "portfolio_rebalance",
        "quote_provisioning",
        "option_spread",
    }
)
_FORBIDDEN_SECRET_KEYS = frozenset(
    {"api_key", "api_secret", "authorization", "password", "secret", "token"}
)


@dataclass(frozen=True, slots=True)
class AgentModelConfig:
    provider: str
    model: str
    credential: str
    request_timeout_seconds: float
    max_turns: int
    max_tool_calls: int
    max_input_tokens: int
    max_output_tokens: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "AgentModelConfig":
        _reject_unknown(
            value,
            {
                "provider",
                "model",
                "credential",
                "request_timeout_seconds",
                "max_turns",
                "max_tool_calls",
                "max_input_tokens",
                "max_output_tokens",
            },
            "agent.model",
        )
        _reject_secrets(value, "agent.model")
        provider = _text(value.get("provider"), "agent.model.provider")
        if provider != "openai":
            raise ValueError("agent.model.provider must be openai")
        return cls(
            provider=provider,
            model=_text(value.get("model"), "agent.model.model"),
            credential=_text(value.get("credential"), "agent.model.credential"),
            request_timeout_seconds=_number(
                value.get("request_timeout_seconds", 5),
                "agent.model.request_timeout_seconds",
                minimum=0.1,
                maximum=300,
            ),
            max_turns=_integer(
                value.get("max_turns", 6),
                "agent.model.max_turns",
                minimum=1,
                maximum=32,
            ),
            max_tool_calls=_integer(
                value.get("max_tool_calls", 8),
                "agent.model.max_tool_calls",
                minimum=0,
                maximum=64,
            ),
            max_input_tokens=_integer(
                value.get("max_input_tokens", 32_000),
                "agent.model.max_input_tokens",
                minimum=256,
                maximum=1_000_000,
            ),
            max_output_tokens=_integer(
                value.get("max_output_tokens", 2_000),
                "agent.model.max_output_tokens",
                minimum=64,
                maximum=100_000,
            ),
        )

    def normalized(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "credential": self.credential,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True, slots=True)
class IntentReviewConfig:
    initial_mode: AgentMode
    strategy_selectable_modes: tuple[AgentMode, ...]
    operations: tuple[str, ...]
    failure_policy: str
    max_decision_age_seconds: float
    required_contexts: tuple[str, ...]
    revisions: Mapping[str, object]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "IntentReviewConfig":
        _reject_unknown(
            value,
            {
                "initial_mode",
                "strategy_selectable_modes",
                "operations",
                "failure_policy",
                "max_decision_age_seconds",
                "required_contexts",
                "revisions",
            },
            "agent.capabilities.intent_review",
        )
        initial_mode = _mode(value.get("initial_mode", AgentMode.SHADOW.value))
        selectable = tuple(
            dict.fromkeys(
                _mode(item)
                for item in _string_list(
                    value.get("strategy_selectable_modes", ()),
                    "agent.capabilities.intent_review.strategy_selectable_modes",
                )
            )
        )
        operations = tuple(
            dict.fromkeys(
                _string_list(
                    value.get("operations", ()),
                    "agent.capabilities.intent_review.operations",
                )
            )
        )
        if not operations:
            raise ValueError("agent.capabilities.intent_review.operations is required")
        unknown = tuple(
            operation for operation in operations if operation not in _OPERATIONS
        )
        if unknown:
            raise ValueError(
                "agent.capabilities.intent_review.operations contains unsupported "
                + ", ".join(unknown)
            )
        failure_policy = _text(
            value.get("failure_policy", "reject_new_exposure"),
            "agent.capabilities.intent_review.failure_policy",
        )
        if failure_policy not in _FAILURE_POLICIES:
            raise ValueError(
                "agent.capabilities.intent_review.failure_policy is unsupported"
            )
        required_contexts = tuple(
            dict.fromkeys(
                _string_list(
                    value.get("required_contexts", ()),
                    "agent.capabilities.intent_review.required_contexts",
                )
            )
        )
        revisions = _mapping(
            value.get("revisions"), "agent.capabilities.intent_review.revisions"
        )
        _validate_revisions(revisions)
        return cls(
            initial_mode=initial_mode,
            strategy_selectable_modes=selectable,
            operations=operations,
            failure_policy=failure_policy,
            max_decision_age_seconds=_number(
                value.get("max_decision_age_seconds", 10),
                "agent.capabilities.intent_review.max_decision_age_seconds",
                minimum=0.1,
                maximum=300,
            ),
            required_contexts=required_contexts,
            revisions=dict(revisions),
        )

    def normalized(self) -> dict[str, object]:
        return {
            "initial_mode": self.initial_mode.value,
            "strategy_selectable_modes": [
                mode.value for mode in self.strategy_selectable_modes
            ],
            "operations": list(self.operations),
            "failure_policy": self.failure_policy,
            "max_decision_age_seconds": self.max_decision_age_seconds,
            "required_contexts": list(self.required_contexts),
            "revisions": dict(self.revisions),
        }


@dataclass(frozen=True, slots=True)
class AgentLaunchConfig:
    enabled: bool
    required: bool
    runtime: str
    profile: str | None
    fixture_path: str | None
    max_queue_size: int
    shutdown_timeout_seconds: float
    model: AgentModelConfig | None
    intent_review: IntentReviewConfig | None
    mcp: tuple[Mapping[str, object], ...]
    profile_snapshot: Mapping[str, object] | None = None

    @classmethod
    def disabled(cls) -> "AgentLaunchConfig":
        return cls(
            False,
            False,
            "openai-agents",
            None,
            None,
            128,
            5.0,
            None,
            None,
            (),
            None,
        )

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object], *, launch_mode: str
    ) -> "AgentLaunchConfig":
        enabled = _boolean(value.get("enabled", False), "agent.enabled")
        required = _boolean(value.get("required", False), "agent.required")
        if required and not enabled:
            raise ValueError(
                "agent.required cannot be true when agent.enabled is false"
            )
        if not enabled:
            unexpected = set(value) - {"enabled", "required"}
            if unexpected:
                raise ValueError("disabled agent cannot configure runtime resources")
            return cls.disabled()
        _reject_secrets(value, "agent")
        _reject_unknown(
            value,
            {
                "enabled",
                "required",
                "runtime",
                "profile",
                "fixture_path",
                "max_queue_size",
                "shutdown_timeout_seconds",
                "model",
                "capabilities",
                "mcp",
            },
            "agent",
        )
        runtime = _text(value.get("runtime", "openai-agents"), "agent.runtime")
        if runtime not in _RUNTIMES:
            raise ValueError("agent.runtime must be openai-agents or fixture")
        if launch_mode == "backtest" and runtime != "fixture":
            raise ValueError("backtest agent.runtime must be fixture")
        if launch_mode != "backtest" and runtime == "fixture":
            raise ValueError("fixture agent.runtime is only valid for backtest")
        profile = _text(value.get("profile"), "agent.profile")
        fixture_path = None
        if runtime == "fixture":
            fixture_path = _text(value.get("fixture_path"), "agent.fixture_path")
        elif value.get("fixture_path") is not None:
            raise ValueError("agent.fixture_path is only valid for fixture runtime")
        model_value = _mapping(value.get("model"), "agent.model")
        model = (
            None
            if runtime == "fixture" and not model_value
            else AgentModelConfig.from_mapping(model_value)
        )
        capabilities = _mapping(value.get("capabilities"), "agent.capabilities")
        _reject_unknown(capabilities, {"intent_review"}, "agent.capabilities")
        review_value = _mapping(
            capabilities.get("intent_review"),
            "agent.capabilities.intent_review",
        )
        if not review_value:
            raise ValueError("agent.capabilities.intent_review is required")
        intent_review = IntentReviewConfig.from_mapping(review_value)
        if launch_mode != "backtest" and (
            intent_review.initial_mode is not AgentMode.SHADOW
        ):
            raise ValueError("paper/live Agent initial_mode must be shadow")
        raw_mcp = value.get("mcp", ())
        if not isinstance(raw_mcp, (list, tuple)):
            raise ValueError("agent.mcp must be an array of tables")
        mcp = tuple(_normalize_mcp(item, index) for index, item in enumerate(raw_mcp))
        return cls(
            enabled=True,
            required=required,
            runtime=runtime,
            profile=profile,
            fixture_path=fixture_path,
            max_queue_size=_integer(
                value.get("max_queue_size", 128),
                "agent.max_queue_size",
                minimum=1,
                maximum=100_000,
            ),
            shutdown_timeout_seconds=_number(
                value.get("shutdown_timeout_seconds", 5),
                "agent.shutdown_timeout_seconds",
                minimum=0,
                maximum=300,
            ),
            model=model,
            intent_review=intent_review,
            mcp=mcp,
            profile_snapshot=None,
        )

    def normalized(self) -> dict[str, object]:
        if not self.enabled:
            return {"enabled": False, "required": False}
        return {
            "enabled": True,
            "required": self.required,
            "runtime": self.runtime,
            "profile": self.profile,
            "fixture_path": self.fixture_path,
            "max_queue_size": self.max_queue_size,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
            "model": None if self.model is None else self.model.normalized(),
            "capabilities": {
                "intent_review": self.intent_review.normalized()
                if self.intent_review is not None
                else None
            },
            "mcp": [dict(item) for item in self.mcp],
        }


def _normalize_mcp(value: object, index: int) -> Mapping[str, object]:
    mapping = _mapping(value, f"agent.mcp[{index}]")
    _reject_unknown(mapping, {"server", "profile", "required"}, f"agent.mcp[{index}]")
    _reject_secrets(mapping, f"agent.mcp[{index}]")
    return {
        "server": _text(mapping.get("server"), f"agent.mcp[{index}].server"),
        "profile": _text(mapping.get("profile"), f"agent.mcp[{index}].profile"),
        "required": _boolean(
            mapping.get("required", False), f"agent.mcp[{index}].required"
        ),
    }


def _validate_revisions(value: Mapping[str, object]) -> None:
    boolean_fields = (
        "allow_quantity_reduction",
        "allow_deadline_reduction",
        "allow_slippage_reduction",
        "allow_split_tightening",
        "allow_require_maker",
    )
    _reject_unknown(
        value,
        {*boolean_fields, "max_price_adjustment_bps"},
        "agent.capabilities.intent_review.revisions",
    )
    for field in boolean_fields:
        if field in value:
            _boolean(
                value[field], f"agent.capabilities.intent_review.revisions.{field}"
            )
    if "max_price_adjustment_bps" in value:
        _integer(
            value["max_price_adjustment_bps"],
            "agent.capabilities.intent_review.revisions.max_price_adjustment_bps",
            minimum=0,
            maximum=10_000,
        )


def _reject_secrets(value: Mapping[str, object], prefix: str) -> None:
    for key in value:
        if str(key).lower() in _FORBIDDEN_SECRET_KEYS:
            raise ValueError(f"{prefix}.{key} is forbidden; use a credential reference")


def _reject_unknown(
    value: Mapping[str, object], allowed: set[str], prefix: str
) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ValueError(f"{prefix}.{unknown[0]} is unsupported")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a table")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(value: object, name: str, *, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if result < minimum or result > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _mode(value: object) -> AgentMode:
    try:
        return AgentMode(str(value))
    except ValueError as error:
        raise ValueError("Agent mode must be shadow, gate, or revise") from error


__all__ = ["AgentLaunchConfig", "AgentModelConfig", "IntentReviewConfig"]

"""Launch-owned normalized inputs consumed by the Strategy process."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Literal, Mapping, cast

from ..domain.identity import LaunchIdentity


@dataclass(frozen=True, slots=True)
class StrategyLaunchConfig:
    """Typed projection of canonical Launch config needed by Strategy assembly."""

    identity: LaunchIdentity
    market_scope: Literal["shared", "instance"]
    execution_enabled: bool
    allow_trading: bool
    max_order_notional: Decimal | None
    require_limit_orders: bool
    replay_end: datetime | None
    authoritative: bool

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        launch_id: str,
        mode: str,
        allow_ad_hoc_defaults: bool = True,
    ) -> "StrategyLaunchConfig":
        """Load one typed view, with explicit env overrides for ad-hoc runs."""

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            authoritative = True
        except FileNotFoundError:
            if not allow_ad_hoc_defaults:
                raise
            raw = {"launch": {"id": launch_id, "mode": mode}}
            authoritative = False
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid normalized launch config: {path}") from error
        if not isinstance(raw, Mapping):
            raise ValueError("normalized launch config must be an object")
        launch = _mapping(raw.get("launch"), "launch")
        actual_launch_id = _text(launch.get("id", launch_id), "launch.id")
        actual_mode = _text(launch.get("mode", mode), "launch.mode")
        if actual_launch_id != launch_id or actual_mode != mode:
            raise ValueError(
                "normalized launch identity does not match Strategy instance"
            )

        scope_value = os.environ.get(
            "KAIROS_MARKET_SCOPE",
            str(raw.get("market_scope", "shared" if mode == "live" else "instance")),
        )
        if scope_value not in {"shared", "instance"}:
            raise ValueError("market scope must be shared or instance")

        execution = _mapping(raw.get("execution"), "execution")
        live_safety = _mapping(raw.get("live_safety"), "live_safety")
        execution_enabled = _boolean(
            execution.get("enabled", True), "execution.enabled"
        )
        allow_trading = mode != "live" or _env_boolean(
            "KAIROS_LIVE_TRADING_ENABLED",
            live_safety.get("trading_enabled", False),
        )
        require_limit_orders = mode == "live" and _env_boolean(
            "KAIROS_LIVE_REQUIRE_LIMIT_ORDERS",
            live_safety.get("require_limit_orders", True),
        )
        raw_max_notional = os.environ.get(
            "KAIROS_LIVE_MAX_ORDER_NOTIONAL",
            None
            if live_safety.get("max_order_notional") is None
            else str(live_safety["max_order_notional"]),
        )
        max_order_notional = _optional_positive_decimal(
            raw_max_notional, "live_safety.max_order_notional"
        )
        replay_end = _optional_datetime(
            os.environ.get("KAIROS_BACKTEST_END")
            or _mapping(raw.get("backtest_market"), "backtest_market").get("end"),
            "backtest_market.end",
        )
        return cls(
            identity=LaunchIdentity(actual_launch_id, actual_mode),
            market_scope=cast(Literal["shared", "instance"], scope_value),
            execution_enabled=execution_enabled,
            allow_trading=allow_trading,
            max_order_notional=max_order_notional,
            require_limit_orders=require_limit_orders,
            replay_end=replay_end if mode == "backtest" else None,
            authoritative=authoritative,
        )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"normalized launch {name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"normalized launch {name} is required")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"normalized launch {name} must be a boolean")
    return value


def _env_boolean(name: str, default: object) -> bool:
    value = os.environ.get(name)
    if value is None:
        return _boolean(default, name)
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _optional_positive_decimal(value: object, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a decimal") from error
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _optional_datetime(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error

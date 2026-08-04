from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Mapping, TypeVar

from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.usecases.execution.application.runtime import BasisPointSlippageModel
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.application.usecases.strategy.application.entrypoint import load_strategy_entrypoint
from kairospy.application.support.launch.application.config.launch import ConfigError, LaunchConfig, load_launch_config

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


def load_required_launch_config(
    config_path: Path,
    *,
    mode: RuntimeMode,
    error_type: type[ConfigErrorT],
    strategy_ref: str | None = None,
) -> LaunchConfig:
    try:
        launch_config = load_launch_config(config_path)
        if strategy_ref is not None:
            launch_config = launch_config.with_launch_strategy(strategy_ref)
        launch_config.require_mode(mode.value)
    except ConfigError as error:
        raise error_type(str(error)) from error
    if launch_config.strategy is None:
        raise error_type("launch.strategy is required")
    return launch_config


def table(value: object, name: str, error_type: type[ConfigErrorT], *, allow_none: bool = True) -> Mapping[str, object]:
    if value is None and allow_none:
        return {}
    if not isinstance(value, Mapping):
        raise error_type(f"[{name}] must be a table")
    return value


def strategy_params(values: Mapping[str, object], error_type: type[ConfigErrorT]) -> Mapping[str, object]:
    strategy = table(values.get("strategy"), "strategy", error_type) if values.get("strategy") is not None else {}
    params = strategy.get("params", {})
    if not isinstance(params, Mapping):
        raise error_type("[strategy.params] must be a table")
    return params


def load_strategy(ref: str, *, root: Path, params: Mapping[str, object], error_type: type[ConfigErrorT]) -> Strategy:
    return load_strategy_entrypoint(ref, root=root, params=params, error_type=error_type).strategy


def resolve_path(value: object, *, root: Path, source: str, error_type: type[ConfigErrorT]) -> Path:
    if value is None:
        raise error_type(f"{source} is required")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def read_jsonl(path: Path, error_type: type[ConfigErrorT]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise error_type(f"event row must be a JSON object: {path}")
            rows.append(value)
    if not rows:
        raise error_type(f"event file has no rows: {path}")
    return rows


def required_text(value: object, source: str, error_type: type[ConfigErrorT]) -> str:
    text = str(value or "").strip()
    if not text:
        raise error_type(f"{source} is required")
    return text


def optional_text(value: object, source: str, error_type: type[ConfigErrorT]) -> str | None:
    if value is None:
        return None
    return required_text(value, source, error_type)


def optional_int(value: object, source: str, error_type: type[ConfigErrorT], *, positive: bool = False) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise error_type(f"{source} must be an integer")
    try:
        parsed = int(value)
    except Exception as error:
        raise error_type(f"{source} must be an integer") from error
    if positive and parsed < 1:
        raise error_type(f"{source} must be positive")
    if not positive and parsed < 0:
        raise error_type(f"{source} cannot be negative")
    return parsed


def int_value(value: object, source: str, error_type: type[ConfigErrorT]) -> int:
    if not isinstance(value, int):
        raise error_type(f"{source} must be an integer")
    if value < 0:
        raise error_type(f"{source} cannot be negative")
    return value


def bool_value(value: object, source: str, error_type: type[ConfigErrorT]) -> bool:
    if not isinstance(value, bool):
        raise error_type(f"{source} must be a boolean")
    return value


def account_selector(value: object, source: str, error_type: type[ConfigErrorT]) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise error_type(f"{source} must be an account id or integer account index")
    if isinstance(value, int):
        return value
    return required_text(value, source, error_type)


def params_table(
    value: object,
    *,
    default: Mapping[str, object] | None = None,
    source: str,
    error_type: type[ConfigErrorT],
) -> Mapping[str, object]:
    values = dict(default or {})
    if value is None:
        return values
    if not isinstance(value, Mapping):
        raise error_type(f"{source} params must be a table")
    values.update(value)
    return values


def slippage_model(execution: Mapping[str, object]) -> BasisPointSlippageModel | None:
    bps = execution.get("slippage_bps")
    return None if bps is None else BasisPointSlippageModel(Decimal(str(bps)))


def jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


__all__ = [
    "account_selector",
    "bool_value",
    "int_value",
    "jsonable",
    "load_required_launch_config",
    "load_strategy",
    "optional_int",
    "optional_text",
    "params_table",
    "read_jsonl",
    "required_text",
    "resolve_path",
    "slippage_model",
    "strategy_params",
    "table",
]

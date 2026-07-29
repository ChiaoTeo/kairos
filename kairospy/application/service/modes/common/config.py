from __future__ import annotations

import importlib
import json
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
import sys
from typing import Mapping, TypeVar

from kairospy.application.runtime import RuntimeMode
from kairospy.application.service.domain.execution import BasisPointSlippageModel
from kairospy.application.strategy import Strategy
from kairospy.config import ConfigError, RunConfig, load_run_config

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


def load_required_run_config(config_path: Path, *, mode: RuntimeMode, error_type: type[ConfigErrorT]) -> RunConfig:
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode(mode.value)
    except ConfigError as error:
        raise error_type(str(error)) from error
    if run_config.strategy is None:
        raise error_type("run.strategy is required")
    return run_config


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
    if ":" not in ref:
        raise error_type("run.strategy must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    project_root = _project_root(root)
    inserted = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        inserted = True
    if inserted:
        sys.modules.pop(module_name, None)
    try:
        module = importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(project_root))
            except ValueError:
                pass
    factory = getattr(module, attr_name)
    strategy = factory(**dict(params)) if callable(factory) else factory
    if not hasattr(strategy, "strategy_id"):
        raise error_type(f"strategy factory did not return a Strategy: {ref}")
    return strategy


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


def _project_root(root: Path) -> Path:
    for directory in (root, *root.parents):
        if (directory / "pyproject.toml").exists() or (directory / "kairos.toml").exists():
            return directory
    return root


__all__ = [
    "account_selector",
    "bool_value",
    "int_value",
    "jsonable",
    "load_required_run_config",
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

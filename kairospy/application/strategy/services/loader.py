from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from kairospy.strategy import validate_strategy

from ..protocol import Strategy


@dataclass(frozen=True, slots=True)
class StrategyEntrypoint:
    ref: str
    strategy: Strategy
    module_file: Path | None


def load_strategy(ref: str, *, root: Path, params: Mapping[str, object] | None = None) -> StrategyEntrypoint:
    if ":" not in ref:
        raise ValueError("strategy ref must be module:callable")
    module_name, attribute = ref.split(":", 1)
    if not module_name or not attribute:
        raise ValueError("strategy ref must contain module and callable")
    inserted = str(root) not in sys.path
    if inserted:
        sys.path.insert(0, str(root))
    try:
        module = importlib.import_module(module_name)
        target = getattr(module, attribute)
        if inspect.isclass(target) or callable(target):
            strategy = target(**dict(params or {}))
        else:
            strategy = target
    except AttributeError as error:
        raise ValueError(f"strategy entrypoint was not found: {ref}") from error
    finally:
        if inserted:
            sys.path.remove(str(root))
    try:
        validate_strategy(strategy)
    except ValueError as error:
        raise ValueError(f"strategy entrypoint does not implement StrategyProtocol: {ref}: {error}") from error
    return StrategyEntrypoint(ref, strategy, Path(module.__file__).resolve() if getattr(module, "__file__", None) else None)

from __future__ import annotations

from dataclasses import dataclass
import importlib
import inspect
from pathlib import Path
import sys
from typing import Mapping

from kairospy.application.strategy.protocol import Strategy


@dataclass(frozen=True, slots=True)
class StrategyEntrypoint:
    ref: str
    module_name: str
    attribute_name: str
    module_file: Path | None
    strategy: Strategy

    @property
    def metadata(self) -> Mapping[str, object]:
        return {
            "type": "strategy",
            "ref": self.ref,
            "module": self.module_name,
            "attribute": self.attribute_name,
            "module_file": None if self.module_file is None else str(self.module_file),
            "strategy_id": self.strategy.strategy_id,
        }


def load_strategy_entrypoint(
    ref: str,
    *,
    root: Path,
    env: object | None = None,
    params: Mapping[str, object] | None = None,
    error_type: type[Exception] = ValueError,
) -> StrategyEntrypoint:
    if ":" not in ref:
        raise error_type("run.strategy must be module:callable")
    module_name, attribute_name = ref.split(":", 1)
    module = _import_module(module_name, root=root)
    try:
        target = getattr(module, attribute_name)
    except AttributeError as error:
        raise error_type(f"strategy entrypoint was not found: {ref}") from error
    strategy = _build_strategy(target, env=env, params=dict(params or {}), ref=ref, error_type=error_type)
    if not hasattr(strategy, "strategy_id"):
        raise error_type(f"strategy factory did not return a Strategy: {ref}")
    module_file = getattr(module, "__file__", None)
    return StrategyEntrypoint(
        ref=ref,
        module_name=module_name,
        attribute_name=attribute_name,
        module_file=None if module_file is None else Path(module_file).resolve(),
        strategy=strategy,
    )


def _import_module(module_name: str, *, root: Path):
    project_root = _project_root(root)
    inserted = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        inserted = True
    if inserted:
        sys.modules.pop(module_name, None)
    try:
        return importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(project_root))
            except ValueError:
                pass


def _build_strategy(
    target: object,
    *,
    env: object | None,
    params: Mapping[str, object],
    ref: str,
    error_type: type[Exception],
) -> Strategy:
    if inspect.isclass(target):
        return _call_factory(target, env=env, params=params, ref=ref, error_type=error_type)
    if callable(target):
        return _call_factory(target, env=env, params=params, ref=ref, error_type=error_type)
    return target  # type: ignore[return-value]


def _call_factory(
    factory,
    *,
    env: object | None,
    params: Mapping[str, object],
    ref: str,
    error_type: type[Exception],
) -> Strategy:
    signature = inspect.signature(factory)
    parameters = signature.parameters
    if env is not None and "env" in parameters:
        return factory(env=env)
    if env is not None and len(parameters) == 1 and next(iter(parameters.values())).name == "env":
        return factory(env)
    try:
        return factory(**dict(params))
    except TypeError as error:
        if params:
            raise error_type(f"strategy factory parameters did not match [strategy.params]: {ref}: {error}") from error
        return factory()


def _project_root(root: Path) -> Path:
    for directory in (root, *root.parents):
        if (directory / "pyproject.toml").exists() or (directory / ".kairos" / "kairos.toml").exists():
            return directory
    return root


__all__ = ["StrategyEntrypoint", "load_strategy_entrypoint"]

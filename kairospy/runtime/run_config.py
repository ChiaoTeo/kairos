from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import tomllib

from kairospy.reference.contracts import ProductType
from kairospy.strategy.contracts import StrategyLifecycle, StrategySpec


class RunConfigError(ValueError):
    """Raised when a reusable RunConfig file is invalid."""


@dataclass(frozen=True, slots=True)
class RunConfigValidationReport:
    path: Path
    valid: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "valid": self.valid,
            "issues": list(self.issues),
        }


class RunConfigResolver:
    def __init__(self, *, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve() if project_root is not None else None

    def load(self, path: str | Path) -> "RunConfig":
        return RunConfig.load(path, project_root=self.project_root)

    def validate(self, path: str | Path | "RunConfig") -> RunConfigValidationReport:
        config = path if isinstance(path, RunConfig) else self.load(path)
        issues = tuple(config.validate())
        return RunConfigValidationReport(config.path, not issues, issues)

    def explain(self, path: str | Path | "RunConfig") -> dict[str, Any]:
        config = path if isinstance(path, RunConfig) else self.load(path)
        return config.explain()

    def to_start_args(
        self,
        path: str | Path | "RunConfig",
        *,
        confirm_live: bool = False,
        supervise_live_services: bool = False,
        param_overrides: tuple[str, ...] = (),
    ) -> SimpleNamespace:
        config = path if isinstance(path, RunConfig) else self.load(path)
        return config.to_start_args(
            confirm_live=confirm_live,
            supervise_live_services=supervise_live_services,
            param_overrides=param_overrides,
        )


@dataclass(frozen=True, slots=True)
class RunConfig:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path, *, project_root: str | Path | None = None) -> "RunConfig":
        raw_path = Path(path).expanduser()
        if not raw_path.is_absolute() and project_root is not None:
            raw_path = Path(project_root).expanduser().resolve() / raw_path
        config_path = raw_path.resolve()
        if not config_path.exists():
            raise RunConfigError(f"RunConfig does not exist: {config_path}")
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise RunConfigError(f"invalid TOML in RunConfig {config_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RunConfigError(f"RunConfig root must be a TOML table: {config_path}")
        return cls(config_path, data)

    def get(self, dotted_path: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in tuple(part for part in dotted_path.split(".") if part):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def validate(self) -> list[str]:
        return list(self.validation_report().issues)

    def validation_report(self) -> RunConfigValidationReport:
        issues: list[str] = []
        run = self.get("run")
        if not isinstance(run, dict):
            return RunConfigValidationReport(self.path, False, ("[run] table is required",))
        name = _required_text(run, "name")
        mode = _required_text(run, "mode")
        workspace = _required_text(run, "workspace")
        entrypoint = _required_text(run, "entrypoint")
        if not name:
            issues.append("run.name is required")
        if mode not in {"backtest", "historical-simulation", "paper", "live"}:
            issues.append("run.mode must be one of: backtest, historical-simulation, paper, live")
        if not workspace:
            issues.append("run.workspace is required")
        if not entrypoint:
            issues.append("run.entrypoint is required")
        if entrypoint and ":" not in entrypoint:
            issues.append("run.entrypoint must be module:callable")
        params = self.get("params", {})
        if params is not None and not isinstance(params, dict):
            issues.append("[params] must be a table")
        bindings = self.get("bindings", {})
        if bindings is not None and not isinstance(bindings, dict):
            issues.append("[bindings] must be a table")
        if mode in {"paper", "live"} and isinstance(bindings, dict) and not _required_text(bindings, "account"):
            issues.append("bindings.account is required for paper/live runs")
        guards = self.get("guards", {})
        if guards is not None and not isinstance(guards, dict):
            issues.append("[guards] must be a table")
        strategy = self.get("strategy", {})
        if strategy is not None and not isinstance(strategy, dict):
            issues.append("[strategy] must be a table")
        elif isinstance(strategy, dict) and strategy:
            issues.extend(_strategy_issues(strategy))
        if mode == "live":
            live = self.get("live")
            if not isinstance(live, dict):
                issues.append("[live] table is required for live runs")
            elif not _required_text(live, "provider"):
                issues.append("live.provider is required for live runs")
            evidence = self.get("evidence")
            if not isinstance(evidence, dict):
                issues.append("[evidence] table is required for live runs")
            else:
                if not _required_text(evidence, "readiness"):
                    issues.append("evidence.readiness is required for live runs")
                if not _required_text(evidence, "promotion"):
                    issues.append("evidence.promotion is required for live runs")
        return RunConfigValidationReport(self.path, not issues, tuple(issues))

    def require_valid(self) -> None:
        issues = self.validate()
        if issues:
            raise RunConfigError("; ".join(issues))

    def to_start_args(
        self,
        *,
        confirm_live: bool = False,
        supervise_live_services: bool = False,
        param_overrides: tuple[str, ...] = (),
    ) -> SimpleNamespace:
        self.require_valid()
        run = self.get("run")
        assert isinstance(run, dict)
        params = _params_as_cli_values(self.get("params", {}))
        params.extend(param_overrides)
        return SimpleNamespace(
            config=self.path,
            workspace=str(run["workspace"]),
            entrypoint=str(run["entrypoint"]),
            mode=str(run["mode"]),
            param=params,
            confirm_live=confirm_live,
            supervise_live_services=supervise_live_services,
            _run_config=self,
        )

    def explain(self) -> dict[str, Any]:
        report = self.validation_report()
        run = self.get("run", {})
        bindings = self.get("bindings", {})
        return {
            "path": str(self.path),
            "valid": report.valid,
            "issues": list(report.issues),
            "run": dict(run) if isinstance(run, dict) else run,
            "params": dict(self.get("params", {})) if isinstance(self.get("params", {}), dict) else self.get("params"),
            "bindings": dict(bindings) if isinstance(bindings, dict) else bindings,
            "guards": dict(self.get("guards", {})) if isinstance(self.get("guards", {}), dict) else self.get("guards"),
            "strategy": (
                dict(self.get("strategy", {}))
                if isinstance(self.get("strategy", {}), dict)
                else self.get("strategy")
            ),
            "live": dict(self.get("live", {})) if isinstance(self.get("live", {}), dict) else self.get("live"),
        }

    def strategy_spec(
        self,
        *,
        project_root: str | Path | None = None,
        default_evidence_hash: str | None = None,
    ) -> StrategySpec | None:
        strategy = self.get("strategy", {})
        if strategy in (None, {}):
            return None
        if not isinstance(strategy, dict):
            raise RunConfigError("[strategy] must be a table")
        spec_ref = _optional_text(strategy, "spec") or _optional_text(strategy, "spec_ref")
        if spec_ref:
            return _load_strategy_spec(spec_ref, project_root or self.path.parent)
        if "stop_policy" in strategy:
            raise RunConfigError("strategy.stop_policy is not supported in RunConfig; declare stop policy in StrategySpec code")
        return _strategy_spec_from_table(strategy, default_evidence_hash=default_evidence_hash)


def load_run_config(path: str | Path, *, project_root: str | Path | None = None) -> RunConfig:
    return RunConfig.load(path, project_root=project_root)


def _required_text(table: dict[str, Any], key: str) -> str:
    value = table.get(key)
    return str(value).strip() if value is not None else ""


def _optional_text(table: dict[str, Any], key: str) -> str:
    value = table.get(key)
    return str(value).strip() if value is not None else ""


def _params_as_cli_values(params: Any) -> list[str]:
    if params in (None, {}):
        return []
    if not isinstance(params, dict):
        raise RunConfigError("[params] must be a table")
    values: list[str] = []
    for key, value in params.items():
        if not isinstance(key, str) or not key.strip():
            raise RunConfigError("params keys must be non-empty strings")
        values.append(f"{key}={value}")
    return values


def _strategy_issues(strategy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    spec_ref = _optional_text(strategy, "spec") or _optional_text(strategy, "spec_ref")
    if spec_ref:
        if ":" not in spec_ref:
            issues.append("strategy.spec must be module:callable")
        if "stop_policy" in strategy:
            issues.append("strategy.stop_policy is not supported; declare stop policy in StrategySpec code")
        return issues
    if "stop_policy" in strategy:
        issues.append("strategy.stop_policy is not supported; declare stop policy in StrategySpec code")
    if not (_optional_text(strategy, "strategy_id") or _optional_text(strategy, "id")):
        issues.append("strategy.strategy_id is required when strategy.spec is not provided")
    if not _optional_text(strategy, "version"):
        issues.append("strategy.version is required when strategy.spec is not provided")
    products = strategy.get("products")
    if not isinstance(products, list) or not products:
        issues.append("strategy.products must be a non-empty list when strategy.spec is not provided")
    if _optional_text(strategy, "risk_budget_fraction"):
        try:
            Decimal(_optional_text(strategy, "risk_budget_fraction"))
        except Exception:
            issues.append("strategy.risk_budget_fraction must be decimal text")
    else:
        issues.append("strategy.risk_budget_fraction is required when strategy.spec is not provided")
    return issues


def _load_strategy_spec(ref: str, project_root: str | Path) -> StrategySpec:
    if ":" not in ref:
        raise RunConfigError("strategy.spec must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    if not module_name.strip() or not attr_name.strip():
        raise RunConfigError("strategy.spec must be module:callable")
    root = Path(project_root).expanduser().resolve()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        _drop_stale_project_module(module_name, root)
        importlib.invalidate_caches()
        module = importlib.import_module(module_name)
        candidate = getattr(module, attr_name)
        spec = candidate() if callable(candidate) else candidate
    except Exception as exc:
        raise RunConfigError(f"failed to load strategy.spec {ref}: {exc}") from exc
    if not isinstance(spec, StrategySpec):
        raise RunConfigError(f"strategy.spec must resolve to StrategySpec: {ref}")
    return spec


def _drop_stale_project_module(module_name: str, project_root: Path) -> None:
    parts = module_name.split(".")
    top_level = parts[0]
    if not ((project_root / f"{top_level}.py").exists() or (project_root / top_level).exists()):
        return
    for index in range(1, len(parts) + 1):
        candidate = ".".join(parts[:index])
        module = sys.modules.get(candidate)
        if module is None:
            continue
        locations = []
        module_file = getattr(module, "__file__", None)
        if module_file:
            locations.append(Path(str(module_file)))
        locations.extend(Path(str(item)) for item in getattr(module, "__path__", ()) or ())
        if not locations:
            continue
        try:
            resolved = tuple(path.resolve() for path in locations)
        except OSError:
            continue
        if not any(path.is_relative_to(project_root) for path in resolved):
            sys.modules.pop(candidate, None)


def _strategy_spec_from_table(strategy: dict[str, Any], *, default_evidence_hash: str | None) -> StrategySpec:
    evidence_hash = _optional_text(strategy, "evidence_hash") or str(default_evidence_hash or "")
    try:
        return StrategySpec(
            _required_strategy_text(strategy, "strategy_id", fallback_key="id"),
            _required_strategy_text(strategy, "version"),
            StrategyLifecycle(_optional_text(strategy, "lifecycle") or StrategyLifecycle.DRAFT.value),
            tuple(ProductType(str(item)) for item in _required_strategy_list(strategy, "products")),
            _string_tuple(strategy, "strategy_archetypes"),
            _string_tuple(strategy, "return_drivers"),
            _string_tuple(strategy, "risk_drivers"),
            _mapping_tuple(strategy, "universe"),
            _string_tuple(strategy, "features"),
            _mapping_tuple(strategy, "signal"),
            _mapping_tuple(strategy, "portfolio_construction"),
            _string_tuple(strategy, "entry_rules"),
            _string_tuple(strategy, "exit_rules"),
            _string_tuple(strategy, "rebalance_rules"),
            Decimal(_required_strategy_text(strategy, "risk_budget_fraction")),
            _string_tuple(strategy, "required_data_capabilities"),
            _string_tuple(strategy, "required_execution_capabilities"),
            evidence_hash,
        )
    except ValueError as exc:
        raise RunConfigError(f"invalid [strategy] table: {exc}") from exc


def _required_strategy_text(strategy: dict[str, Any], key: str, *, fallback_key: str | None = None) -> str:
    value = _optional_text(strategy, key)
    if not value and fallback_key is not None:
        value = _optional_text(strategy, fallback_key)
    if not value:
        raise RunConfigError(f"strategy.{key} is required")
    return value


def _required_strategy_list(strategy: dict[str, Any], key: str) -> list[object]:
    value = strategy.get(key)
    if not isinstance(value, list) or not value:
        raise RunConfigError(f"strategy.{key} must be a non-empty list")
    return value


def _string_tuple(strategy: dict[str, Any], key: str) -> tuple[str, ...]:
    value = strategy.get(key, ())
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise RunConfigError(f"strategy.{key} must be a list")
    return tuple(str(item) for item in value)


def _mapping_tuple(strategy: dict[str, Any], key: str) -> tuple[tuple[str, Any], ...]:
    value = strategy.get(key, {})
    if value in (None, ""):
        return ()
    if not isinstance(value, dict):
        raise RunConfigError(f"strategy.{key} must be a table")
    return tuple(sorted((str(item_key), item_value) for item_key, item_value in value.items()))

"""Launch-owned TOML configuration use cases.

The launch configuration describes one run.  Account definitions and
credentials remain workspace-owned; launch files only reference them.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


VALID_MODES = frozenset({"backtest", "paper", "live"})
SYSTEM_LAUNCH_ID = "kairos-system"


class LaunchConfigError(ValueError):
    """Raised when a launch TOML file is missing or invalid."""


@dataclass(frozen=True, slots=True)
class LaunchConfigReport:
    path: Path
    valid: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """Mode-specific launch inputs after TOML validation.

    This is still launch-owned configuration.  Concrete market/account
    connectors are selected by composition and are deliberately absent here.
    """

    launch_id: str
    mode: str
    strategy_ref: str
    strategy_params: Mapping[str, Any]
    account_refs: tuple[str, ...]
    execution: Mapping[str, Any]
    mode_config: Mapping[str, Any]
    backtest_market: Mapping[str, Any] | None = None
    backtest_data_root: Path | None = None
    backtest_storage_format: str | None = None
    paper_events: Path | None = None
    live_safety: Mapping[str, Any] | None = None
    live_private_sync: Mapping[str, Any] | None = None

    def normalized(self) -> dict[str, Any]:
        return _jsonable({
            "launch": {"id": self.launch_id, "mode": self.mode, "strategy": self.strategy_ref},
            "strategy": {"params": dict(self.strategy_params)},
            "accounts": list(self.account_refs),
            "execution": dict(self.execution),
            self.mode: dict(self.mode_config),
            "backtest_market": self.backtest_market,
            "backtest_data_root": self.backtest_data_root,
            "backtest_storage_format": self.backtest_storage_format,
            "paper_events": self.paper_events,
            "live_safety": self.live_safety,
            "live_private_sync": self.live_private_sync,
        })


@dataclass(frozen=True, slots=True)
class LaunchConfig:
    path: Path
    root: Path
    values: Mapping[str, Any]

    @classmethod
    def load(cls, path: str | Path, *, root: str | Path | None = None) -> "LaunchConfig":
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise LaunchConfigError(f"launch config does not exist: {source}")
        try:
            values = tomllib.loads(source.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise LaunchConfigError(f"invalid TOML in launch config {source}: {error}") from error
        if not isinstance(values, dict):
            raise LaunchConfigError(f"launch config root must be a TOML table: {source}")
        return cls(source, Path(root).expanduser().resolve() if root is not None else source.parent, values)

    @property
    def launch(self) -> Mapping[str, Any]:
        return _table(self.values.get("launch"), "launch")

    @property
    def launch_id(self) -> str:
        value = self.launch.get("id", self.path.stem)
        return _text(value, "launch.id")

    @property
    def mode(self) -> str:
        return _text(self.launch.get("mode"), "launch.mode")

    @property
    def strategy(self) -> str | None:
        value = self.launch.get("strategy")
        return None if value is None else _text(value, "launch.strategy")

    @property
    def account_ref(self) -> str | None:
        account = _optional_table(self.values.get("account"), "account")
        value = account.get("ref")
        return None if value is None else _text(value, "account.ref")

    @property
    def account_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        if self.account_ref is not None:
            refs.append(self.account_ref)
        accounts = self.values.get("accounts")
        if isinstance(accounts, Mapping):
            for alias, value in accounts.items():
                if not isinstance(value, Mapping) or "ref" not in value:
                    continue
                refs.append(_text(value["ref"], f"accounts.{alias}.ref"))
        return tuple(dict.fromkeys(refs))

    @property
    def strategy_params(self) -> Mapping[str, Any]:
        value = self.values.get("strategy", {})
        if value is None:
            return {}
        table = _table(value, "strategy")
        params = table.get("params", {})
        if not isinstance(params, Mapping):
            raise LaunchConfigError("strategy.params must be a TOML table")
        return dict(params)

    @property
    def execution(self) -> Mapping[str, Any]:
        return _optional_table(self.values.get("execution"), "execution")

    def plan(self) -> LaunchPlan:
        self.require_valid()
        mode = self.mode
        mode_config = dict(_optional_table(self.values.get(mode), mode))
        execution = dict(self.execution)
        if mode in {"backtest", "paper"}:
            execution.setdefault("dry_run", True)
        else:
            execution.setdefault("dry_run", False)
        backtest_market: Mapping[str, Any] | None = None
        backtest_data_root: Path | None = None
        backtest_storage_format: str | None = None
        paper_events: Path | None = None
        live_safety: Mapping[str, Any] | None = None
        live_private_sync: Mapping[str, Any] | None = None
        if mode == "backtest":
            backtest_market = dict(_table(mode_config.get("market"), "backtest.market"))
            raw_data_root = mode_config.get("data_root", ".kairos/data")
            backtest_data_root = _resolve_path(raw_data_root, self.root, "backtest.data_root")
            backtest_storage_format = str(mode_config.get("storage_format", "parquet"))
        elif mode == "paper":
            raw_events = mode_config.get("events")
            if raw_events is not None:
                paper_events = _resolve_path(raw_events, self.root, "paper.events")
        else:
            live = mode_config
            safety = _optional_table(live.get("safety"), "live.safety")
            live_safety = {
                "trading_enabled": safety.get("trading_enabled", False),
                "require_limit_orders": safety.get("require_limit_orders", True),
                **({"max_order_notional": safety["max_order_notional"]} if "max_order_notional" in safety else {}),
            }
            live_private_sync = dict(_optional_table(live.get("private_sync"), "live.private_sync"))
            live_private_sync.setdefault("enabled", bool(self.account_refs))
        return LaunchPlan(
            launch_id=self.launch_id,
            mode=mode,
            strategy_ref=self.strategy or "",
            strategy_params=dict(self.strategy_params),
            account_refs=self.account_refs,
            execution=execution,
            mode_config=mode_config,
            backtest_market=backtest_market,
            backtest_data_root=backtest_data_root,
            backtest_storage_format=backtest_storage_format,
            paper_events=paper_events,
            live_safety=live_safety,
            live_private_sync=live_private_sync,
        )

    @property
    def normalized(self) -> dict[str, Any]:
        return self.plan().normalized() | {"source": str(self.path)}

    def report(self) -> LaunchConfigReport:
        issues: list[str] = []
        try:
            launch = self.launch
        except LaunchConfigError as error:
            return LaunchConfigReport(self.path, False, (str(error),))
        try:
            mode = _text(launch.get("mode"), "launch.mode")
        except LaunchConfigError as error:
            issues.append(str(error))
            mode = ""
        if mode not in VALID_MODES:
            issues.append("launch.mode must be one of: backtest, paper, live")
        try:
            launch_id = _text(launch.get("id", self.path.stem), "launch.id")
            if launch_id == SYSTEM_LAUNCH_ID:
                issues.append(f"launch.id {SYSTEM_LAUNCH_ID!r} is reserved")
        except LaunchConfigError as error:
            issues.append(str(error))
        strategy = launch.get("strategy")
        if strategy is None:
            issues.append("launch.strategy is required")
        elif not isinstance(strategy, str) or not strategy.strip() or ":" not in strategy:
            issues.append("launch.strategy must be a module:callable reference")
        for name in ("account", "execution", "strategy"):
            value = self.values.get(name)
            if value is not None and not isinstance(value, Mapping):
                issues.append(f"[{name}] must be a table")
        accounts = self.values.get("accounts")
        if accounts is not None:
            if not isinstance(accounts, Mapping):
                issues.append("[accounts] must be a table")
            else:
                for alias, value in accounts.items():
                    if not isinstance(value, Mapping):
                        issues.append(f"accounts.{alias} must be a table")
                    elif "ref" not in value:
                        issues.append(f"accounts.{alias}.ref is required")
        try:
            account_refs = self.account_refs
        except LaunchConfigError as error:
            issues.append(str(error))
            account_refs = ()
        if mode in {"paper", "live"} and not account_refs:
            issues.append(f"{mode} launch requires [account].ref or [accounts.<alias>].ref")
        if mode == "live":
            live = self.values.get("live")
            if not isinstance(live, Mapping):
                issues.append("[live] table is required for live launches")
            else:
                safety = live.get("safety")
                if safety is not None and not isinstance(safety, Mapping):
                    issues.append("live.safety must be a table")
                if isinstance(safety, Mapping) and "trading_enabled" in safety and not isinstance(safety["trading_enabled"], bool):
                    issues.append("live.safety.trading_enabled must be a boolean")
                if isinstance(safety, Mapping) and "require_limit_orders" in safety and not isinstance(safety["require_limit_orders"], bool):
                    issues.append("live.safety.require_limit_orders must be a boolean")
                if isinstance(safety, Mapping) and "max_order_notional" in safety:
                    try:
                        if Decimal(str(safety["max_order_notional"])) <= 0:
                            issues.append("live.safety.max_order_notional must be positive")
                    except Exception:
                        issues.append("live.safety.max_order_notional must be decimal-compatible")
            account = _optional_table(self.values.get("account"), "account")
            if account.get("environment") is not None and account.get("environment") not in {"live", "testnet"}:
                issues.append("account.environment must be live or testnet for live launches")
        if mode == "backtest":
            backtest = self.values.get("backtest")
            if not isinstance(backtest, Mapping):
                issues.append("[backtest] table is required for backtest launches")
            else:
                market = backtest.get("market")
                if not isinstance(market, Mapping):
                    issues.append("[backtest.market] table is required for backtest launches")
                else:
                    for key in ("start", "end"):
                        if market.get(key) is None:
                            issues.append(f"backtest.market.{key} is required")
                if backtest.get("storage_format", "parquet") not in {"parquet", "jsonl"}:
                    issues.append("backtest.storage_format must be parquet or jsonl")
        if mode == "paper":
            account = _optional_table(self.values.get("account"), "account")
            if account.get("environment") is not None and account.get("environment") not in {"paper", "sandbox", "simulation", "testnet"}:
                issues.append("account.environment must be paper-compatible")
        execution = self.values.get("execution")
        if isinstance(execution, Mapping) and "dry_run" in execution and not isinstance(execution["dry_run"], bool):
            issues.append("execution.dry_run must be a boolean")
        for forbidden in ("broker", "credentials", "data"):
            if forbidden in self.values:
                issues.append(f"[{forbidden}] is not valid launch config; use workspace configuration")
        return LaunchConfigReport(self.path, not issues, tuple(issues))

    def require_valid(self) -> None:
        report = self.report()
        if not report.valid:
            raise LaunchConfigError("; ".join(report.issues))

    def explain(self) -> dict[str, Any]:
        report = self.report()
        return {
            "path": str(self.path),
            "root": str(self.root),
            "valid": report.valid,
            "issues": list(report.issues),
            "launch": dict(self.launch) if "launch" in self.values else {},
            "account_refs": list(self.account_refs),
            "normalized": self.normalized,
        }


@dataclass(frozen=True, slots=True)
class LaunchEnvironment:
    config: LaunchConfig
    launch_id: str
    mode: str
    instance_id: str
    group_directory: Path
    instance_directory: Path
    normalized_config_path: Path

    @property
    def process_environment(self) -> dict[str, str]:
        """Stable launch identity passed to child processes."""
        plan = self.config.plan()
        safety = plan.live_safety or {}
        return {
            "KAIROS_LAUNCH_ID": self.launch_id,
            "KAIROS_LAUNCH_MODE": self.mode,
            "KAIROS_LAUNCH_INSTANCE_ID": self.instance_id,
            "KAIROS_LAUNCH_CONFIG": str(self.config.path),
            "KAIROS_LAUNCH_DIRECTORY": str(self.instance_directory),
            "KAIROS_LAUNCH_GROUP_DIRECTORY": str(self.group_directory),
            "KAIROS_LAUNCH_NORMALIZED_CONFIG": str(self.normalized_config_path),
            "KAIROS_EXECUTION_DRY_RUN": str(bool(plan.execution.get("dry_run", False))).lower(),
            "KAIROS_ACCOUNT_REFS": json.dumps(list(plan.account_refs), separators=(",", ":")),
            "KAIROS_LIVE_TRADING_ENABLED": str(bool(safety.get("trading_enabled", False))).lower(),
            "KAIROS_LIVE_REQUIRE_LIMIT_ORDERS": str(bool(safety.get("require_limit_orders", True))).lower(),
            **({"KAIROS_LIVE_MAX_ORDER_NOTIONAL": str(safety["max_order_notional"])} if "max_order_notional" in safety else {}),
        }

    @classmethod
    def create(cls, config: LaunchConfig, *, workspace_root: str | Path, instance_id: str = "default") -> "LaunchEnvironment":
        config.require_valid()
        root = Path(workspace_root).expanduser().resolve()
        if not instance_id.strip():
            raise LaunchConfigError("launch instance id is required")
        group = root / "launches" / config.mode / config.launch_id
        instance = group / "instances" / instance_id
        normalized_path = instance / "normalized-config.json"
        instance.mkdir(parents=True, exist_ok=True)
        normalized_path.write_text(json.dumps(config.plan().normalized(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return cls(config, config.launch_id, config.mode, instance_id, group, instance, normalized_path)


class LaunchConfigurationApplication:
    """Public use cases for loading and resolving launch configuration."""

    def load(self, path: str | Path, *, workspace_root: str | Path | None = None) -> LaunchConfig:
        return LaunchConfig.load(path, root=workspace_root)

    def validate(self, path: str | Path, *, workspace_root: str | Path | None = None) -> dict[str, Any]:
        report = self.load(path, workspace_root=workspace_root).report()
        return {"path": str(report.path), "valid": report.valid, "issues": list(report.issues)}

    def explain(self, path: str | Path, *, workspace_root: str | Path | None = None) -> dict[str, Any]:
        config = self.load(path, workspace_root=workspace_root)
        value = config.explain()
        if config.report().valid:
            value["plan"] = config.plan().normalized()
        return value

    def plan(self, path: str | Path, *, workspace_root: str | Path | None = None) -> LaunchPlan:
        return self.load(path, workspace_root=workspace_root).plan()

    def environment(self, path: str | Path, *, workspace_root: str | Path, instance_id: str = "default") -> LaunchEnvironment:
        config = self.load(path, workspace_root=workspace_root)
        return LaunchEnvironment.create(config, workspace_root=workspace_root, instance_id=instance_id)


def _table(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LaunchConfigError(f"[{name}] must be a table")
    return value


def _optional_table(value: object, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _table(value, name)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LaunchConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _resolve_path(value: object, root: Path, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise LaunchConfigError(f"{name} must be a non-empty path")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "LaunchConfig",
    "LaunchConfigError",
    "LaunchConfigReport",
    "LaunchConfigurationApplication",
    "LaunchEnvironment",
    "LaunchPlan",
    "VALID_MODES",
]

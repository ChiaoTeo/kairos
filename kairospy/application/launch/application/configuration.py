"""Launch-owned TOML configuration use cases.

The launch configuration describes one run.  Account definitions and
credentials remain workspace-owned; launch files only reference them.
"""

from __future__ import annotations

import json
import hashlib
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, cast

from ...agent import AgentLaunchConfig
from ...data import DatasetSetRef
from ...workspace import ResourceScopePaths
from .semantics import OptionBacktestConstraints


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
    required_account_segments: Mapping[str, tuple[str, ...]]
    execution: Mapping[str, Any]
    capital: Mapping[str, Any]
    mode_config: Mapping[str, Any]
    market_scope: str
    market_profile: str | None
    backtest_market: Mapping[str, Any] | None = None
    backtest_data_root: Path | None = None
    backtest_storage_format: str | None = None
    backtest_replay_file: Path | None = None
    backtest_dataset_set: DatasetSetRef | None = None
    backtest_seed: int | None = None
    backtest_start_time_unix_nanos: int | None = None
    backtest_end_time_unix_nanos: int | None = None
    risk_profile: str | None = None
    paper_events: Path | None = None
    live_safety: Mapping[str, Any] | None = None
    live_private_sync: Mapping[str, Any] | None = None
    notifications: Mapping[str, Any] | None = None
    agent: Mapping[str, Any] | None = None
    agent_profile: Mapping[str, Any] | None = None
    agent_mcp: Mapping[str, Any] | None = None

    def normalized(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _jsonable(
                {
                    "launch": {
                        "id": self.launch_id,
                        "mode": self.mode,
                        "strategy": self.strategy_ref,
                    },
                    "strategy": {"params": dict(self.strategy_params)},
                    "accounts": list(self.account_refs),
                    "account_requirements": {
                        account_id: {"required_segments": list(segments)}
                        for account_id, segments in self.required_account_segments.items()
                    },
                    "execution": dict(self.execution),
                    "capital": dict(self.capital),
                    self.mode: dict(self.mode_config),
                    "market_scope": self.market_scope,
                    "market_profile": self.market_profile,
                    "backtest_market": self.backtest_market,
                    "backtest_data_root": self.backtest_data_root,
                    "backtest_storage_format": self.backtest_storage_format,
                    "backtest_replay_file": self.backtest_replay_file,
                    "backtest_dataset_set": (
                        self.backtest_dataset_set.as_dict()
                        if self.backtest_dataset_set is not None
                        else None
                    ),
                    "backtest_seed": self.backtest_seed,
                    "backtest_start_time_unix_nanos": self.backtest_start_time_unix_nanos,
                    "backtest_end_time_unix_nanos": self.backtest_end_time_unix_nanos,
                    "risk_profile": self.risk_profile,
                    "paper_events": self.paper_events,
                    "live_safety": self.live_safety,
                    "live_private_sync": self.live_private_sync,
                    "notifications": self.notifications,
                    "agent": self.agent,
                    "agent_profile": self.agent_profile,
                    "agent_mcp": self.agent_mcp,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class LaunchConfig:
    path: Path
    root: Path
    values: Mapping[str, Any]

    @classmethod
    def load(
        cls, path: str | Path, *, root: str | Path | None = None
    ) -> "LaunchConfig":
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise LaunchConfigError(f"launch config does not exist: {source}")
        try:
            values = tomllib.loads(source.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise LaunchConfigError(
                f"invalid TOML in launch config {source}: {error}"
            ) from error
        if not isinstance(values, dict):
            raise LaunchConfigError(
                f"launch config root must be a TOML table: {source}"
            )
        return cls(
            source,
            Path(root).expanduser().resolve() if root is not None else source.parent,
            values,
        )

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, Any],
        *,
        root: str | Path,
        source_name: str = "programmatic-launch",
    ) -> "LaunchConfig":
        """Build the same canonical config model used by TOML adapters."""
        resolved_root = Path(root).expanduser().resolve()
        if not source_name.strip() or "/" in source_name or "\\" in source_name:
            raise LaunchConfigError("programmatic launch source name must be path-safe")
        return cls(
            path=resolved_root / f"{source_name}.programmatic",
            root=resolved_root,
            values=dict(values),
        )

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
        account = _optional_table(self.values.get("account"), "account")
        if self.account_ref is not None and account.get("enabled", True):
            refs.append(self.account_ref)
        accounts = self.values.get("accounts")
        if isinstance(accounts, Mapping):
            for alias, value in accounts.items():
                if (
                    not isinstance(value, Mapping)
                    or "ref" not in value
                    or not value.get("enabled", True)
                ):
                    continue
                refs.append(_text(value["ref"], f"accounts.{alias}.ref"))
        return tuple(dict.fromkeys(refs))

    @property
    def required_account_segments(self) -> Mapping[str, tuple[str, ...]]:
        requirements: dict[str, list[str]] = {}

        def include(account_id: str, value: object, name: str) -> None:
            if value is None:
                return
            if not isinstance(value, list) or any(
                not isinstance(segment, str) or not segment.strip() for segment in value
            ):
                raise LaunchConfigError(f"{name} must be an array of segment names")
            selected = requirements.setdefault(account_id, [])
            selected.extend(segment.strip() for segment in value)

        account = _optional_table(self.values.get("account"), "account")
        if self.account_ref is not None and account.get("enabled", True):
            include(
                self.account_ref,
                account.get("required_segments"),
                "account.required_segments",
            )
        accounts = self.values.get("accounts")
        if isinstance(accounts, Mapping):
            for alias, value in accounts.items():
                if (
                    not isinstance(value, Mapping)
                    or "ref" not in value
                    or not value.get("enabled", True)
                ):
                    continue
                account_id = _text(value["ref"], f"accounts.{alias}.ref")
                include(
                    account_id,
                    value.get("required_segments"),
                    f"accounts.{alias}.required_segments",
                )
        return {
            account_id: tuple(dict.fromkeys(segments))
            for account_id, segments in requirements.items()
        }

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

    @property
    def notifications(self) -> Mapping[str, Any]:
        return _optional_table(self.values.get("notifications"), "notifications")

    @property
    def agent(self) -> Mapping[str, Any]:
        return _optional_table(self.values.get("agent"), "agent")

    def plan(self) -> LaunchPlan:
        self.require_valid()
        mode = self.mode
        mode_config = dict(_optional_table(self.values.get(mode), mode))
        market_config = _optional_table(mode_config.get("market"), f"{mode}.market")
        market_scope = str(
            market_config.get("scope", "shared" if mode == "live" else "instance")
        )
        raw_market_profile = market_config.get("profile")
        market_profile = (
            None
            if raw_market_profile is None
            else _text(raw_market_profile, f"{mode}.market.profile")
        )
        execution = dict(self.execution)
        notifications = _normalized_notifications(self.notifications)
        agent = AgentLaunchConfig.from_mapping(
            self.agent,
            launch_mode=mode,
        ).normalized()
        agent_profile = _agent_profile_snapshot(agent)
        agent_mcp = _agent_mcp_snapshot(agent)
        if (
            mode in {"backtest", "paper"}
            and execution.get("enabled", True)
            and not execution.get("routes")
        ):
            account_ids = self.account_refs or ("main",)
            execution["routes"] = [
                {
                    "route_id": f"{account_id}-simulated-spot",
                    "account_id": account_id,
                    "segment_key": "spot",
                    "broker_id": "simulated",
                    "execution_channel": "spot",
                }
                for account_id in account_ids
            ]
        if mode in {"backtest", "paper"}:
            execution.setdefault("dry_run", True)
        else:
            execution.setdefault("dry_run", False)
        backtest_market: Mapping[str, Any] | None = None
        backtest_data_root: Path | None = None
        backtest_storage_format: str | None = None
        backtest_replay_file: Path | None = None
        backtest_dataset_set: DatasetSetRef | None = None
        backtest_seed: int | None = None
        backtest_start_time_unix_nanos: int | None = None
        backtest_end_time_unix_nanos: int | None = None
        risk_profile: str | None = None
        paper_events: Path | None = None
        live_safety: Mapping[str, Any] | None = None
        live_private_sync: Mapping[str, Any] | None = None
        if mode == "backtest":
            backtest_market = dict(_table(mode_config.get("market"), "backtest.market"))
            backtest_start_time_unix_nanos = _time_nanos(
                backtest_market.get("start"), "backtest.market.start"
            )
            backtest_end_time_unix_nanos = _time_nanos(
                backtest_market.get("end"), "backtest.market.end"
            )
            if backtest_start_time_unix_nanos > backtest_end_time_unix_nanos:
                raise LaunchConfigError("backtest market start must not be after end")
            raw_data_root = mode_config.get("data_root", ".kairos/data")
            backtest_data_root = _resolve_path(
                raw_data_root, self.root, "backtest.data_root"
            )
            backtest_storage_format = str(mode_config.get("storage_format", "parquet"))
            raw_replay = backtest_market.get("events")
            if raw_replay is not None:
                backtest_replay_file = _resolve_path(
                    raw_replay, self.root, "backtest.market.events"
                )
            raw_dataset_set = mode_config.get("data")
            if raw_dataset_set is not None:
                if not isinstance(raw_dataset_set, Mapping):
                    raise LaunchConfigError("backtest.data must be a dataset set table")
                try:
                    backtest_dataset_set = DatasetSetRef.from_dict(raw_dataset_set)
                except (TypeError, ValueError) as error:
                    raise LaunchConfigError(
                        f"invalid backtest.data: {error}"
                    ) from error
            raw_seed = mode_config.get("seed")
            if raw_seed is not None:
                if not isinstance(raw_seed, int) or isinstance(raw_seed, bool):
                    raise LaunchConfigError("backtest.seed must be an integer")
                backtest_seed = raw_seed
            mode_config["market"] = dict(backtest_market)
            if backtest_dataset_set is not None:
                mode_config["data"] = backtest_dataset_set.as_dict()
            if backtest_seed is not None:
                mode_config["seed"] = backtest_seed
            raw_option_constraints = mode_config.get("option_constraints")
            if raw_option_constraints is not None:
                if not isinstance(raw_option_constraints, Mapping):
                    raise LaunchConfigError(
                        "backtest.option_constraints must be a table"
                    )
                try:
                    option_constraints = OptionBacktestConstraints.from_mapping(
                        raw_option_constraints
                    )
                except (TypeError, ValueError) as error:
                    raise LaunchConfigError(
                        f"invalid backtest.option_constraints: {error}"
                    ) from error
                mode_config["option_constraints"] = option_constraints.as_dict()
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
                **(
                    {"max_order_notional": safety["max_order_notional"]}
                    if "max_order_notional" in safety
                    else {}
                ),
            }
            live_private_sync = dict(
                _optional_table(live.get("private_sync"), "live.private_sync")
            )
            live_private_sync.setdefault("enabled", bool(self.account_refs))
        risk = _optional_table(self.values.get("risk"), "risk")
        capital = dict(_optional_table(self.values.get("capital"), "capital"))
        raw_risk_profile = risk.get("profile")
        if raw_risk_profile is not None:
            risk_profile = _text(raw_risk_profile, "risk.profile")
        return LaunchPlan(
            launch_id=self.launch_id,
            mode=mode,
            strategy_ref=self.strategy or "",
            strategy_params=dict(self.strategy_params),
            account_refs=self.account_refs,
            required_account_segments=self.required_account_segments,
            execution=execution,
            capital=capital,
            mode_config=mode_config,
            market_scope=market_scope,
            market_profile=market_profile,
            backtest_market=backtest_market,
            backtest_data_root=backtest_data_root,
            backtest_storage_format=backtest_storage_format,
            backtest_replay_file=backtest_replay_file,
            backtest_dataset_set=backtest_dataset_set,
            backtest_seed=backtest_seed,
            backtest_start_time_unix_nanos=backtest_start_time_unix_nanos,
            backtest_end_time_unix_nanos=backtest_end_time_unix_nanos,
            risk_profile=risk_profile,
            paper_events=paper_events,
            live_safety=live_safety,
            live_private_sync=live_private_sync,
            notifications=notifications,
            agent=agent,
            agent_profile=agent_profile,
            agent_mcp=agent_mcp,
        )

    @property
    def normalized(self) -> dict[str, Any]:
        return self.plan().normalized() | {"source": str(self.path)}

    @property
    def normalized_hash(self) -> str:
        """Content identity of the canonical, source-independent launch plan."""

        payload = json.dumps(
            self.plan().normalized(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

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
        elif (
            not isinstance(strategy, str) or not strategy.strip() or ":" not in strategy
        ):
            issues.append("launch.strategy must be a module:callable reference")
        for name in (
            "account",
            "execution",
            "strategy",
            "risk",
            "capital",
            "notifications",
            "agent",
        ):
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
            self.required_account_segments
        except LaunchConfigError as error:
            issues.append(str(error))
            account_refs = ()
        if mode == "live":
            live = self.values.get("live")
            if not isinstance(live, Mapping):
                issues.append("[live] table is required for live launches")
            else:
                safety = live.get("safety")
                if safety is not None and not isinstance(safety, Mapping):
                    issues.append("live.safety must be a table")
                if (
                    isinstance(safety, Mapping)
                    and "trading_enabled" in safety
                    and not isinstance(safety["trading_enabled"], bool)
                ):
                    issues.append("live.safety.trading_enabled must be a boolean")
                if (
                    isinstance(safety, Mapping)
                    and "require_limit_orders" in safety
                    and not isinstance(safety["require_limit_orders"], bool)
                ):
                    issues.append("live.safety.require_limit_orders must be a boolean")
                if isinstance(safety, Mapping) and "max_order_notional" in safety:
                    try:
                        if Decimal(str(safety["max_order_notional"])) <= 0:
                            issues.append(
                                "live.safety.max_order_notional must be positive"
                            )
                    except Exception:
                        issues.append(
                            "live.safety.max_order_notional must be decimal-compatible"
                        )
                execution = self.values.get("execution")
                if isinstance(execution, Mapping) and execution.get("enabled", True):
                    if not isinstance(safety, Mapping) or not safety.get(
                        "trading_enabled", False
                    ):
                        issues.append(
                            "live.safety.trading_enabled must be explicitly true when Execution is enabled"
                        )
                    if not isinstance(safety, Mapping) or not safety.get(
                        "max_order_notional"
                    ):
                        issues.append(
                            "live.safety.max_order_notional is required when Execution is enabled"
                        )
            account = _optional_table(self.values.get("account"), "account")
            if account.get("environment") is not None and account.get(
                "environment"
            ) not in {"live", "testnet"}:
                issues.append(
                    "account.environment must be live or testnet for live launches"
                )
        if mode == "backtest":
            backtest = self.values.get("backtest")
            if not isinstance(backtest, Mapping):
                issues.append("[backtest] table is required for backtest launches")
            else:
                market = backtest.get("market")
                if not isinstance(market, Mapping):
                    issues.append(
                        "[backtest.market] table is required for backtest launches"
                    )
                else:
                    for key in ("start", "end"):
                        if market.get(key) is None:
                            issues.append(f"backtest.market.{key} is required")
                if backtest.get("storage_format", "parquet") not in {
                    "parquet",
                    "jsonl",
                }:
                    issues.append("backtest.storage_format must be parquet or jsonl")
                raw_seed = backtest.get("seed")
                if raw_seed is not None and (
                    not isinstance(raw_seed, int) or isinstance(raw_seed, bool)
                ):
                    issues.append("backtest.seed must be an integer")
                raw_dataset_set = backtest.get("data")
                if raw_dataset_set is not None:
                    if not isinstance(raw_dataset_set, Mapping):
                        issues.append("backtest.data must be a dataset set table")
                    else:
                        try:
                            DatasetSetRef.from_dict(raw_dataset_set)
                        except (TypeError, ValueError) as error:
                            issues.append(f"invalid backtest.data: {error}")
                raw_option_constraints = backtest.get("option_constraints")
                if raw_option_constraints is not None:
                    if not isinstance(raw_option_constraints, Mapping):
                        issues.append("backtest.option_constraints must be a table")
                    else:
                        try:
                            OptionBacktestConstraints.from_mapping(
                                raw_option_constraints
                            )
                        except (TypeError, ValueError) as error:
                            issues.append(
                                f"invalid backtest.option_constraints: {error}"
                            )
        if mode == "paper":
            account = _optional_table(self.values.get("account"), "account")
            if account.get("environment") is not None and account.get(
                "environment"
            ) not in {"paper", "sandbox", "simulation", "testnet"}:
                issues.append("account.environment must be paper-compatible")
        risk = self.values.get("risk")
        if risk is not None and not isinstance(risk, Mapping):
            issues.append("risk must be a table")
        elif isinstance(risk, Mapping):
            profile = risk.get("profile")
            if profile is not None and (
                not isinstance(profile, str) or not profile.strip()
            ):
                issues.append("risk.profile must be a non-empty string")
            if mode == "live" and profile == "simulation-default":
                issues.append(
                    "risk.profile simulation-default is forbidden for live launches"
                )
        if mode == "live" and (
            not isinstance(risk, Mapping) or not risk.get("profile")
        ):
            issues.append("risk.profile is required for live launches")
        capital = self.values.get("capital")
        if isinstance(capital, Mapping):
            enabled = capital.get("enabled", False)
            if not isinstance(enabled, bool):
                issues.append("capital.enabled must be a boolean")
            if enabled:
                automatic_execution = capital.get("automatic_execution", False)
                if not isinstance(automatic_execution, bool):
                    issues.append("capital.automatic_execution must be a boolean")
                plan_ttl_millis = capital.get("plan_ttl_millis", 30_000)
                if (
                    isinstance(plan_ttl_millis, bool)
                    or not isinstance(plan_ttl_millis, int)
                    or plan_ttl_millis <= 0
                ):
                    issues.append("capital.plan_ttl_millis must be a positive integer")
                for field in ("capital_group_id", "strategy_id"):
                    value = capital.get(field)
                    if not isinstance(value, str) or not value.strip():
                        issues.append(
                            f"capital.{field} is required when Capital is enabled"
                        )
                member_readiness = capital.get("member_readiness", {})
                if not isinstance(member_readiness, Mapping):
                    issues.append("capital.member_readiness must be a table")
                else:
                    known_accounts = set(account_refs)
                    for account_id, role in member_readiness.items():
                        if account_id not in known_accounts:
                            issues.append(
                                "capital.member_readiness references an Account outside "
                                f"the launch: {account_id}"
                            )
                        if role not in {"critical", "optional"}:
                            issues.append(
                                "capital.member_readiness values must be critical or optional"
                            )
                policies = capital.get("policies")
                if not isinstance(policies, list) or not policies:
                    issues.append(
                        "capital.policies requires at least one policy when Capital is enabled"
                    )
                else:
                    for index, policy in enumerate(policies):
                        prefix = f"capital.policies[{index}]"
                        if not isinstance(policy, Mapping):
                            issues.append(f"{prefix} must be a table")
                            continue
                        destination = policy.get("destination")
                        if not isinstance(destination, Mapping):
                            issues.append(f"{prefix}.destination must be a table")
                        else:
                            for field in ("broker", "account_id", "segment", "asset"):
                                value = destination.get(field)
                                if not isinstance(value, str) or not value.strip():
                                    issues.append(
                                        f"{prefix}.destination.{field} is required"
                                    )
                        amounts: dict[str, Decimal] = {}
                        for field in (
                            "minimum",
                            "default_target",
                            "maximum",
                            "stress_buffer",
                            "minimum_movement",
                            "hysteresis",
                        ):
                            raw = policy.get(field, "0")
                            try:
                                amount = Decimal(str(raw))
                            except Exception:
                                issues.append(
                                    f"{prefix}.{field} must be an exact decimal"
                                )
                                continue
                            if not amount.is_finite() or amount < 0:
                                issues.append(f"{prefix}.{field} cannot be negative")
                            amounts[field] = amount
                        if all(
                            field in amounts
                            for field in ("minimum", "default_target", "maximum")
                        ) and not (
                            amounts["minimum"]
                            <= amounts["default_target"]
                            <= amounts["maximum"]
                        ):
                            issues.append(
                                f"{prefix} requires minimum <= default_target <= maximum"
                            )
                        max_age = policy.get("max_fact_age_millis")
                        if not isinstance(max_age, int) or max_age <= 0:
                            issues.append(
                                f"{prefix}.max_fact_age_millis must be a positive integer"
                            )
                routes = capital.get("routes", [])
                if not isinstance(routes, list):
                    issues.append("capital.routes must be an array of tables")
                elif automatic_execution and not routes:
                    issues.append(
                        "capital.routes requires at least one route when automatic execution is enabled"
                    )
                else:
                    for index, route in enumerate(routes):
                        prefix = f"capital.routes[{index}]"
                        if not isinstance(route, Mapping):
                            issues.append(f"{prefix} must be a table")
                            continue
                        for field in ("route_id", "kind", "settlement_class"):
                            value = route.get(field)
                            if not isinstance(value, str) or not value.strip():
                                issues.append(f"{prefix}.{field} is required")
                        kind = route.get("kind")
                        if isinstance(kind, str) and kind not in {
                            "internal_transfer",
                            "account_transfer",
                            "earn_redemption_then_transfer",
                            "earn_subscription",
                        }:
                            issues.append(f"{prefix}.kind is unsupported: {kind}")
                        endpoints: dict[str, Mapping[str, object]] = {}
                        for endpoint in ("source", "destination"):
                            value = route.get(endpoint)
                            if not isinstance(value, Mapping):
                                issues.append(f"{prefix}.{endpoint} must be a table")
                            else:
                                endpoints[endpoint] = value
                                for field in (
                                    "broker",
                                    "account_id",
                                    "segment",
                                    "asset",
                                ):
                                    part = value.get(field)
                                    if not isinstance(part, str) or not part.strip():
                                        issues.append(
                                            f"{prefix}.{endpoint}.{field} is required"
                                        )
                        if "source" in endpoints and "destination" in endpoints:
                            same_endpoint = (
                                endpoints["source"] == endpoints["destination"]
                            )
                            if kind == "earn_subscription" and not same_endpoint:
                                issues.append(
                                    f"{prefix} Earn subscription must remain at one balance location"
                                )
                            elif kind != "earn_subscription" and same_endpoint:
                                issues.append(
                                    f"{prefix} transfer endpoints must differ"
                                )
                        product_id = route.get("earn_product_id")
                        if kind == "earn_subscription":
                            if (
                                not isinstance(product_id, str)
                                or not product_id.strip()
                            ):
                                issues.append(
                                    f"{prefix}.earn_product_id is required for Earn subscription"
                                )
                        elif product_id is not None:
                            issues.append(
                                f"{prefix}.earn_product_id is only valid for Earn subscription"
                            )
                        demand_guard = route.get("demand_guard_millis", 0)
                        if not isinstance(demand_guard, int) or demand_guard < 0:
                            issues.append(
                                f"{prefix}.demand_guard_millis must be a non-negative integer"
                            )
                        allow_unknown_quota = route.get(
                            "allow_unknown_redemption_quota", False
                        )
                        if not isinstance(allow_unknown_quota, bool):
                            issues.append(
                                f"{prefix}.allow_unknown_redemption_quota must be a boolean"
                            )
                        for field in ("per_operation_limit", "daily_limit"):
                            try:
                                amount = Decimal(str(route.get(field)))
                            except Exception:
                                issues.append(
                                    f"{prefix}.{field} must be an exact decimal"
                                )
                                continue
                            if not amount.is_finite() or amount <= 0:
                                issues.append(f"{prefix}.{field} must be positive")
        execution = self.values.get("execution")
        if mode == "live" and not isinstance(execution, Mapping):
            issues.append(
                "execution must be explicitly configured or disabled for live launches"
            )
        if (
            isinstance(execution, Mapping)
            and "enabled" in execution
            and not isinstance(execution["enabled"], bool)
        ):
            issues.append("execution.enabled must be a boolean")
        account = self.values.get("account")
        if (
            isinstance(account, Mapping)
            and "enabled" in account
            and not isinstance(account["enabled"], bool)
        ):
            issues.append("account.enabled must be a boolean")
        if isinstance(accounts, Mapping):
            for alias, value in accounts.items():
                if (
                    isinstance(value, Mapping)
                    and "enabled" in value
                    and not isinstance(value["enabled"], bool)
                ):
                    issues.append(f"accounts.{alias}.enabled must be a boolean")
        if (
            isinstance(execution, Mapping)
            and "dry_run" in execution
            and not isinstance(execution["dry_run"], bool)
        ):
            issues.append("execution.dry_run must be a boolean")
        if (
            isinstance(execution, Mapping)
            and execution.get("enabled", True)
            and not (mode in {"backtest", "paper"} and "routes" not in execution)
        ):
            routes = execution.get("routes")
            if not isinstance(routes, list) or not routes:
                issues.append(
                    "execution.routes must be a non-empty array of route tables"
                )
            else:
                route_ids: set[str] = set()
                for index, route in enumerate(routes):
                    prefix = f"execution.routes[{index}]"
                    if not isinstance(route, Mapping):
                        issues.append(f"{prefix} must be a table")
                        continue
                    for field in (
                        "route_id",
                        "account_id",
                        "segment_key",
                        "broker_id",
                        "execution_channel",
                    ):
                        value = route.get(field)
                        if not isinstance(value, str) or not value.strip():
                            issues.append(f"{prefix}.{field} is required")
                    route_id = route.get("route_id")
                    if isinstance(route_id, str) and route_id.strip():
                        if route_id in route_ids:
                            issues.append(f"duplicate execution route_id: {route_id}")
                        route_ids.add(route_id)
                    route_account_id = route.get("account_id")
                    if (
                        isinstance(route_account_id, str)
                        and self.account_refs
                        and route_account_id not in self.account_refs
                    ):
                        issues.append(
                            f"{prefix}.account_id is not an enabled launch account: "
                            f"{route_account_id}"
                        )
                    for secret in ("api_key", "secret", "api_secret", "passphrase"):
                        if secret in route:
                            issues.append(
                                f"{prefix}.{secret} is forbidden; use credential_id"
                            )
            for obsolete in ("provider", "product"):
                if execution.get(obsolete) is not None:
                    issues.append(
                        f"execution.{obsolete} is obsolete; configure execution.routes"
                    )
        notifications = self.values.get("notifications")
        if isinstance(notifications, Mapping):
            issues.extend(_notification_config_issues(notifications))
        agent = self.values.get("agent")
        if isinstance(agent, Mapping):
            try:
                AgentLaunchConfig.from_mapping(agent, launch_mode=mode)
            except ValueError as error:
                issues.append(str(error))
        mode_config = self.values.get(mode) if mode else None
        if isinstance(mode_config, Mapping) and "market" in mode_config:
            market = mode_config.get("market")
            if not isinstance(market, Mapping):
                issues.append(f"[{mode}.market] must be a table")
            else:
                if market.get(
                    "scope", "shared" if mode == "live" else "instance"
                ) not in {"shared", "instance"}:
                    issues.append(f"{mode}.market.scope must be shared or instance")
                if market.get("profile") is not None and (
                    not isinstance(market.get("profile"), str)
                    or not str(market.get("profile")).strip()
                ):
                    issues.append(f"{mode}.market.profile must be a non-empty string")
                for legacy in ("provider", "credential_id", "connection"):
                    if legacy in market:
                        issues.append(
                            f"{mode}.market.{legacy} is obsolete; select a runtime profile"
                        )
        if mode == "backtest" and isinstance(mode_config, Mapping):
            market = mode_config.get("market")
            if (
                isinstance(market, Mapping)
                and market.get("scope", "instance") == "shared"
            ):
                issues.append("backtest.market.scope must be instance")
        if mode == "paper" and isinstance(mode_config, Mapping):
            if mode_config.get("events") is not None and isinstance(
                mode_config.get("market"), Mapping
            ):
                if mode_config["market"].get("scope", "instance") == "shared":
                    issues.append(
                        "paper.market.scope must be instance when paper.events is configured"
                    )
        for forbidden in ("broker", "credentials", "data"):
            if forbidden in self.values:
                issues.append(
                    f"[{forbidden}] is not valid launch config; use workspace configuration"
                )
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
            "KAIROS_MARKET_SCOPE": plan.market_scope,
            **(
                {"KAIROS_MARKET_RUNTIME_PROFILE": plan.market_profile}
                if plan.market_profile is not None
                else {}
            ),
            "KAIROS_EXECUTION_DRY_RUN": str(
                bool(plan.execution.get("dry_run", False))
            ).lower(),
            "KAIROS_ACCOUNT_REFS": json.dumps(
                list(plan.account_refs), separators=(",", ":")
            ),
            **(
                {"KAIROS_BACKTEST_START": str(plan.backtest_market.get("start"))}
                if self.mode == "backtest"
                and isinstance(plan.backtest_market, Mapping)
                and plan.backtest_market.get("start") is not None
                else {}
            ),
            **(
                {"KAIROS_BACKTEST_END": str(plan.backtest_market.get("end"))}
                if self.mode == "backtest"
                and isinstance(plan.backtest_market, Mapping)
                and plan.backtest_market.get("end") is not None
                else {}
            ),
            "KAIROS_LIVE_TRADING_ENABLED": str(
                bool(safety.get("trading_enabled", False))
            ).lower(),
            "KAIROS_LIVE_REQUIRE_LIMIT_ORDERS": str(
                bool(safety.get("require_limit_orders", True))
            ).lower(),
            **(
                {"KAIROS_LIVE_MAX_ORDER_NOTIONAL": str(safety["max_order_notional"])}
                if "max_order_notional" in safety
                else {}
            ),
        }

    @classmethod
    def create(
        cls,
        config: LaunchConfig,
        *,
        workspace_root: str | Path,
        instance_id: str = "default",
    ) -> "LaunchEnvironment":
        config.require_valid()
        root = Path(workspace_root).expanduser().resolve()
        resource_diagnostics = _workspace_resource_diagnostics(config, root)
        resource_blockers = [
            item for item in resource_diagnostics if item["severity"] == "blocker"
        ]
        if resource_blockers:
            raise LaunchConfigError(
                "; ".join(str(item["reason"]) for item in resource_blockers)
            )
        if not instance_id.strip():
            raise LaunchConfigError("launch instance id is required")
        group = root / "launches" / config.mode / config.launch_id
        instance = group / "instances" / instance_id
        paths = ResourceScopePaths(instance)
        normalized_path = paths.config / "normalized.json"
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        prior_drift: dict[str, Any] | None = None
        if normalized_path.is_file():
            prior_drift = LaunchConfigurationApplication().instance_resource_drift(
                normalized_path, workspace_root=root
            )
            blockers = [
                issue
                for issue in prior_drift["issues"]
                if issue.get("severity") == "blocker"
            ]
            if blockers:
                raise LaunchConfigError(
                    "Instance resource configuration drifted; create a new Instance: "
                    + "; ".join(str(issue["resource"]) for issue in blockers)
                )
        normalized = config.plan().normalized()
        normalized["snapshot_schema_version"] = 1
        normalized["readiness_diagnostics"] = resource_diagnostics
        snapshots = _workspace_resource_snapshots(config, root)
        normalized["resource_snapshots"] = snapshots
        normalized["resource_hashes"] = {
            f"{kind}:{resource_id}": str(snapshot["resource_hash"])
            for kind, resources in snapshots.items()
            if isinstance(resources, Mapping)
            for resource_id, snapshot in resources.items()
            if isinstance(snapshot, Mapping) and snapshot.get("resource_hash")
        }
        if prior_drift and prior_drift["issues"]:
            normalized["replaced_instance_drift"] = prior_drift["issues"]
        notifications = normalized.get("notifications")
        if isinstance(notifications, dict) and notifications.get("enabled", False):
            from kairospy.application.notification.composition import (
                notification_config_hash,
            )
            from kairospy.application.workspace import WorkspaceApplication

            workspace = WorkspaceApplication().open(root)
            notifications["workspace_config_hash"] = notification_config_hash(
                workspace, mode=config.mode
            )
        normalized_path.write_text(
            json.dumps(normalized, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return cls(
            config,
            config.launch_id,
            config.mode,
            instance_id,
            group,
            instance,
            normalized_path,
        )


class LaunchConfigurationApplication:
    """Public use cases for loading and resolving launch configuration."""

    def load(
        self, path: str | Path, *, workspace_root: str | Path | None = None
    ) -> LaunchConfig:
        return LaunchConfig.load(path, root=workspace_root)

    def from_values(
        self,
        values: Mapping[str, Any],
        *,
        workspace_root: str | Path,
        source_name: str = "programmatic-launch",
    ) -> LaunchConfig:
        return LaunchConfig.from_values(
            values, root=workspace_root, source_name=source_name
        )

    def validate(
        self, path: str | Path, *, workspace_root: str | Path | None = None
    ) -> dict[str, Any]:
        config = self.load(path, workspace_root=workspace_root)
        report = config.report()
        issues = list(report.issues)
        diagnostics: list[dict[str, str]] = [
            _structural_diagnostic(issue) for issue in issues
        ]
        if not issues and workspace_root is not None:
            diagnostics.extend(
                _workspace_resource_diagnostics(
                    config, Path(workspace_root).expanduser().resolve()
                )
            )
            issues.extend(
                item["reason"] for item in diagnostics if item["severity"] == "blocker"
            )
        return {
            "path": str(report.path),
            "valid": not issues,
            "issues": issues,
            "warnings": [
                item["reason"] for item in diagnostics if item["severity"] == "warning"
            ],
            "diagnostics": diagnostics,
        }

    def explain(
        self, path: str | Path, *, workspace_root: str | Path | None = None
    ) -> dict[str, Any]:
        config = self.load(path, workspace_root=workspace_root)
        value = config.explain()
        if config.report().valid:
            value["plan"] = config.plan().normalized()
        return value

    def plan(
        self, path: str | Path, *, workspace_root: str | Path | None = None
    ) -> LaunchPlan:
        return self.load(path, workspace_root=workspace_root).plan()

    def environment(
        self,
        path: str | Path,
        *,
        workspace_root: str | Path,
        instance_id: str = "default",
    ) -> LaunchEnvironment:
        config = self.load(path, workspace_root=workspace_root)
        return LaunchEnvironment.create(
            config, workspace_root=workspace_root, instance_id=instance_id
        )

    def environment_config(
        self,
        config: LaunchConfig,
        *,
        workspace_root: str | Path,
        instance_id: str = "default",
    ) -> LaunchEnvironment:
        return LaunchEnvironment.create(
            config, workspace_root=workspace_root, instance_id=instance_id
        )

    def instance_resource_drift(
        self, normalized_path: str | Path, *, workspace_root: str | Path
    ) -> dict[str, Any]:
        path = Path(normalized_path)
        try:
            normalized = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LaunchConfigError(
                f"invalid Instance normalized snapshot: {error}"
            ) from error
        snapshots = normalized.get("resource_snapshots")
        if not isinstance(snapshots, Mapping):
            return {
                "valid": False,
                "issues": [
                    {
                        "resource": "snapshot",
                        "severity": "blocker",
                        "reason": "Instance snapshot has no typed resource hashes",
                    }
                ],
            }
        current = _current_resource_hashes(
            snapshots, Path(workspace_root).expanduser().resolve()
        )
        required = {
            "accounts": True,
            "data_providers": True,
            "models": bool(
                isinstance(normalized.get("agent"), Mapping)
                and normalized["agent"].get("required", False)
            ),
            "notifications": bool(
                isinstance(normalized.get("notifications"), Mapping)
                and normalized["notifications"].get("required", False)
            ),
            "mcp_credentials": False,
        }
        issues: list[dict[str, str]] = []
        for kind, resources in snapshots.items():
            if not isinstance(resources, Mapping):
                continue
            for resource_id, snapshot in resources.items():
                if not isinstance(snapshot, Mapping):
                    continue
                expected = snapshot.get("resource_hash")
                actual = current.get(f"{kind}:{resource_id}")
                if expected != actual:
                    is_required = bool(
                        snapshot.get("required", required.get(str(kind), True))
                    )
                    issues.append(
                        {
                            "resource": f"{kind}:{resource_id}",
                            "severity": "blocker" if is_required else "warning",
                            "reason": "configuration hash changed"
                            if actual
                            else "resource missing",
                        }
                    )
        return {
            "valid": not any(issue["severity"] == "blocker" for issue in issues),
            "issues": issues,
        }

    def draft_path(self, workspace_root: str | Path, launch_id: str) -> Path:
        launch_id = launch_id.strip()
        if not _safe_agent_resource_id(launch_id):
            raise ValueError("launch id must be a path-safe identifier")
        root = Path(workspace_root).expanduser().resolve()
        return root / "config" / "launches" / ".drafts" / f"{launch_id}.toml"

    def save_draft(
        self,
        workspace_root: str | Path,
        launch_id: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .wizard import write_atomic

        path = self.draft_path(workspace_root, launch_id)
        write_atomic(path, values)
        report = self.validate(path, workspace_root=workspace_root)
        return {
            "launch_id": launch_id,
            "status": "ready_draft" if report["valid"] else "draft",
            "path": str(path),
            "ready": bool(report["valid"]),
            "issues": list(report["issues"]),
            "warnings": list(report.get("warnings") or ()),
            "diagnostics": list(report.get("diagnostics") or ()),
        }

    def load_draft(self, workspace_root: str | Path, launch_id: str) -> dict[str, Any]:
        path = self.draft_path(workspace_root, launch_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise LaunchConfigError("Launch draft root must be a TOML table")
        return value

    def list_drafts(self, workspace_root: str | Path) -> list[dict[str, Any]]:
        root = (
            Path(workspace_root).expanduser().resolve()
            / "config"
            / "launches"
            / ".drafts"
        )
        if not root.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in sorted(root.glob("*.toml")):
            report = self.validate(path, workspace_root=workspace_root)
            result.append(
                {
                    "launch_id": path.stem,
                    "status": "ready_draft" if report["valid"] else "draft",
                    "ready": bool(report["valid"]),
                    "issues": list(report["issues"]),
                    "warnings": list(report.get("warnings") or ()),
                    "diagnostics": list(report.get("diagnostics") or ()),
                    "path": str(path),
                }
            )
        return result

    def discard_draft(self, workspace_root: str | Path, launch_id: str) -> None:
        self.draft_path(workspace_root, launch_id).unlink(missing_ok=True)
        self._draft_return_path(workspace_root, launch_id).unlink(missing_ok=True)

    def record_draft_return(
        self,
        workspace_root: str | Path,
        launch_id: str,
        *,
        resource: str,
        step: str,
    ) -> dict[str, str]:
        if resource not in {"accounts", "data", "models", "notifications"}:
            raise ValueError("unsupported Launch resource return target")
        path = self._draft_return_path(workspace_root, launch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"launch_id": launch_id, "resource": resource, "step": step}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        return payload

    def draft_return(
        self, workspace_root: str | Path, launch_id: str
    ) -> dict[str, str] | None:
        path = self._draft_return_path(workspace_root, launch_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return (
            {str(key): str(item) for key, item in value.items()}
            if isinstance(value, Mapping)
            else None
        )

    def clear_draft_return(self, workspace_root: str | Path, launch_id: str) -> None:
        self._draft_return_path(workspace_root, launch_id).unlink(missing_ok=True)

    @staticmethod
    def _draft_return_path(workspace_root: str | Path, launch_id: str) -> Path:
        root = Path(workspace_root).expanduser().resolve()
        return root / "state" / "configuration" / "launch-drafts" / f"{launch_id}.json"


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


def _time_nanos(value: object, name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise LaunchConfigError(f"{name} cannot be negative")
        return value
    if not isinstance(value, str) or not value.strip():
        raise LaunchConfigError(
            f"{name} must be an ISO timestamp or integer nanoseconds"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LaunchConfigError(f"{name} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _agent_profile_snapshot(agent: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if not agent.get("enabled", False):
        return None
    value = agent.get("profile")
    if not isinstance(value, Mapping):
        raise LaunchConfigError("agent.profile must be an inline table")
    snapshot = dict(value)
    snapshot["content_hash"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return snapshot


def _workspace_agent_resource_issues(
    config: LaunchConfig, workspace_root: Path
) -> list[str]:
    try:
        agent = AgentLaunchConfig.from_mapping(config.agent, launch_mode=config.mode)
    except (LaunchConfigError, ValueError) as error:
        return [str(error)]
    if not agent.enabled:
        return []
    issues: list[str] = []
    if agent.model is not None:
        credential_id = agent.model.credential
        if not _safe_agent_resource_id(credential_id):
            issues.append("Agent credential id is invalid")
        else:
            try:
                from kairospy.application.agent import AgentResourceApplication
                from kairospy.application.credential import (
                    CredentialConfigurationApplication,
                )
                from kairospy.application.workspace import WorkspaceApplication

                workspace = WorkspaceApplication().open(workspace_root)
                credential = CredentialConfigurationApplication(workspace).show(
                    credential_id
                )
                if credential.get("provider") != "openai":
                    issues.append("Agent credential provider must be openai")
                elif credential.get("configured") is not True:
                    issues.append(
                        "Agent credential api_key SecretRef is unavailable or requires migration"
                    )
                verification = AgentResourceApplication(workspace).model_verification(
                    credential_id, model=agent.model.model
                )
                if verification.get("verification_status") != "verified":
                    issues.append(
                        "Agent model connection test is missing, failed, or stale"
                    )
            except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
                issues.append(f"Agent credential does not exist: {credential_id}")
    return issues


def _agent_mcp_snapshot(agent: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if not agent.get("enabled", False):
        return None
    selections = agent.get("mcp", [])
    if not isinstance(selections, list):
        raise LaunchConfigError("normalized agent.mcp must be an array")
    if not selections:
        return None
    payload: dict[str, Any] = {"entries": [dict(item) for item in selections]}
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _safe_agent_resource_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(
            character
            in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in value
        )
    )


def _normalized_notifications(value: Mapping[str, Any]) -> dict[str, Any]:
    enabled = value.get("enabled", False)
    required = value.get("required", False)
    routes_value = value.get("routes", {})
    routes = (
        {
            str(route): list(dict.fromkeys(str(item) for item in destinations))
            for route, destinations in routes_value.items()
            if isinstance(destinations, list)
        }
        if isinstance(routes_value, Mapping)
        else {}
    )
    defaults_value = value.get("default_routes", [])
    default_routes = (
        list(dict.fromkeys(str(item) for item in defaults_value))
        if isinstance(defaults_value, list)
        else []
    )
    lifecycle_value = value.get("lifecycle_routes", [])
    lifecycle_routes = (
        list(dict.fromkeys(str(item) for item in lifecycle_value))
        if isinstance(lifecycle_value, list)
        else []
    )
    normalized = {
        "enabled": enabled,
        "required": required,
        "default_routes": default_routes,
        "queue_capacity": value.get("queue_capacity", 256),
        "shutdown_grace_seconds": value.get("shutdown_grace_seconds", 5),
        "routes": routes,
    }
    if "lifecycle_routes" in value:
        normalized["lifecycle_routes"] = lifecycle_routes
    return normalized


def _notification_config_issues(value: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    for name in ("enabled", "required"):
        if name in value and not isinstance(value[name], bool):
            issues.append(f"notifications.{name} must be a boolean")
    enabled = value.get("enabled", False)
    defaults = value.get("default_routes", [])
    lifecycle_routes = value.get("lifecycle_routes", [])
    if not isinstance(defaults, list) or any(
        not isinstance(item, str) or not item.strip() for item in defaults
    ):
        issues.append("notifications.default_routes must be an array of route names")
        defaults = []
    elif len(defaults) != len(set(defaults)):
        issues.append("notifications.default_routes must not contain duplicates")
    if not isinstance(lifecycle_routes, list) or any(
        not isinstance(item, str) or not item.strip() for item in lifecycle_routes
    ):
        issues.append("notifications.lifecycle_routes must be an array of route names")
        lifecycle_routes = []
    elif len(lifecycle_routes) != len(set(lifecycle_routes)):
        issues.append("notifications.lifecycle_routes must not contain duplicates")
    queue_capacity = value.get("queue_capacity", 256)
    if (
        not isinstance(queue_capacity, int)
        or isinstance(queue_capacity, bool)
        or not 1 <= queue_capacity <= 100_000
    ):
        issues.append(
            "notifications.queue_capacity must be an integer from 1 to 100000"
        )
    grace = value.get("shutdown_grace_seconds", 5)
    if (
        not isinstance(grace, (int, float))
        or isinstance(grace, bool)
        or not 0 <= float(grace) <= 300
    ):
        issues.append(
            "notifications.shutdown_grace_seconds must be a number from 0 to 300"
        )
    routes = value.get("routes", {})
    route_names: set[str] = set()
    destination_ids: set[str] = set()
    if not isinstance(routes, Mapping):
        issues.append("notifications.routes must be a table")
    else:
        for route, destinations in routes.items():
            prefix = f"notifications.routes.{route}"
            if not isinstance(route, str) or not route.strip():
                issues.append("notification route names must be non-empty strings")
                continue
            route_names.add(route)
            if not isinstance(destinations, list) or not destinations:
                issues.append(f"{prefix} must be a non-empty array of destination IDs")
            elif any(
                not isinstance(item, str) or not item.strip() for item in destinations
            ):
                issues.append(f"{prefix} must contain non-empty destination IDs")
            elif len(destinations) != len(set(destinations)):
                issues.append(f"{prefix} must not contain duplicate destination IDs")
            else:
                destination_ids.update(destinations)
        if len(destination_ids) > 64:
            issues.append("notifications may reference at most 64 unique destinations")
    for route in defaults if isinstance(defaults, list) else []:
        if isinstance(route, str) and route not in route_names:
            issues.append(
                f"notifications.default_routes references unknown route: {route}"
            )
    for route in lifecycle_routes if isinstance(lifecycle_routes, list) else []:
        if isinstance(route, str) and route not in route_names:
            issues.append(
                f"notifications.lifecycle_routes references unknown route: {route}"
            )
    if enabled and not route_names:
        issues.append("enabled notifications require at least one route")
    if not enabled and route_names:
        issues.append("disabled notifications must not configure delivery routes")
    if not enabled and value.get("required", False):
        issues.append("disabled notifications cannot be required")
    forbidden = {
        "webhook",
        "webhook_url",
        "bot_token",
        "token",
        "signing_secret",
        "secret",
        "api_secret",
    }
    for key in value:
        if str(key).lower() in forbidden:
            issues.append(
                f"notifications.{key} is forbidden; use a Workspace credential"
            )
    return issues


def _workspace_resource_diagnostics(
    config: LaunchConfig, workspace_root: Path
) -> list[dict[str, str]]:
    """Classify owner-specific readiness without turning optional failures into blockers."""

    diagnostics: list[dict[str, str]] = []

    def extend(
        owner: str,
        resource: str,
        reasons: tuple[str, ...],
        *,
        severity: str,
        action: str,
    ) -> None:
        diagnostics.extend(
            {
                "owner": owner,
                "resource": resource,
                "severity": severity,
                "reason": reason,
                "action": action,
            }
            for reason in reasons
        )

    extend(
        "Account",
        "accounts",
        _workspace_account_issues(config, workspace_root),
        severity="blocker",
        action="configure and manually test the referenced Account",
    )
    extend(
        "Reference/Market",
        "data_provider",
        _workspace_data_provider_issues(config, workspace_root),
        severity="blocker",
        action="configure and manually test the selected data connection",
    )
    notifications = config.notifications
    extend(
        "Notification",
        "destinations",
        _workspace_notification_issues(config, workspace_root),
        severity="blocker" if notifications.get("required", False) else "warning",
        action=(
            "test the destination or disable Notification; optional failure degrades to no delivery"
        ),
    )
    agent = config.agent
    extend(
        "Agent",
        "model_connection",
        tuple(_workspace_agent_resource_issues(config, workspace_root)),
        severity="blocker" if agent.get("required", False) else "warning",
        action=(
            "test the model connection or disable Agent; optional failure degrades to no Agent review"
        ),
    )
    for credential_id, required, reason in _workspace_mcp_credential_issues(
        config, workspace_root
    ):
        extend(
            "Agent",
            f"mcp_credential:{credential_id}",
            (reason,),
            severity="blocker" if required else "warning",
            action=(
                "configure the MCP credential SecretRef; optional failure disables that MCP server"
            ),
        )
    return diagnostics


def _structural_diagnostic(reason: str) -> dict[str, str]:
    text = reason.lower()
    if "agent" in text or "mcp" in text:
        owner, resource, action = (
            "Agent/Launch",
            "agent",
            "edit the Launch Agent step or configure and test its model connection",
        )
    elif "notification" in text:
        owner, resource, action = (
            "Notification/Launch",
            "notifications",
            "edit the Launch notification step or test the selected Destination",
        )
    elif "risk" in text or "live.safety" in text:
        owner, resource, action = (
            "Risk/Launch",
            "risk",
            "edit the live risk profile and explicit side-effect bounds",
        )
    elif "account" in text or "execution" in text:
        owner, resource, action = (
            "Account/Execution/Launch",
            "accounts",
            "edit Account selection, segment/trade scope, and Execution routes",
        )
    elif "backtest" in text or "market" in text or "data" in text:
        owner, resource, action = (
            "Market/Launch",
            "market",
            "edit the Launch market/data step",
        )
    else:
        owner, resource, action = (
            "Launch",
            "launch",
            "edit the Launch working draft",
        )
    return {
        "owner": owner,
        "resource": resource,
        "severity": "blocker",
        "reason": reason,
        "action": action,
    }


def _workspace_mcp_credential_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[tuple[str, bool, str], ...]:
    if config.mode == "backtest":
        return ()
    try:
        agent = AgentLaunchConfig.from_mapping(config.agent, launch_mode=config.mode)
    except (LaunchConfigError, ValueError):
        return ()
    if not agent.enabled:
        return ()
    from kairospy.application.credential import CredentialConfigurationApplication
    from kairospy.application.workspace import WorkspaceApplication

    workspace = WorkspaceApplication().open(workspace_root)
    credentials = CredentialConfigurationApplication(workspace)
    result: list[tuple[str, bool, str]] = []
    for server in agent.mcp:
        credential_id = server.get("credential")
        if not isinstance(credential_id, str) or not credential_id:
            continue
        required = bool(server.get("required", False))
        try:
            summary = credentials.show(credential_id)
        except (KeyError, OSError, ValueError):
            result.append(
                (
                    credential_id,
                    required,
                    f"MCP credential does not exist: {credential_id}",
                )
            )
            continue
        if summary.get("configured") is not True:
            result.append(
                (
                    credential_id,
                    required,
                    f"MCP credential SecretRef is unavailable or requires migration: {credential_id}",
                )
            )
    return tuple(result)


def _workspace_notification_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[str, ...]:
    notifications = config.notifications
    if not notifications.get("enabled", False):
        return ()
    from kairospy.application.notification.composition import (
        validate_notification_resources,
    )
    from kairospy.application.notification import NotificationAdminApplication
    from kairospy.application.workspace import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
    except (FileNotFoundError, ValueError) as error:
        return (f"cannot validate notification resources: {error}",)
    issues = list(
        validate_notification_resources(
            workspace,
            _normalized_notifications(notifications),
            mode=config.mode,
            resolve_secrets=False,
        )
    )
    if config.mode != "backtest":
        routes = notifications.get("routes", {})
        destination_ids = {
            str(destination_id)
            for destinations in (routes.values() if isinstance(routes, Mapping) else ())
            if isinstance(destinations, list)
            for destination_id in destinations
        }
        admin = NotificationAdminApplication(workspace)
        for destination_id in sorted(destination_ids):
            try:
                destination = admin.show(destination_id)
            except KeyError:
                continue
            if destination.get("verification_status") != "verified":
                issues.append(
                    f"notification destination requires a successful manual test: {destination_id}"
                )
    return tuple(issues)


def _workspace_account_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[str, ...]:
    if config.mode == "backtest" or not config.account_refs:
        return ()
    from kairospy.application.account import AccountConfigurationApplication
    from kairospy.application.workspace import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
    except (FileNotFoundError, ValueError) as error:
        return (f"cannot validate Account resources: {error}",)
    application = AccountConfigurationApplication(workspace)
    issues: list[str] = []
    trade_accounts = {
        str(route.get("account_id"))
        for route in config.execution.get("routes", ())
        if isinstance(route, Mapping) and route.get("account_id")
    }
    for account_id in config.account_refs:
        try:
            account = application.show(account_id)
        except (KeyError, OSError, RuntimeError, ValueError):
            issues.append(f"Account resource does not exist: {account_id}")
            continue
        if account.get("verification_status") != "verified":
            issues.append(
                f"Account requires a successful manual connection test: {account_id}"
            )
            continue
        environment = str(account.get("environment") or "").lower()
        if config.mode == "live" and environment not in {"live", "testnet"}:
            issues.append(
                f"Account environment is not live-compatible: {account_id} "
                f"({environment or 'unknown'})"
            )
        if config.mode == "paper" and environment == "live":
            issues.append(f"Paper Launch cannot select a live Account: {account_id}")
        capabilities = {str(value) for value in account.get("capabilities") or ()}
        if account_id in trade_accounts and "trade" not in capabilities:
            issues.append(
                f"Account manual test did not verify trade permission: {account_id}"
            )
    return tuple(issues)


def _workspace_data_provider_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[str, ...]:
    if config.mode == "backtest":
        return ()
    mode_value = config.values.get(config.mode)
    market = mode_value.get("market") if isinstance(mode_value, Mapping) else None
    profile = market.get("profile") if isinstance(market, Mapping) else None
    if profile is None:
        return ()
    if profile != "massive":
        return (f"Workspace data connection is not supported or verified: {profile}",)
    from kairospy.application.reference import ReferenceProviderConfigurationApplication
    from kairospy.application.workspace import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
        connection = ReferenceProviderConfigurationApplication(workspace).show(
            "massive"
        )
    except (KeyError, FileNotFoundError, OSError, ValueError) as error:
        return (f"Massive data connection is unavailable: {error}",)
    if connection.get("verification_status") != "verified":
        return ("Massive data connection requires a successful manual read test",)
    return ()


def _workspace_resource_snapshots(
    config: LaunchConfig, workspace_root: Path
) -> dict[str, dict[str, Any]]:
    """Resolve secret-free, owner-produced resource snapshots for one Instance."""

    if config.mode == "backtest":
        return {
            "accounts": {},
            "data_providers": {},
            "models": {},
            "notifications": {},
            "mcp_credentials": {},
        }
    from kairospy.application.account import AccountConfigurationApplication
    from kairospy.application.agent import AgentResourceApplication
    from kairospy.application.notification import NotificationAdminApplication
    from kairospy.application.reference import ReferenceProviderConfigurationApplication
    from kairospy.application.workspace import WorkspaceApplication

    workspace = WorkspaceApplication().open(workspace_root)
    accounts = AccountConfigurationApplication(workspace)
    account_snapshots = {
        account_id: accounts.resource_snapshot(account_id)
        for account_id in config.account_refs
    }

    data_snapshots: dict[str, Any] = {}
    mode_value = config.values.get(config.mode)
    market = mode_value.get("market") if isinstance(mode_value, Mapping) else None
    if isinstance(market, Mapping) and market.get("profile") == "massive":
        data_snapshots["massive"] = ReferenceProviderConfigurationApplication(
            workspace
        ).resource_snapshot("massive")

    model_snapshots: dict[str, Any] = {}
    agent = AgentLaunchConfig.from_mapping(config.agent, launch_mode=config.mode)
    if agent.enabled and agent.model is not None:
        try:
            model_snapshots[agent.model.credential] = AgentResourceApplication(
                workspace
            ).resource_snapshot(agent.model.credential, model=agent.model.model)
        except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
            if agent.required:
                raise

    from kairospy.application.credential import CredentialConfigurationApplication

    credential_owner = CredentialConfigurationApplication(workspace)
    mcp_credential_snapshots: dict[str, Any] = {}
    for server in agent.mcp:
        credential_id = server.get("credential")
        if not isinstance(credential_id, str) or not credential_id:
            continue
        required = bool(server.get("required", False))
        try:
            snapshot = credential_owner.resource_snapshot(credential_id)
        except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
            if required:
                raise
            continue
        snapshot["required"] = required
        mcp_credential_snapshots[credential_id] = snapshot

    notification_snapshots: dict[str, Any] = {}
    notifications = config.notifications
    if notifications.get("enabled", False):
        routes = notifications.get("routes", {})
        destination_ids = {
            str(destination_id)
            for destinations in (routes.values() if isinstance(routes, Mapping) else ())
            if isinstance(destinations, list)
            for destination_id in destinations
        }
        notification_owner = NotificationAdminApplication(workspace)
        for destination_id in sorted(destination_ids):
            try:
                notification_snapshots[destination_id] = (
                    notification_owner.resource_snapshot(destination_id)
                )
            except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
                if notifications.get("required", False):
                    raise
    return {
        "accounts": account_snapshots,
        "data_providers": data_snapshots,
        "models": model_snapshots,
        "notifications": notification_snapshots,
        "mcp_credentials": mcp_credential_snapshots,
    }


def _current_resource_hashes(
    snapshots: Mapping[str, Any], workspace_root: Path
) -> dict[str, str]:
    from kairospy.application.account import AccountConfigurationApplication
    from kairospy.application.agent import AgentResourceApplication
    from kairospy.application.notification import NotificationAdminApplication
    from kairospy.application.reference import ReferenceProviderConfigurationApplication
    from kairospy.application.credential import CredentialConfigurationApplication
    from kairospy.application.workspace import WorkspaceApplication

    workspace = WorkspaceApplication().open(workspace_root)
    owners = {
        "accounts": AccountConfigurationApplication(workspace),
        "data_providers": ReferenceProviderConfigurationApplication(workspace),
        "models": AgentResourceApplication(workspace),
        "notifications": NotificationAdminApplication(workspace),
        "mcp_credentials": CredentialConfigurationApplication(workspace),
    }
    result: dict[str, str] = {}
    for kind, resources in snapshots.items():
        if not isinstance(resources, Mapping) or kind not in owners:
            continue
        for resource_id, prior in resources.items():
            if not isinstance(prior, Mapping):
                continue
            try:
                if kind == "accounts":
                    current = owners[kind].resource_snapshot(str(resource_id))
                elif kind == "data_providers":
                    current = owners[kind].resource_snapshot(str(resource_id))
                elif kind == "models":
                    current = owners[kind].resource_snapshot(
                        str(resource_id), model=str(prior.get("model") or "")
                    )
                else:
                    current = owners[kind].resource_snapshot(str(resource_id))
            except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
                continue
            resource_hash = current.get("resource_hash")
            if isinstance(resource_hash, str):
                result[f"{kind}:{resource_id}"] = resource_hash
    return result


__all__ = [
    "LaunchConfig",
    "LaunchConfigError",
    "LaunchConfigReport",
    "LaunchConfigurationApplication",
    "LaunchEnvironment",
    "LaunchPlan",
    "VALID_MODES",
]

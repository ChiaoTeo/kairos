from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
import tomllib


CONFIG_FILENAME = "kairos.toml"
DEFAULT_DATA_ROOT = ".kairos/data"
DEFAULT_REFERENCE_ROOT = ".kairos/reference"
DEFAULT_STORAGE_FORMAT = "parquet"
VALID_STORAGE_FORMATS = frozenset({"parquet", "jsonl"})
VALID_RUN_MODES = frozenset({"backtest", "paper", "live"})
VALID_ACCOUNT_ENVIRONMENTS = frozenset({"backtest", "paper", "live", "sandbox", "simulation", "testnet"})
DEFAULT_ACCOUNT_CASH = Decimal("100000")
DEFAULT_ACCOUNT_CURRENCY = "USD"
DEFAULT_ACCOUNT_FEE_RATE = Decimal("0")


class ConfigError(ValueError):
    """Raised when a Kairos configuration file is invalid."""


@dataclass(frozen=True)
class KairosConfig:
    source_path: Path | None
    root: Path
    values: Mapping[str, Any]

    @property
    def project_name(self) -> str | None:
        value = self._section_value("project", "name")
        return value if isinstance(value, str) and value else None

    @property
    def data_root(self) -> Path:
        value = self._section_value("paths", "lake_root")
        root = value if isinstance(value, str) and value else DEFAULT_DATA_ROOT
        return self.resolve_path(root)

    @property
    def reference_root(self) -> Path:
        value = self._section_value("paths", "reference_root")
        root = value if isinstance(value, str) and value else DEFAULT_REFERENCE_ROOT
        return self.resolve_path(root)

    @property
    def storage_format(self) -> str:
        value = self._section_value("data", "storage_format")
        storage_format = value if isinstance(value, str) and value else DEFAULT_STORAGE_FORMAT
        if storage_format not in VALID_STORAGE_FORMATS:
            raise ValueError(f"unsupported data.storage_format: {storage_format!r}")
        return storage_format

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return self.root / path

    def _section_value(self, section: str, key: str) -> object:
        table = self.values.get(section)
        if not isinstance(table, Mapping):
            return None
        return table.get(key)


@dataclass(frozen=True, slots=True)
class AccountDefaults:
    cash: Decimal = DEFAULT_ACCOUNT_CASH
    currency: str = DEFAULT_ACCOUNT_CURRENCY
    fee_rate: Decimal = DEFAULT_ACCOUNT_FEE_RATE


@dataclass(frozen=True, slots=True)
class AccountConfig:
    account_id: str
    index: int
    venue: str
    cash: Decimal = DEFAULT_ACCOUNT_CASH
    currency: str = DEFAULT_ACCOUNT_CURRENCY
    fee_rate: Decimal = DEFAULT_ACCOUNT_FEE_RATE
    credential: str | None = None


@dataclass(frozen=True, slots=True)
class RunConfigValidationReport:
    path: Path | None
    valid: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunConfig:
    path: Path | None
    root: Path
    values: Mapping[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "RunConfig":
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise ConfigError(f"run config does not exist: {config_path}")
        try:
            values = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"invalid TOML in run config {config_path}: {error}") from error
        if not isinstance(values, Mapping):
            raise ConfigError(f"run config root must be a TOML table: {config_path}")
        return cls(config_path, config_path.parent, values)

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, Any],
        *,
        root: str | Path | None = None,
        path: str | Path | None = None,
    ) -> "RunConfig":
        source_path = Path(path).expanduser().resolve() if path is not None else None
        base = Path(root).expanduser().resolve() if root is not None else (source_path.parent if source_path else Path.cwd())
        return cls(source_path, base, values)

    @property
    def mode(self) -> str:
        raw = self._table("run").get("mode")
        return _text(raw, "run.mode")

    @property
    def run_id(self) -> str:
        run = self._table("run")
        raw = run.get("id")
        if raw is not None:
            return _text(raw, "run.id")
        if self.path is not None:
            return self.path.stem
        return "kairos-run"

    @property
    def strategy(self) -> str | None:
        run = self._table("run")
        raw = run.get("strategy")
        return _optional_text(raw, "run.strategy")

    @property
    def account_environment(self) -> str:
        account = self._table("account", required=False)
        raw = account.get("environment", self.mode)
        return _text(raw, "account.environment")

    @property
    def execution_dry_run(self) -> bool | None:
        execution = self._table("execution", required=False)
        if "dry_run" not in execution:
            return None
        value = execution["dry_run"]
        if not isinstance(value, bool):
            raise ConfigError("execution.dry_run must be a boolean")
        return value

    @property
    def account_defaults(self) -> AccountDefaults:
        accounts = self.accounts
        if accounts:
            first = min(accounts.values(), key=lambda account: account.index)
            return AccountDefaults(first.cash, first.currency, first.fee_rate)
        account = self._table("account", required=False)
        return AccountDefaults(
            cash=_decimal(account.get("cash", DEFAULT_ACCOUNT_CASH), "account.cash"),
            currency=_text(account.get("currency", DEFAULT_ACCOUNT_CURRENCY), "account.currency"),
            fee_rate=_decimal(account.get("fee_rate", DEFAULT_ACCOUNT_FEE_RATE), "account.fee_rate"),
        )

    @property
    def accounts(self) -> Mapping[str, AccountConfig]:
        table = self._table("accounts", required=False)
        parsed: dict[str, AccountConfig] = {}
        for fallback_index, (account_id, raw) in enumerate(table.items()):
            if not isinstance(raw, Mapping):
                raise ConfigError(f"accounts.{account_id} must be a table")
            parsed[str(account_id)] = AccountConfig(
                account_id=str(account_id),
                index=_int(raw.get("index", fallback_index), f"accounts.{account_id}.index"),
                venue=_text(raw.get("venue"), f"accounts.{account_id}.venue"),
                cash=_decimal(raw.get("cash", DEFAULT_ACCOUNT_CASH), f"accounts.{account_id}.cash"),
                currency=_text(raw.get("currency", DEFAULT_ACCOUNT_CURRENCY), f"accounts.{account_id}.currency"),
                fee_rate=_decimal(raw.get("fee_rate", DEFAULT_ACCOUNT_FEE_RATE), f"accounts.{account_id}.fee_rate"),
                credential=_optional_text(raw.get("credential"), f"accounts.{account_id}.credential") or None,
            )
        return dict(sorted(parsed.items(), key=lambda item: item[1].index))

    def validate(self) -> list[str]:
        return list(self.validation_report().issues)

    def validation_report(self) -> RunConfigValidationReport:
        issues: list[str] = []
        run = self.values.get("run")
        if run is None:
            issues.append("[run] table is required")
        elif not isinstance(run, Mapping):
            issues.append("[run] must be a table")
        mode = ""
        if isinstance(run, Mapping):
            if "id" in run:
                try:
                    _text(run.get("id"), "run.id")
                except ConfigError as error:
                    issues.append(str(error))
            raw_mode = run.get("mode")
            try:
                mode = _text(raw_mode, "run.mode")
            except ConfigError as error:
                issues.append(str(error))
            strategy = run.get("strategy")
            if strategy is not None:
                try:
                    strategy_value = _text(strategy, "run.strategy")
                except ConfigError as error:
                    issues.append(str(error))
                else:
                    if ":" not in strategy_value:
                        issues.append("run.strategy must be module:callable")
        if mode not in VALID_RUN_MODES:
            issues.append("run.mode must be one of: backtest, paper, live")
        if "data" in self.values:
            issues.append("[data] is not valid run config; strategy code declares market data with context.subscribe_market_data")
        accounts = self.values.get("accounts")
        if accounts is not None:
            if not isinstance(accounts, Mapping):
                issues.append("[accounts] must be a table")
            else:
                issues.extend(_accounts_issues(accounts, mode=mode))
        account = self.values.get("account")
        if account is not None:
            if not isinstance(account, Mapping):
                issues.append("[account] must be a table")
            else:
                issues.extend(_account_issues(account, mode=mode))
        elif mode in {"paper", "live"} and not isinstance(accounts, Mapping):
            issues.append("[accounts] table is required for paper/live runs")
        broker = self.values.get("broker")
        execution = self.values.get("execution")
        credentials = self.values.get("credentials")
        live = self.values.get("live")
        if mode == "live":
            if isinstance(accounts, Mapping):
                if not isinstance(live, Mapping):
                    issues.append("[live] table is required for live runs")
                else:
                    issues.extend(_live_issues(live))
            if isinstance(account, Mapping):
                if not isinstance(broker, Mapping):
                    issues.append("[broker] table is required for legacy live runs")
                elif not _valid_optional_text(broker.get("provider")):
                    issues.append("broker.provider is required for legacy live runs")
                if not _valid_optional_text(account.get("id")):
                    issues.append("account.id is required for legacy live runs")
                elif str(account.get("environment", "")).strip() != "live":
                    issues.append("account.environment must be live for live runs")
                if not isinstance(execution, Mapping):
                    issues.append("[execution] table is required for legacy live runs")
                else:
                    issues.extend(_execution_issues(execution, mode=mode))
                if not isinstance(credentials, Mapping) or not credentials:
                    issues.append("[credentials] table is required for legacy live runs")
        elif mode == "paper":
            if isinstance(account, Mapping) and str(account.get("environment", "paper")).strip() not in {"paper"}:
                issues.append("account.environment must be paper-compatible for paper runs")
            if isinstance(execution, Mapping):
                issues.extend(_execution_issues(execution, mode=mode))
        elif broker is not None and not isinstance(broker, Mapping):
            issues.append("[broker] must be a table")
        elif execution is not None and not isinstance(execution, Mapping):
            issues.append("[execution] must be a table")
        elif credentials is not None and not isinstance(credentials, Mapping):
            issues.append("[credentials] must be a table")
        elif live is not None and not isinstance(live, Mapping):
            issues.append("[live] must be a table")
        return RunConfigValidationReport(self.path, not issues, tuple(issues))

    def require_valid(self) -> None:
        issues = self.validate()
        if issues:
            raise ConfigError("; ".join(issues))

    def require_mode(self, expected: str) -> None:
        self.require_valid()
        mode = expected
        if mode not in VALID_RUN_MODES:
            raise ConfigError("expected mode must be one of: backtest, paper, live")
        if self.mode != mode:
            raise ConfigError(f"run config mode is {self.mode!r}, but command requires {mode!r}")

    def explain(self) -> dict[str, object]:
        report = self.validation_report()
        return {
            "path": str(self.path) if self.path is not None else None,
            "root": str(self.root),
            "valid": report.valid,
            "issues": list(report.issues),
            "run": dict(self._table("run", required=False)),
            "mode": self._optional_mode(),
            "strategy": self._optional_strategy(),
            "accounts": {key: account for key, account in self.accounts.items()},
            "account": dict(self._table("account", required=False)),
            "broker": dict(self._table("broker", required=False)),
            "execution": dict(self._table("execution", required=False)),
        }

    def _table(self, name: str, *, required: bool = True) -> Mapping[str, Any]:
        value = self.values.get(name)
        if value is None:
            if required:
                raise ConfigError(f"[{name}] table is required")
            return {}
        if not isinstance(value, Mapping):
            raise ConfigError(f"[{name}] must be a table")
        return value

    def _optional_mode(self) -> str | None:
        run = self._table("run", required=False)
        if "mode" not in run:
            return None
        value = _text(run.get("mode"), "run.mode")
        return value

    def _optional_strategy(self) -> str | None:
        run = self._table("run", required=False)
        if "strategy" not in run:
            return None
        return _optional_text(run.get("strategy"), "run.strategy") or None


def find_config_path(start: str | Path | None = None) -> Path | None:
    project_config = find_project_config(start)
    if project_config is not None:
        return project_config
    user_config = Path.home() / ".kairos" / CONFIG_FILENAME
    return user_config if user_config.exists() else None


def find_project_config(start: str | Path | None = None) -> Path | None:
    current = Path.cwd() if start is None else Path(start)
    current = current.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


def load_config(path: str | Path | None = None) -> KairosConfig:
    source_path = Path(path).expanduser().resolve() if path is not None else find_config_path()
    if source_path is None:
        return KairosConfig(source_path=None, root=Path.cwd(), values={})
    with source_path.open("rb") as file:
        values = tomllib.load(file)
    return KairosConfig(source_path=source_path, root=source_path.parent, values=values)


def load_run_config(path: str | Path) -> RunConfig:
    return RunConfig.load(path)


def _text(value: object, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{source} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, source: str) -> str:
    if value is None:
        return ""
    return _text(value, source)


def _valid_optional_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _decimal(value: object, source: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as error:
        raise ConfigError(f"{source} must be decimal-compatible") from error


def _int(value: object, source: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{source} must be an integer")
    try:
        parsed = int(value)
    except Exception as error:
        raise ConfigError(f"{source} must be an integer") from error
    if str(value).strip() != str(parsed):
        raise ConfigError(f"{source} must be an integer")
    return parsed


def _account_issues(account: Mapping[str, Any], *, mode: str) -> list[str]:
    issues: list[str] = []
    for key in ("cash", "fee_rate"):
        if key in account:
            try:
                value = _decimal(account[key], f"account.{key}")
            except ConfigError as error:
                issues.append(str(error))
            else:
                if value < 0:
                    issues.append(f"account.{key} cannot be negative")
    if "currency" in account and not _valid_optional_text(account.get("currency")):
        issues.append("account.currency must be a non-empty string")
    if "environment" in account:
        environment = account.get("environment")
        if not _valid_optional_text(environment):
            issues.append("account.environment must be a non-empty string")
        else:
            value = str(environment).strip()
            if value not in VALID_ACCOUNT_ENVIRONMENTS:
                issues.append("account.environment must be one of: backtest, paper, live, sandbox, simulation, testnet")
            if mode == "backtest" and value == "live":
                issues.append("account.environment cannot be live for backtest runs")
    return issues


def _accounts_issues(accounts: Mapping[str, Any], *, mode: str) -> list[str]:
    issues: list[str] = []
    indexes: dict[int, str] = {}
    if mode in {"paper", "live"} and not accounts:
        issues.append("[accounts] table must declare at least one account")
    for fallback_index, (account_id, raw) in enumerate(accounts.items()):
        source = f"accounts.{account_id}"
        if not isinstance(raw, Mapping):
            issues.append(f"{source} must be a table")
            continue
        if not str(account_id).strip():
            issues.append("accounts account id cannot be empty")
        try:
            index = _int(raw.get("index", fallback_index), f"{source}.index")
        except ConfigError as error:
            issues.append(str(error))
        else:
            if index < 0:
                issues.append(f"{source}.index cannot be negative")
            elif index in indexes:
                issues.append(f"{source}.index duplicates accounts.{indexes[index]}.index")
            else:
                indexes[index] = str(account_id)
        if not _valid_optional_text(raw.get("venue")):
            issues.append(f"{source}.venue is required")
        for key in ("cash", "fee_rate"):
            if key in raw:
                try:
                    value = _decimal(raw[key], f"{source}.{key}")
                except ConfigError as error:
                    issues.append(str(error))
                else:
                    if value < 0:
                        issues.append(f"{source}.{key} cannot be negative")
        if "currency" in raw and not _valid_optional_text(raw.get("currency")):
            issues.append(f"{source}.currency must be a non-empty string")
        if "credential" in raw and not _valid_optional_text(raw.get("credential")):
            issues.append(f"{source}.credential must be a non-empty string")
        if mode == "live" and not _valid_optional_text(raw.get("credential")):
            issues.append(f"{source}.credential is required for live runs")
    return issues


def _live_issues(live: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in ("venue", "symbol"):
        if not _valid_optional_text(live.get(key)):
            issues.append(f"live.{key} is required")
    if "market" in live and not _valid_optional_text(live.get("market")):
        issues.append("live.market must be a non-empty string")
    if "equity_currency" in live and not _valid_optional_text(live.get("equity_currency")):
        issues.append("live.equity_currency must be a non-empty string")
    if "max_iterations" in live:
        try:
            value = _int(live["max_iterations"], "live.max_iterations")
        except ConfigError as error:
            issues.append(str(error))
        else:
            if value < 1:
                issues.append("live.max_iterations must be positive")
    safety = live.get("safety")
    if safety is not None:
        if not isinstance(safety, Mapping):
            issues.append("live.safety must be a table")
        else:
            for key in ("trading_enabled", "require_limit_orders"):
                if key in safety and not isinstance(safety[key], bool):
                    issues.append(f"live.safety.{key} must be a boolean")
            if "max_order_notional" in safety:
                try:
                    value = _decimal(safety["max_order_notional"], "live.safety.max_order_notional")
                except ConfigError as error:
                    issues.append(str(error))
                else:
                    if value <= 0:
                        issues.append("live.safety.max_order_notional must be positive")
    stream = live.get("account_stream")
    if stream is not None:
        if not isinstance(stream, Mapping):
            issues.append("live.account_stream must be a table")
        else:
            for key in ("max_balance_events", "max_order_events", "max_trade_events"):
                if key in stream:
                    try:
                        value = _int(stream[key], f"live.account_stream.{key}")
                    except ConfigError as error:
                        issues.append(str(error))
                    else:
                        if value < 0:
                            issues.append(f"live.account_stream.{key} cannot be negative")
    for key in ("stream", "balance_params", "order_params"):
        if key in live and not isinstance(live[key], Mapping):
            issues.append(f"live.{key} must be a table")
    return issues


def _execution_issues(execution: Mapping[str, Any], *, mode: str) -> list[str]:
    issues: list[str] = []
    if "dry_run" in execution and not isinstance(execution["dry_run"], bool):
        issues.append("execution.dry_run must be a boolean")
    if mode == "live":
        if not _valid_optional_text(execution.get("driver")):
            issues.append("execution.driver is required for live runs")
        if execution.get("dry_run") is not False:
            issues.append("execution.dry_run must be false for live runs")
    if mode == "paper" and execution.get("dry_run") is False:
        issues.append("execution.dry_run cannot be false for paper runs")
    return issues


__all__ = [
    "CONFIG_FILENAME",
    "AccountConfig",
    "AccountDefaults",
    "ConfigError",
    "DEFAULT_DATA_ROOT",
    "DEFAULT_REFERENCE_ROOT",
    "DEFAULT_STORAGE_FORMAT",
    "KairosConfig",
    "RunConfig",
    "RunConfigValidationReport",
    "VALID_ACCOUNT_ENVIRONMENTS",
    "VALID_RUN_MODES",
    "find_config_path",
    "find_project_config",
    "load_config",
    "load_run_config",
]

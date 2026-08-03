from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONFIG_FILENAME = "kairos.toml"
DEFAULT_DATA_ROOT = ".kairos/data"
DEFAULT_REFERENCE_ROOT = ".kairos/reference"
DEFAULT_STORAGE_FORMAT = "parquet"
VALID_STORAGE_FORMATS = frozenset({"parquet", "jsonl"})
VALID_LAUNCH_MODES = frozenset({"backtest", "paper", "live"})
SYSTEM_LAUNCH_ID = "kairos-system"
RESERVED_LAUNCH_IDS = frozenset({SYSTEM_LAUNCH_ID})
VALID_ACCOUNT_ENVIRONMENTS = frozenset({"backtest", "paper", "live", "sandbox", "simulation", "testnet"})
DEFAULT_ACCOUNT_CASH = Decimal("100000")
DEFAULT_ACCOUNT_CURRENCY = "USD"
DEFAULT_ACCOUNT_FEE_RATE = Decimal("0")
DEFAULT_PROJECT_TIMEZONE = "UTC"
DEFAULT_PROJECT_LANGUAGE = "en"
VALID_PROJECT_LANGUAGES = frozenset({"en", "zh-CN"})


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
    def timezone_name(self) -> str:
        value = self._section_value("project", "timezone")
        name = value if isinstance(value, str) and value.strip() else DEFAULT_PROJECT_TIMEZONE
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unsupported project.timezone: {name!r}") from error
        return name

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def language(self) -> str:
        value = self._section_value("project", "language")
        language = _normalize_language(value if isinstance(value, str) else DEFAULT_PROJECT_LANGUAGE)
        if language not in VALID_PROJECT_LANGUAGES:
            raise ValueError(f"unsupported project.language: {value!r}")
        return language

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
class LaunchAccountConfig:
    alias: str
    ref: str
    index: int
    books: tuple[str, ...] = ()
    trade: bool = True


@dataclass(frozen=True, slots=True)
class LaunchConfigValidationReport:
    path: Path | None
    valid: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LaunchConfig:
    path: Path | None
    root: Path
    values: Mapping[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "LaunchConfig":
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise ConfigError(f"launch config does not exist: {config_path}")
        try:
            values = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"invalid TOML in launch config {config_path}: {error}") from error
        if not isinstance(values, Mapping):
            raise ConfigError(f"launch config root must be a TOML table: {config_path}")
        return cls(config_path, Path.cwd().resolve(), values)

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, Any],
        *,
        root: str | Path | None = None,
        path: str | Path | None = None,
    ) -> "LaunchConfig":
        source_path = Path(path).expanduser().resolve() if path is not None else None
        base = Path(root).expanduser().resolve() if root is not None else Path.cwd().resolve()
        return cls(source_path, base, values)

    def with_launch_strategy(self, strategy: str) -> "LaunchConfig":
        if not isinstance(strategy, str) or not strategy.strip():
            raise ConfigError("launch.strategy must be a non-empty string")
        values = {str(key): value for key, value in self.values.items()}
        launch = values.get("launch")
        if launch is None:
            launch_values: dict[str, Any] = {}
        elif isinstance(launch, Mapping):
            launch_values = dict(launch)
        else:
            raise ConfigError("[launch] must be a table")
        launch_values["strategy"] = strategy.strip()
        values["launch"] = launch_values
        return LaunchConfig(self.path, self.root, values)

    @property
    def mode(self) -> str:
        raw = self._table("launch").get("mode")
        return _text(raw, "launch.mode")

    @property
    def launch_id(self) -> str:
        launch = self._table("launch")
        raw = launch.get("id")
        if raw is not None:
            return _text(raw, "launch.id")
        if self.path is not None:
            return self.path.stem
        return "kairos-launch"

    @property
    def strategy(self) -> str | None:
        launch = self._table("launch")
        raw = launch.get("strategy")
        return _optional_text(raw, "launch.strategy")

    @property
    def account_environment(self) -> str:
        account = self._table("account", required=False)
        raw = account.get("environment", self.mode)
        return _text(raw, "account.environment")

    @property
    def account_ref(self) -> str | None:
        account = self._table("account", required=False)
        raw = account.get("ref")
        return _optional_text(raw, "account.ref") or None

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
            if "ref" in raw:
                continue
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

    @property
    def launch_accounts(self) -> Mapping[str, LaunchAccountConfig]:
        table = self._table("accounts", required=False)
        parsed: dict[str, LaunchAccountConfig] = {}
        for fallback_index, (alias, raw) in enumerate(table.items()):
            if not isinstance(raw, Mapping):
                raise ConfigError(f"accounts.{alias} must be a table")
            if "ref" not in raw:
                continue
            books = raw.get("books")
            parsed[str(alias)] = LaunchAccountConfig(
                alias=str(alias),
                ref=_text(raw.get("ref"), f"accounts.{alias}.ref"),
                index=_int(raw.get("index", fallback_index), f"accounts.{alias}.index"),
                books=_string_tuple(books, f"accounts.{alias}.books"),
                trade=_bool(raw.get("trade", True), f"accounts.{alias}.trade"),
            )
        return dict(sorted(parsed.items(), key=lambda item: item[1].index))

    def validate(self) -> list[str]:
        return list(self.validation_report().issues)

    def validation_report(self) -> LaunchConfigValidationReport:
        issues: list[str] = []
        launch = self.values.get("launch")
        if launch is None:
            issues.append("[launch] table is required")
        elif not isinstance(launch, Mapping):
            issues.append("[launch] must be a table")
        mode = ""
        if isinstance(launch, Mapping):
            launch_id = ""
            if "id" in launch:
                try:
                    launch_id = _text(launch.get("id"), "launch.id")
                except ConfigError as error:
                    issues.append(str(error))
            raw_mode = launch.get("mode")
            try:
                mode = _text(raw_mode, "launch.mode")
            except ConfigError as error:
                issues.append(str(error))
            if launch_id in RESERVED_LAUNCH_IDS and mode != "system":
                issues.append(f"launch.id {launch_id!r} is reserved for the built-in system runtime")
            strategy = launch.get("strategy")
            if strategy is not None:
                try:
                    strategy_value = _text(strategy, "launch.strategy")
                except ConfigError as error:
                    issues.append(str(error))
                else:
                    if ":" not in strategy_value:
                        issues.append("launch.strategy must be module:callable")
        if mode not in VALID_LAUNCH_MODES:
            issues.append("launch.mode must be one of: backtest, paper, live")
        if "data" in self.values:
            issues.append("[data] is not valid launch config; strategy code declares market data with context.subscribe")
        feeds = self.values.get("feeds")
        if feeds is not None:
            if not isinstance(feeds, Mapping):
                issues.append("[feeds] must be a table")
            else:
                issues.extend(_feeds_issues(feeds))
        accounts = self.values.get("accounts")
        if accounts is not None:
            if not isinstance(accounts, Mapping):
                issues.append("[accounts] must be a table")
            elif _looks_like_launch_account_table(accounts):
                issues.extend(_launch_accounts_issues(accounts))
            else:
                issues.append("[accounts] inline account definitions are not valid launch config; configure accounts in .kairos/accounts and reference them with accounts.<alias>.ref")
        account = self.values.get("account")
        if account is not None:
            if not isinstance(account, Mapping):
                issues.append("[account] must be a table")
            else:
                issues.extend(_account_issues(account, mode=mode))
        if mode in {"paper", "live"}:
            has_legacy_account_ref = isinstance(account, Mapping) and _valid_optional_text(account.get("ref"))
            has_launch_accounts = isinstance(accounts, Mapping) and _looks_like_launch_account_table(accounts)
            if not has_legacy_account_ref and not has_launch_accounts:
                issues.append("[account] table with account.ref or [accounts.<alias>] with ref is required for paper/live launches")
        broker = self.values.get("broker")
        execution = self.values.get("execution")
        credentials = self.values.get("credentials")
        live = self.values.get("live")
        if broker is not None:
            issues.append("[broker] is not valid launch config; configure broker/provider via .kairos/accounts")
        if credentials is not None:
            issues.append("[credentials] is not valid launch config; configure credentials via .kairos/accounts")
        if mode == "live":
            if not isinstance(live, Mapping):
                issues.append("[live] table is required for live launches")
            else:
                issues.extend(_live_issues(live))
            if isinstance(execution, Mapping):
                issues.extend(_execution_issues(execution, mode=mode))
        elif mode == "paper":
            if isinstance(account, Mapping) and str(account.get("environment", "paper")).strip() not in {"paper"}:
                issues.append("account.environment must be paper-compatible for paper launches")
            paper = self.values.get("paper")
            if isinstance(paper, Mapping):
                issues.extend(_account_selector_issues(paper, "paper"))
            if isinstance(execution, Mapping):
                issues.extend(_execution_issues(execution, mode=mode))
        elif execution is not None and not isinstance(execution, Mapping):
            issues.append("[execution] must be a table")
        elif live is not None and not isinstance(live, Mapping):
            issues.append("[live] must be a table")
        return LaunchConfigValidationReport(self.path, not issues, tuple(issues))

    def require_valid(self) -> None:
        issues = self.validate()
        if issues:
            raise ConfigError("; ".join(issues))

    def require_mode(self, expected: str) -> None:
        self.require_valid()
        mode = expected
        if mode not in VALID_LAUNCH_MODES:
            raise ConfigError("expected mode must be one of: backtest, paper, live")
        if self.mode != mode:
            raise ConfigError(f"launch config mode is {self.mode!r}, but command requires {mode!r}")

    def explain(self) -> dict[str, object]:
        report = self.validation_report()
        return {
            "path": str(self.path) if self.path is not None else None,
            "root": str(self.root),
            "valid": report.valid,
            "issues": list(report.issues),
            "launch": dict(self._table("launch", required=False)),
            "mode": self._optional_mode(),
            "strategy": self._optional_strategy(),
            "account": dict(self._table("account", required=False)),
            "account_ref": self.account_ref,
            "accounts": {
                key: {"ref": value.ref, "index": value.index, "books": list(value.books), "trade": value.trade}
                for key, value in self.launch_accounts.items()
            },
            "feeds": dict(self._table("feeds", required=False)),
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
        launch = self._table("launch", required=False)
        if "mode" not in launch:
            return None
        value = _text(launch.get("mode"), "launch.mode")
        return value

    def _optional_strategy(self) -> str | None:
        launch = self._table("launch", required=False)
        if "strategy" not in launch:
            return None
        return _optional_text(launch.get("strategy"), "launch.strategy") or None


def find_config_path(start: str | Path | None = None) -> Path | None:
    return find_manifest_path(start)


def find_manifest_path(start: str | Path | None = None) -> Path | None:
    current = Path.cwd() if start is None else Path(start)
    current = current.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / ".kairos" / CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


def find_project_config(start: str | Path | None = None) -> Path | None:
    return find_manifest_path(start)


def load_config(path: str | Path | None = None) -> KairosConfig:
    source_path = Path(path).expanduser().resolve() if path is not None else find_config_path()
    if source_path is None:
        return KairosConfig(source_path=None, root=Path.cwd(), values={})
    with source_path.open("rb") as file:
        values = tomllib.load(file)
    root = source_path.parent.parent if source_path.parent.name == ".kairos" else source_path.parent
    return KairosConfig(source_path=source_path, root=root, values=values)


def load_launch_config(path: str | Path) -> LaunchConfig:
    return LaunchConfig.load(path)


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


def _string_tuple(value: object, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ConfigError(f"{source} must not contain empty book names")
        return (text,)
    if not isinstance(value, list):
        raise ConfigError(f"{source} must be a string or list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{source}[{index}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


def _normalize_language(value: str) -> str:
    text = value.strip().replace("_", "-")
    lowered = text.lower()
    if lowered in {"en", "en-us", "en-gb"}:
        return "en"
    if lowered in {"zh", "zh-cn", "zh-hans", "cn"}:
        return "zh-CN"
    return text


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


def _bool(value: object, source: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{source} must be a boolean")
    return value


def _account_issues(account: Mapping[str, Any], *, mode: str) -> list[str]:
    issues: list[str] = []
    if "ref" in account and not _valid_optional_text(account.get("ref")):
        issues.append("account.ref must be a non-empty string")
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
                issues.append("account.environment cannot be live for backtest launches")
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
            issues.append(f"{source}.credential is required for live launches")
    return issues


def _looks_like_launch_account_table(accounts: Mapping[str, Any]) -> bool:
    return any(isinstance(raw, Mapping) and "ref" in raw for raw in accounts.values())


def _launch_accounts_issues(accounts: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    indexes: dict[int, str] = {}
    if not accounts:
        issues.append("[accounts] table must declare at least one launch account")
    for fallback_index, (alias, raw) in enumerate(accounts.items()):
        source = f"accounts.{alias}"
        if not isinstance(raw, Mapping):
            issues.append(f"{source} must be a table")
            continue
        if not str(alias).strip():
            issues.append("accounts alias cannot be empty")
        if not _valid_optional_text(raw.get("ref")):
            issues.append(f"{source}.ref is required")
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
                indexes[index] = str(alias)
        if "books" in raw:
            try:
                _string_tuple(raw.get("books"), f"{source}.books")
            except ConfigError as error:
                issues.append(str(error))
        if "trade" in raw and not isinstance(raw.get("trade"), bool):
            issues.append(f"{source}.trade must be a boolean")
    return issues


def _feeds_issues(feeds: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    for feed_id, raw in feeds.items():
        source = f"feeds.{feed_id}"
        if not str(feed_id).strip():
            issues.append("feeds id cannot be empty")
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            issues.append(f"{source} must be a table")
            continue
        if "credential" in raw and not _valid_optional_text(raw.get("credential")):
            issues.append(f"{source}.credential must be a non-empty string")
    return issues


def _live_issues(live: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    legacy_fields = ("venue", "market", "symbol", "stream", "order_params", "balance_params", "watch_private")
    for key in legacy_fields:
        if key in live:
            issues.append(f"live.{key} is no longer supported; use account refs, account books, and strategy context.subscribe(...)")
    for key in ("max_balance_events", "max_order_events", "max_trade_events"):
        if key in live:
            issues.append(f"live.{key} is no longer supported; use live.account_stream.{key}")
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
    private_sync = live.get("private_sync")
    if private_sync is not None:
        if not isinstance(private_sync, Mapping):
            issues.append("live.private_sync must be a table")
        else:
            if "enabled" in private_sync and not isinstance(private_sync["enabled"], bool):
                issues.append("live.private_sync.enabled must be a boolean")
            if "mode" in private_sync:
                issues.append("live.private_sync.mode is not supported")
    issues.extend(_account_selector_issues(live, "live"))
    return issues


def _account_selector_issues(values: Mapping[str, Any], section: str) -> list[str]:
    issues: list[str] = []
    if "account" in values:
        value = values["account"]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            issues.append(f"{section}.account must be an account id or integer account index")
        elif isinstance(value, str) and not value.strip():
            issues.append(f"{section}.account must be a non-empty string or integer account index")
        elif isinstance(value, int) and value < 0:
            issues.append(f"{section}.account cannot be negative")
    if "account_id" in values and not _valid_optional_text(values.get("account_id")):
        issues.append(f"{section}.account_id must be a non-empty string")
    if "account_index" in values:
        try:
            value = _int(values["account_index"], f"{section}.account_index")
        except ConfigError as error:
            issues.append(str(error))
        else:
            if value < 0:
                issues.append(f"{section}.account_index cannot be negative")
    return issues


def _execution_issues(execution: Mapping[str, Any], *, mode: str) -> list[str]:
    issues: list[str] = []
    if "dry_run" in execution and not isinstance(execution["dry_run"], bool):
        issues.append("execution.dry_run must be a boolean")
    if mode == "live":
        if not _valid_optional_text(execution.get("driver")):
            issues.append("execution.driver is required for live launches")
        if execution.get("dry_run") is not False:
            issues.append("execution.dry_run must be false for live launches")
    if mode == "paper" and execution.get("dry_run") is False:
        issues.append("execution.dry_run cannot be false for paper launches")
    return issues


__all__ = [
    "CONFIG_FILENAME",
    "AccountConfig",
    "AccountDefaults",
    "ConfigError",
    "DEFAULT_DATA_ROOT",
    "DEFAULT_REFERENCE_ROOT",
    "DEFAULT_STORAGE_FORMAT",
    "DEFAULT_PROJECT_LANGUAGE",
    "DEFAULT_PROJECT_TIMEZONE",
    "KairosConfig",
    "LaunchAccountConfig",
    "LaunchConfig",
    "LaunchConfigValidationReport",
    "RESERVED_LAUNCH_IDS",
    "SYSTEM_LAUNCH_ID",
    "VALID_ACCOUNT_ENVIRONMENTS",
    "VALID_PROJECT_LANGUAGES",
    "VALID_LAUNCH_MODES",
    "find_config_path",
    "find_manifest_path",
    "find_project_config",
    "load_config",
    "load_launch_config",
]

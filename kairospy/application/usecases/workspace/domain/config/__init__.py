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
DEFAULT_PROJECT_TIMEZONE = "UTC"
DEFAULT_PROJECT_LANGUAGE = "en"
VALID_PROJECT_LANGUAGES = frozenset({"en", "zh-CN"})
DEFAULT_ACCOUNT_CASH = Decimal("100000")
DEFAULT_ACCOUNT_CURRENCY = "USD"
DEFAULT_ACCOUNT_FEE_RATE = Decimal("0")


class ConfigError(ValueError):
    """Raised when a Kairos project configuration is invalid."""


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



__all__ = [
    "CONFIG_FILENAME",
    "ConfigError",
    "DEFAULT_DATA_ROOT",
    "DEFAULT_REFERENCE_ROOT",
    "DEFAULT_STORAGE_FORMAT",
    "DEFAULT_PROJECT_LANGUAGE",
    "DEFAULT_PROJECT_TIMEZONE",
    "KairosConfig",
    "VALID_PROJECT_LANGUAGES",
    "VALID_STORAGE_FORMATS",
    "find_config_path",
    "find_manifest_path",
    "find_project_config",
    "load_config",
]

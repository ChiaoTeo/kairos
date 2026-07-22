from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kairospy.integrations.connectors.massive.config import MassiveConfig


CONFIG_FILE_NAME = "kairos.toml"
PROJECT_STATE_DIR = ".kairos"
DEFAULT_LAKE_ROOT = f"{PROJECT_STATE_DIR}/data"


class ConfigError(ValueError):
    """Raised when a Kairos project configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ConfigValue:
    raw: Any
    resolved: Any
    source: str


@dataclass(frozen=True, slots=True)
class BinanceCredentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True, slots=True)
class KairosProjectConfig:
    root: Path
    path: Path
    data: dict[str, Any]

    @classmethod
    def discover(cls, start: str | Path = ".") -> "KairosProjectConfig":
        current = Path(start).expanduser().resolve()
        if current.is_file():
            current = current.parent
        for directory in (current, *current.parents):
            candidate = directory / CONFIG_FILE_NAME
            if candidate.exists():
                return cls.load(candidate)
        raise ConfigError(f"no {CONFIG_FILE_NAME} found from {current}")

    @classmethod
    def load(cls, path: str | Path) -> "KairosProjectConfig":
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise ConfigError(f"configuration file does not exist: {config_path}")
        _load_dotenv(config_path.parent / ".env")
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"configuration root must be a TOML table: {config_path}")
        return cls(config_path.parent, config_path, data)

    def get(self, dotted_path: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in _parts(dotted_path):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def resolve(self, dotted_path: str, default: Any = None) -> ConfigValue:
        raw = self.get(dotted_path, default)
        return _resolve_value(raw, dotted_path)

    def relative_path(self, dotted_path: str, default: str) -> Path:
        value = self.resolve(dotted_path, default).resolved
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else self.root / path

    def massive_config(self) -> MassiveConfig:
        from kairospy.integrations.connectors.massive.config import MassiveConfig

        api_key = self.resolve("providers.massive.api_key").resolved
        if not api_key:
            raise ConfigError(
                "Massive API key is missing. Set MASSIVE_API_KEY or configure providers.massive.api_key."
            )
        timeout = int(self.resolve("providers.massive.timeout_seconds", 30).resolved)
        retries = int(self.resolve("providers.massive.max_retries", 4).resolved)
        return MassiveConfig(str(api_key), timeout_seconds=timeout, max_retries=retries)

    def binance_credentials(self, environment: str = "testnet") -> BinanceCredentials:
        base = f"providers.binance.{environment}"
        key = self.resolve(f"{base}.api_key").resolved
        secret = self.resolve(f"{base}.api_secret").resolved
        if not key or not secret:
            prefix = "BINANCE_TESTNET" if environment == "testnet" else "BINANCE_LIVE"
            raise ConfigError(
                f"Binance {environment} credentials are missing. Set {prefix}_API_KEY/{prefix}_API_SECRET "
                f"or configure {base}.api_key and {base}.api_secret."
            )
        return BinanceCredentials(str(key), str(secret))

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.get("project"), dict):
            issues.append("[project] table is required")
        if not self.get("project.name"):
            issues.append("project.name is required")
        data_root = self.relative_path("data.lake_root", DEFAULT_LAKE_ROOT)
        if not data_root.exists():
            issues.append(f"data.lake_root does not exist: {data_root}")
        for key_path in (
            "providers.massive.api_key",
            "providers.binance.testnet.api_key",
            "providers.binance.testnet.api_secret",
            "providers.binance.live.api_key",
            "providers.binance.live.api_secret",
        ):
            raw = self.get(key_path)
            if isinstance(raw, str) and raw.startswith("env:") and not os.environ.get(raw[4:]):
                issues.append(f"{key_path} references unset environment variable {raw[4:]}")
        return issues

    def to_redacted_dict(self) -> dict[str, Any]:
        return _redact(self.data)


def load_project_config_or_none(start: str | Path = ".") -> KairosProjectConfig | None:
    try:
        return KairosProjectConfig.discover(start)
    except ConfigError:
        return None


def set_config_value(path: str | Path, dotted_path: str, value: str) -> None:
    config_path = Path(path).expanduser().resolve()
    data = KairosProjectConfig.load(config_path).data if config_path.exists() else {}
    target = data
    parts = _parts(dotted_path)
    for part in parts[:-1]:
        existing = target.get(part)
        if existing is None:
            existing = {}
            target[part] = existing
        if not isinstance(existing, dict):
            raise ConfigError(f"cannot set {dotted_path}: {part} is not a table")
        target = existing
    target[parts[-1]] = _parse_scalar(value)
    config_path.write_text(_render_toml(data), encoding="utf-8")


def unset_config_value(path: str | Path, dotted_path: str) -> bool:
    config_path = Path(path).expanduser().resolve()
    config = KairosProjectConfig.load(config_path)
    target = config.data
    parts = _parts(dotted_path)
    for part in parts[:-1]:
        existing = target.get(part)
        if not isinstance(existing, dict):
            return False
        target = existing
    removed = target.pop(parts[-1], None) is not None
    if removed:
        config_path.write_text(_render_toml(config.data), encoding="utf-8")
    return removed


def _parts(dotted_path: str) -> tuple[str, ...]:
    parts = tuple(part for part in dotted_path.split(".") if part)
    if not parts:
        raise ConfigError("configuration path cannot be empty")
    return parts


def _resolve_value(raw: Any, source: str) -> ConfigValue:
    if isinstance(raw, str) and raw.startswith("env:"):
        name = raw[4:]
        return ConfigValue(raw, os.environ.get(name), f"env:{name}")
    return ConfigValue(raw, raw, source)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[len("export "):].lstrip()
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _dotenv_value(value)


def _dotenv_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip()
    return text


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    return value


def _redact(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {item_key: _redact(item_value, key=item_key) for item_key, item_value in value.items()}
    if key.lower() in {"api_key", "api_secret", "secret", "password", "token"}:
        if isinstance(value, str) and value.startswith("env:"):
            return value
        return "***" if value else value
    return value


def _render_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    scalars = {key: value for key, value in data.items() if not isinstance(value, dict)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_format_toml_value(value)}")
    if scalars:
        lines.append("")
    _render_tables(lines, (), {key: value for key, value in data.items() if isinstance(value, dict)})
    return "\n".join(lines).rstrip() + "\n"


def _render_tables(lines: list[str], prefix: tuple[str, ...], tables: dict[str, Any]) -> None:
    for key, value in tables.items():
        table_path = (*prefix, key)
        nested = {child_key: child for child_key, child in value.items() if isinstance(child, dict)}
        scalars = {child_key: child for child_key, child in value.items() if not isinstance(child, dict)}
        if scalars:
            lines.append(f"[{'.'.join(table_path)}]")
            for child_key, child in scalars.items():
                lines.append(f"{child_key} = {_format_toml_value(child)}")
            lines.append("")
        if nested:
            _render_tables(lines, table_path, nested)


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

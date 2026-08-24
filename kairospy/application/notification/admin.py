from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Literal, Mapping

from kairospy.application.credential import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.workspace import Workspace

from .services.setup import TelegramBotIdentity, TelegramChat, TelegramSetupClient


NotificationProvider = Literal["feishu", "telegram"]
SecretSource = Literal["env", "file"]
NotificationSecretRef = SecretRef


@dataclass(frozen=True, slots=True)
class NotificationAdminApplication:
    """Workspace-level notification destination and credential use cases."""

    workspace: Workspace

    def list(self) -> list[dict[str, object]]:
        return [
            self._summary(destination_id, record)
            for destination_id, record in sorted(self._load_destinations().items())
        ]

    def show(self, destination_id: str) -> dict[str, object]:
        destination_id = _safe_id(destination_id, "destination_id")
        record = self._load_destinations().get(destination_id)
        if record is None:
            raise KeyError(f"notification destination does not exist: {destination_id}")
        return self._summary(destination_id, record)

    def configure(
        self,
        destination_id: str,
        *,
        provider: NotificationProvider,
        credential_id: str | None = None,
        secret_ref: NotificationSecretRef,
        chat_id: str | None = None,
    ) -> dict[str, object]:
        destination_id = _safe_id(destination_id, "destination_id")
        credential_id = _safe_id(credential_id or destination_id, "credential_id")
        if provider not in {"feishu", "telegram"}:
            raise ValueError(f"unsupported notification provider: {provider}")
        normalized_chat_id: str | None = None
        if provider == "telegram":
            normalized_chat_id = _telegram_chat_id(chat_id)

        secret = self.resolve_secret(secret_ref)
        if secret is not None:
            _validate_provider_secret(provider, secret)

        credential_path = self._credential_path(credential_id)
        credential = self._load_credential(credential_path)
        configured_provider = str(credential.get("provider", "")).strip().lower()
        if configured_provider and configured_provider != provider:
            raise ValueError(
                f"credential {credential_id} belongs to provider {configured_provider!r}"
            )
        field = "webhook_url" if provider == "feishu" else "bot_token"
        destinations = self._load_destinations()
        record: dict[str, object] = {
            "sender": provider,
            "credential_id": credential_id,
            "enabled": True,
        }
        if normalized_chat_id is not None:
            record["chat_id"] = normalized_chat_id
        destinations[destination_id] = record
        previous_credential = (
            credential_path.read_text(encoding="utf-8")
            if credential_path.is_file()
            else None
        )
        try:
            # The credential is committed first so a concurrently starting
            # process never observes a Destination whose credential is absent.
            CredentialConfigurationApplication(self.workspace).configure(
                credential_id,
                provider=provider,
                role="notification-send",
                fields={field: secret_ref},
                overwrite=True,
            )
            self._write_destinations(destinations)
        except BaseException:
            if previous_credential is None:
                credential_path.unlink(missing_ok=True)
            else:
                _atomic_write(credential_path, previous_credential, mode=0o600)
            raise
        return self.show(destination_id)

    def set_enabled(self, destination_id: str, enabled: bool) -> dict[str, object]:
        destination_id = _safe_id(destination_id, "destination_id")
        destinations = self._load_destinations()
        record = destinations.get(destination_id)
        if record is None:
            raise KeyError(f"notification destination does not exist: {destination_id}")
        record["enabled"] = enabled
        self._write_destinations(destinations)
        return self.show(destination_id)

    def delete(self, destination_id: str) -> dict[str, object]:
        destination_id = _safe_id(destination_id, "destination_id")
        destinations = self._load_destinations()
        record = destinations.pop(destination_id, None)
        if record is None:
            raise KeyError(f"notification destination does not exist: {destination_id}")
        self._write_destinations(destinations)
        self._verification_path(destination_id).unlink(missing_ok=True)
        return {"destination_id": destination_id, "status": "deleted"}

    def record_test(
        self, destination_id: str, *, succeeded: bool, detail: str | None = None
    ) -> dict[str, object]:
        """Record user-triggered delivery evidence without provider payloads."""

        destination = self.show(destination_id)
        evidence = {
            "version": 1,
            "destination_id": destination_id,
            "config_hash": _destination_config_hash(destination),
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "succeeded": succeeded,
            "detail": "delivery accepted" if succeeded else "delivery failed",
            "tested": ["credential authentication", "real provider delivery"],
            "not_tested": ["Launch route selection", "retry and failure policy"],
            "capabilities": ["send"] if succeeded else [],
        }
        del detail
        _atomic_write(
            self._verification_path(destination_id),
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        return self.show(destination_id)

    def resource_snapshot(self, destination_id: str) -> dict[str, object]:
        destination = self.show(destination_id)
        return {
            key: destination.get(key)
            for key in (
                "destination_id",
                "provider",
                "enabled",
                "credential_id",
                "secret_ref",
                "chat_id",
                "verification_status",
                "last_tested_at",
            )
        } | {"resource_hash": _destination_config_hash(destination)}

    def default_secret_environment(self, credential_id: str, provider: str) -> str:
        credential_id = _safe_id(credential_id, "credential_id")
        field = "WEBHOOK_URL" if provider == "feishu" else "BOT_TOKEN"
        prefix = re.sub(r"[^A-Za-z0-9]", "_", credential_id).upper()
        return f"KAIROS_CREDENTIAL_{prefix}_{field}"

    def resolve_secret(self, reference: NotificationSecretRef) -> str | None:
        return CredentialConfigurationApplication(self.workspace).resolve(reference)

    def probe_telegram(self, credential_id: str) -> TelegramBotIdentity:
        secret = self._resolved_credential_secret(credential_id, "telegram")
        return TelegramSetupClient(secret).identity()

    def probe_telegram_reference(
        self, reference: NotificationSecretRef
    ) -> TelegramBotIdentity:
        secret = self.resolve_secret(reference)
        if secret is None:
            raise ValueError(
                f"Telegram SecretRef is unavailable: {reference.source}:{reference.id}"
            )
        _validate_provider_secret("telegram", secret)
        return TelegramSetupClient(secret).identity()

    def discover_telegram_chats(self, credential_id: str) -> tuple[TelegramChat, ...]:
        secret = self._resolved_credential_secret(credential_id, "telegram")
        return TelegramSetupClient(secret).chats()

    def discover_telegram_chats_from_reference(
        self, reference: NotificationSecretRef
    ) -> tuple[TelegramChat, ...]:
        secret = self.resolve_secret(reference)
        if secret is None:
            return ()
        _validate_provider_secret("telegram", secret)
        return TelegramSetupClient(secret).chats()

    def _summary(
        self, destination_id: str, record: Mapping[str, object]
    ) -> dict[str, object]:
        provider = str(record.get("sender", "")).strip().lower()
        credential_id = str(record.get("credential_id", "")).strip()
        enabled = record.get("enabled", True) is True
        credential = self._load_credential(self._credential_path(credential_id))
        reference = _credential_secret_ref(credential, provider)
        try:
            secret_available = bool(
                self._resolved_credential_secret(credential_id, provider)
            )
        except ValueError:
            secret_available = False
        result: dict[str, object] = {
            "destination_id": destination_id,
            "provider": provider,
            "enabled": enabled,
            "credential_id": credential_id,
            "secret_ref": (
                {"source": reference.source, "id": reference.id}
                if reference is not None
                else None
            ),
            "secret_available": secret_available,
        }
        if provider == "telegram":
            result["chat_id"] = str(record.get("chat_id", ""))
        result["configured"] = bool(
            enabled
            and provider in {"feishu", "telegram"}
            and credential_id
            and credential.get("provider") == provider
            and secret_available
            and (provider != "telegram" or result.get("chat_id"))
        )
        result.update(self._verification_summary(destination_id, result))
        return result

    def _verification_summary(
        self, destination_id: str, destination: Mapping[str, object]
    ) -> dict[str, object]:
        path = self._verification_path(destination_id)
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested_configuration_hash": None,
                "current_configuration_hash": _destination_config_hash(destination),
                "tested": [],
                "not_tested": [],
                "capabilities": [],
            }
        if not isinstance(evidence, Mapping):
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested_configuration_hash": None,
                "current_configuration_hash": _destination_config_hash(destination),
                "tested": [],
                "not_tested": [],
                "capabilities": [],
            }
        current_hash = _destination_config_hash(destination)
        if evidence.get("config_hash") != current_hash:
            status = "retest_required"
        elif evidence.get("succeeded") is True:
            status = "verified"
        else:
            status = "failed"
        return {
            "verification_status": status,
            "last_tested_at": evidence.get("tested_at"),
            "last_test_detail": evidence.get("detail"),
            "tested_configuration_hash": evidence.get("config_hash"),
            "current_configuration_hash": current_hash,
            "tested": list(evidence.get("tested") or ()),
            "not_tested": list(evidence.get("not_tested") or ()),
            "capabilities": list(evidence.get("capabilities") or ()),
        }

    def _resolved_credential_secret(self, credential_id: str, provider: str) -> str:
        credential_id = _safe_id(credential_id, "credential_id")
        credential = self._load_credential(self._credential_path(credential_id))
        if str(credential.get("provider", "")).lower() != provider:
            raise ValueError(
                f"notification credential {credential_id} is not configured for {provider}"
            )
        reference = _credential_secret_ref(credential, provider)
        if reference is None:
            # Backward-compatible literal credentials remain readable but are never
            # produced by the administrative application.
            aliases = (
                ("webhook_url", "api_key")
                if provider == "feishu"
                else ("bot_token", "api_key")
            )
            for field in aliases:
                value = credential.get(field)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            derived = self.default_secret_environment(credential_id, provider)
            value = os.environ.get(derived, "").strip()
            if value:
                _validate_provider_secret(provider, value)
                return value
            raise ValueError(
                f"notification credential {credential_id} has no SecretRef"
            )
        value = self.resolve_secret(reference)
        if value is None:
            raise ValueError(
                f"notification credential {credential_id} SecretRef is unavailable: "
                f"{reference.source}:{reference.id}"
            )
        _validate_provider_secret(provider, value)
        return value

    def _load_destinations(self) -> dict[str, dict[str, object]]:
        path = self.workspace.paths.notification_config()
        if not path.is_file():
            return {}
        try:
            value = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"invalid notification config {path}: {error}") from error
        if value.get("version", 1) != 1:
            raise ValueError("unsupported notification config version")
        records = value.get("destinations", {})
        if not isinstance(records, Mapping):
            raise ValueError("notification config requires [destinations]")
        return {
            str(destination_id): dict(record)
            for destination_id, record in records.items()
            if isinstance(record, Mapping)
        }

    def _load_credential(self, path: Path) -> dict[str, object]:
        if not path.is_file():
            return {}
        try:
            value = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ValueError(
                f"invalid notification credential {path}: {error}"
            ) from error
        credential = value.get("credential", value)
        if not isinstance(credential, Mapping):
            raise ValueError(f"notification credential must be a TOML table: {path}")
        return dict(credential)

    def _credential_path(self, credential_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", credential_id) or "unnamed"
        return self.workspace.paths.credential_config().parent / f"{safe}.toml"

    def _verification_path(self, destination_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "notifications", f"{destination_id}.json"
        )

    def _write_destinations(self, records: Mapping[str, Mapping[str, object]]) -> None:
        lines = ["version = 1", "", "[destinations]"]
        for destination_id, record in sorted(records.items()):
            lines.extend(("", f"[destinations.{json.dumps(destination_id)}]"))
            for key in ("sender", "credential_id", "enabled", "chat_id"):
                if key in record:
                    lines.append(f"{key} = {_toml_scalar(record[key])}")
            for key, value in sorted(record.items()):
                if key not in {"sender", "credential_id", "enabled", "chat_id"}:
                    lines.append(f"{key} = {_toml_scalar(value)}")
        _atomic_write(
            self.workspace.paths.notification_config(), "\n".join(lines) + "\n"
        )

    def _write_credential(self, path: Path, credential: Mapping[str, object]) -> None:
        secrets = credential.get("secrets", {})
        lines = ["[credential]"]
        for key in ("id", "provider", "role"):
            if key in credential:
                lines.append(f"{key} = {_toml_scalar(credential[key])}")
        for key, value in sorted(credential.items()):
            if key not in {"id", "provider", "role", "secrets"}:
                lines.append(f"{key} = {_toml_scalar(value)}")
        if isinstance(secrets, Mapping):
            for field, raw_reference in sorted(secrets.items()):
                if not isinstance(raw_reference, Mapping):
                    continue
                lines.extend(("", f"[credential.secrets.{field}]"))
                lines.append(
                    f"source = {_toml_scalar(raw_reference.get('source', ''))}"
                )
                lines.append(f"id = {_toml_scalar(raw_reference.get('id', ''))}")
        _atomic_write(path, "\n".join(lines) + "\n", mode=0o600)


def _safe_id(value: str, name: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", normalized):
        raise ValueError(
            f"{name} must start with a letter or number and contain only letters, "
            "numbers, underscores, or hyphens"
        )
    return normalized


def _telegram_chat_id(value: str | None) -> str:
    normalized = (value or "").strip()
    if not (
        re.fullmatch(r"-?[0-9]{1,32}", normalized)
        or re.fullmatch(r"[A-Za-z_-][A-Za-z0-9_-]+", normalized)
    ):
        raise ValueError(
            "Telegram chat_id must be a numeric chat id or channel username"
        )
    return normalized


def _credential_secret_ref(
    credential: Mapping[str, object], provider: str
) -> NotificationSecretRef | None:
    secrets = credential.get("fields", credential.get("secrets"))
    if not isinstance(secrets, Mapping):
        return None
    field = "webhook_url" if provider == "feishu" else "bot_token"
    raw = secrets.get(field)
    if not isinstance(raw, Mapping):
        return None
    source = str(raw.get("source", ""))
    identifier = str(raw.get("id", ""))
    if source not in {"env", "file"}:
        raise ValueError(f"unsupported notification SecretRef source: {source}")
    return NotificationSecretRef(source, identifier)  # type: ignore[arg-type]


def _validate_provider_secret(provider: str, value: str) -> None:
    if provider == "feishu":
        from .services.senders import _feishu_webhook_token

        _feishu_webhook_token(value)
        return
    if not re.fullmatch(r"(?:bot)?[0-9]+:[A-Za-z0-9_-]+", value):
        raise ValueError("Telegram bot token has an invalid format")


def _destination_config_hash(destination: Mapping[str, object]) -> str:
    payload = {
        key: destination.get(key)
        for key in (
            "destination_id",
            "provider",
            "enabled",
            "credential_id",
            "secret_ref",
            "chat_id",
        )
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if mode is not None:
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if mode is not None:
            path.chmod(mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "NotificationAdminApplication",
    "NotificationProvider",
    "NotificationSecretRef",
    "SecretSource",
]

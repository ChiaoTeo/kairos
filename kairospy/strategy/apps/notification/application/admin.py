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

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import (
    Workspace,
    WorkspaceConfigurationTransaction,
)

from ..services import AppriseSender, NotificationDeliveryRuntime
from ..services.setup import TelegramBotIdentity, TelegramChat, TelegramSetupClient
from .application import NotificationApplication
from .models import NotificationDestination


NotificationProvider = Literal["feishu", "telegram"]


@dataclass(frozen=True, slots=True)
class PreparedNotificationDestination:
    workspace: Workspace
    destination: Mapping[str, object]
    document: str

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        transaction.stage_text(
            self.workspace.paths.notification_config(), self.document
        )


@dataclass(frozen=True, slots=True)
class NotificationAdminApplication:
    """Workspace-level notification destination and credential use cases."""

    workspace: Workspace

    def validate_workspace(self, *, mode: str = "paper") -> dict[str, object]:
        from ..composition import validate_workspace_notifications

        return validate_workspace_notifications(self.workspace, mode=mode)

    async def test_destination(self, destination_id: str) -> dict[str, object]:
        destination_id = _safe_id(destination_id, "destination_id")
        configured = self.show(destination_id)
        if not configured.get("enabled", False):
            raise ValueError(f"notification destination is disabled: {destination_id}")

        provider = str(configured.get("provider", ""))
        credential_id = str(configured.get("credential_id", ""))
        secret = self._resolved_credential_secret(credential_id, provider)
        settings = (
            {"chat_id": str(configured.get("chat_id", ""))}
            if provider == "telegram"
            else {}
        )
        destination = NotificationDestination(
            destination_id,
            provider,  # type: ignore[arg-type]
            credential_id=credential_id,
            settings=settings,
            secrets={"webhook_url" if provider == "feishu" else "bot_token": secret},
        )
        runtime = NotificationDeliveryRuntime(
            identity={
                "workspace_id": self.workspace.identity.workspace_id,
                "launch_id": "notification-test",
                "instance_id": "cli",
                "strategy_id": "notification-test",
                "mode": "paper",
            },
            routes={"test": (destination_id,)},
            default_routes=("test",),
            destinations={destination_id: destination},
            senders={destination_id: AppriseSender(destination)},
            queue_capacity=1,
            shutdown_grace_seconds=10,
            journal_path=self.workspace.instance(
                "paper", "notification-test", "cli"
            ).log("notification", "delivery.jsonl"),
        )
        application = NotificationApplication(runtime)
        await runtime.start()
        try:
            sent_at = datetime.now(timezone.utc).isoformat()
            receipt = application.publish(
                title="Kairos 测试通知",
                body=(
                    f"Kairos 测试 · Workspace {self.workspace.identity.workspace_id} · "
                    f"发送时间 {sent_at}"
                ),
                routes=("test",),
            )
            await runtime.flush(timeout=10)
            return {
                "notification_id": receipt.notification_id,
                "publish_status": receipt.status,
                "destination_id": destination_id,
                "health": application.health(),
            }
        finally:
            await runtime.close()

    def config_hash(self, *, mode: str = "paper") -> str:
        from ..composition import notification_config_hash

        return notification_config_hash(self.workspace, mode=mode)

    def validate_resources(
        self,
        config: Mapping[str, object],
        *,
        mode: str,
        resolve_secrets: bool = False,
    ) -> tuple[str, ...]:
        from ..composition import validate_notification_resources

        return validate_notification_resources(
            self.workspace,
            config,
            mode=mode,
            resolve_secrets=resolve_secrets,
        )

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
        secret: str,
        chat_id: str | None = None,
    ) -> dict[str, object]:
        prepared = self.prepare(
            destination_id,
            provider=provider,
            credential_id=credential_id,
            chat_id=chat_id,
        )
        field = "webhook_url" if provider == "feishu" else "bot_token"
        _validate_provider_secret(provider, secret)
        credential = CredentialConfigurationApplication(self.workspace).prepare(
            str(prepared.destination["credential_id"]),
            provider=provider,
            role="notification-send",
            values={field: secret},
        )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"notification:{destination_id}"
        )
        credential.stage(transaction)
        prepared.stage(transaction)
        transaction.commit()
        return self.show(destination_id)

    def prepare(
        self,
        destination_id: str,
        *,
        provider: NotificationProvider,
        credential_id: str | None = None,
        chat_id: str | None = None,
    ) -> PreparedNotificationDestination:
        """Validate and render a Destination without changing active config."""

        destination_id = _safe_id(destination_id, "destination_id")
        credential_id = _safe_id(credential_id or destination_id, "credential_id")
        if provider not in {"feishu", "telegram"}:
            raise ValueError(f"unsupported notification provider: {provider}")
        normalized_chat_id: str | None = None
        if provider == "telegram":
            normalized_chat_id = _telegram_chat_id(chat_id)

        credential_path = self._credential_path(credential_id)
        credential = self._load_credential(credential_path)
        configured_provider = str(credential.get("provider", "")).strip().lower()
        if configured_provider and configured_provider != provider:
            raise ValueError(
                f"credential {credential_id} belongs to provider {configured_provider!r}"
            )
        destinations = self._load_destinations()
        record: dict[str, object] = {
            "sender": provider,
            "credential_id": credential_id,
            "enabled": True,
        }
        if normalized_chat_id is not None:
            record["chat_id"] = normalized_chat_id
        destinations[destination_id] = record
        return PreparedNotificationDestination(
            self.workspace,
            {
                "destination_id": destination_id,
                "provider": provider,
                "credential_id": credential_id,
                "enabled": True,
                "chat_id": normalized_chat_id,
                "configured": True,
            },
            _destinations_document(destinations),
        )

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
                "chat_id",
                "verification_status",
                "last_tested_at",
            )
        } | {"resource_hash": _destination_config_hash(destination)}

    def probe_telegram(self, credential_id: str) -> TelegramBotIdentity:
        secret = self._resolved_credential_secret(credential_id, "telegram")
        return TelegramSetupClient(secret).identity()

    def probe_telegram_secret(self, secret: str) -> TelegramBotIdentity:
        """Probe a staged token without persisting it first."""

        _validate_provider_secret("telegram", secret)
        return TelegramSetupClient(secret).identity()

    def discover_telegram_chats(self, credential_id: str) -> tuple[TelegramChat, ...]:
        secret = self._resolved_credential_secret(credential_id, "telegram")
        return TelegramSetupClient(secret).chats()

    def discover_telegram_chats_from_secret(
        self, secret: str
    ) -> tuple[TelegramChat, ...]:
        """Discover chats with a staged token that has not been saved."""

        _validate_provider_secret("telegram", secret)
        return TelegramSetupClient(secret).chats()

    def _summary(
        self, destination_id: str, record: Mapping[str, object]
    ) -> dict[str, object]:
        provider = str(record.get("sender", "")).strip().lower()
        credential_id = str(record.get("credential_id", "")).strip()
        enabled = record.get("enabled", True) is True
        credential = self._load_credential(self._credential_path(credential_id))
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
        field = "webhook_url" if provider == "feishu" else "bot_token"
        value = CredentialConfigurationApplication(self.workspace).resolve_field(
            credential_id, field
        )
        if value is None:
            raise ValueError(f"notification credential {credential_id} has no {field}")
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
        return self.workspace.paths.credentials_root() / f"{safe}.toml"

    def _verification_path(self, destination_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "notifications", f"{destination_id}.json"
        )

    def _write_destinations(self, records: Mapping[str, Mapping[str, object]]) -> None:
        _atomic_write(
            self.workspace.paths.notification_config(),
            _destinations_document(records),
        )


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


def _validate_provider_secret(provider: str, value: str) -> None:
    if provider == "feishu":
        from ..services.senders import _feishu_webhook_token

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


def _destinations_document(
    records: Mapping[str, Mapping[str, object]],
) -> str:
    lines = ["version = 1", "", "[destinations]"]
    for destination_id, record in sorted(records.items()):
        lines.extend(("", f"[destinations.{json.dumps(destination_id)}]"))
        for key in ("sender", "credential_id", "enabled", "chat_id"):
            if key in record:
                lines.append(f"{key} = {_toml_scalar(record[key])}")
        for key, value in sorted(record.items()):
            if key not in {"sender", "credential_id", "enabled", "chat_id"}:
                lines.append(f"{key} = {_toml_scalar(value)}")
    return "\n".join(lines) + "\n"


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
    "PreparedNotificationDestination",
    "NotificationProvider",
]

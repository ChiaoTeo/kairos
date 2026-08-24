"""Account-facing application models and workspace-backed commands.

Account state is owned by the Rust Account application. Account configuration,
credentials, and launch leases are persisted by the Workspace layer.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
from uuid import uuid4
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.system.apps.workspace.application import Workspace
from .application import AccountApplication
from .errors import (
    AccountLookupError,
    AccountNotEnabledError,
    AccountSegmentNotFoundError,
    BalanceNotFoundError,
    PositionNotFoundError,
)
from .events import (
    AccountEvent,
    AccountStatusChangedEvent,
    BalanceChangedEvent,
    EarnHoldingChangedEvent,
    EquityChangedEvent,
    ObservedOrderChangedEvent,
    PositionChangedEvent,
)
from .models import (
    COIN_M_FUTURES,
    CROSS_MARGIN,
    EQUITY,
    FUNDING,
    ISOLATED_MARGIN,
    OPTIONS,
    SPOT,
    USD_M_FUTURES,
    AccountSegmentSnapshot,
    AccountSnapshot,
    AccountsSnapshot,
    AccountStatusChange,
    Balance,
    DataFreshness,
    EarnHolding,
    EarnHoldingState,
    EarnLiquidity,
    EquityChange,
    ObservedOrder,
    Position,
    PositionSide,
    SegmentCompleteness,
    SegmentSyncLifecycle,
    SegmentSyncMode,
)


def _text(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _credential_environment_names(
    credential_id: str, fields: tuple[str, ...]
) -> dict[str, str]:
    prefix = "KAIROS_CREDENTIAL_" + "".join(
        character if character.isalnum() else "_" for character in credential_id.upper()
    )
    return {field.upper(): f"{prefix}_{field.upper()}" for field in fields}


def _cli(workspace: Workspace) -> "AccountCliApplication":
    return AccountCliApplication(workspace)


def _account(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("account"), dict):
        value = value["account"]
    if not isinstance(value, dict):
        raise ValueError("account CLI returned an invalid account result")
    result = dict(value)
    if "credential" not in result and result.get("credential_id") is not None:
        result["credential"] = result["credential_id"]
    if "segment" not in result and result.get("segments"):
        result["segment"] = result["segments"][0]
    return result


@dataclass(frozen=True, slots=True)
class AccountConfigurationApplication:
    """Configure Account-owned bindings and retain manual verification evidence."""

    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.account_config()

    def list(self) -> list[dict[str, Any]]:
        value = _cli(self.workspace).run(["list"])
        accounts = (
            list(value) if isinstance(value, list) else list(value.get("accounts", []))
        )
        return [
            self._with_verification(self._show_raw(str(item["account_id"])))
            for item in accounts
        ]

    def show(self, account_id: str) -> dict[str, Any]:
        return self._with_verification(self._show_raw(account_id))

    def _show_raw(self, account_id: str) -> dict[str, Any]:
        return _account(
            _cli(self.workspace).run(
                ["show", "--account-id", _text(account_id, "account_id")]
            )
        )

    def verification(self, account_id: str) -> dict[str, Any]:
        account = self._show_raw(account_id)
        fingerprint = self._configuration_fingerprint(account)
        evidence = self._read_verification(account_id)
        if evidence is None:
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested": [],
                "not_tested": [],
                "tested_configuration_hash": None,
                "current_configuration_hash": fingerprint,
            }
        status = str(evidence.get("result") or "failed")
        if evidence.get("configuration_hash") != fingerprint:
            status = "retest_required"
        return {
            "verification_status": status,
            "last_tested_at": evidence.get("tested_at"),
            "tested": list(evidence.get("tested") or ()),
            "not_tested": list(evidence.get("not_tested") or ()),
            "capabilities": list(evidence.get("capabilities") or ()),
            "segments": list(evidence.get("segments") or ()),
            "error_category": evidence.get("error_category"),
            "tested_configuration_hash": evidence.get("configuration_hash"),
            "current_configuration_hash": fingerprint,
        }

    def resource_snapshot(self, account_id: str) -> dict[str, Any]:
        """Return the secret-free Account facts pinned by a Launch instance."""

        account = self._show_raw(account_id)
        verification = self.verification(account_id)
        return {
            "account_id": account_id,
            "broker": account.get("broker"),
            "integration_provider": account.get("integration_provider"),
            "environment": account.get("environment"),
            "masked_remote_identity": _mask_identity(account.get("remote_identity")),
            "segments": list(account.get("segments") or ()),
            "permissions": dict(account.get("permissions") or {}),
            "credential_identities": self._credential_identities(account),
            "verification": verification,
            "resource_hash": self._configuration_fingerprint(account),
        }

    def test_connection(
        self,
        account_id: str,
        *,
        probe: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run an explicit read/permission probe and store only stable, secret-free facts."""

        account = self._show_raw(account_id)
        tested_at = datetime.now(timezone.utc).isoformat()
        try:
            discovered = dict(
                probe(account) if probe is not None else self._probe_account(account)
            )
            account = self._show_raw(account_id)
            evidence = {
                "schema_version": 1,
                "account_id": account_id,
                "configuration_hash": self._configuration_fingerprint(account),
                "result": "verified",
                "tested_at": tested_at,
                "tested": list(
                    discovered.get("tested")
                    or (
                        "identity authentication",
                        "account read",
                        "permission discovery",
                    )
                ),
                "not_tested": list(
                    discovered.get("not_tested")
                    or ("order submission", "fund transfer")
                ),
                "capabilities": sorted(
                    str(value) for value in discovered.get("capabilities") or ()
                ),
                "segments": sorted(
                    str(value)
                    for value in discovered.get("segments")
                    or account.get("segments")
                    or ()
                ),
                "masked_remote_identity": _mask_identity(
                    discovered.get("remote_identity") or account.get("remote_identity")
                ),
            }
        except Exception as error:
            evidence = {
                "schema_version": 1,
                "account_id": account_id,
                "configuration_hash": self._configuration_fingerprint(account),
                "result": "failed",
                "tested_at": tested_at,
                "tested": [
                    "identity authentication",
                    "account read",
                    "permission discovery",
                ],
                "not_tested": ["order submission", "fund transfer"],
                "capabilities": [],
                "segments": [],
                "error_category": _probe_error_category(error),
            }
        _write_json(self._verification_path(account_id), evidence)
        return self.verification(account_id)

    def _probe_account(self, account: Mapping[str, Any]) -> Mapping[str, Any]:
        environment = str(account.get("environment") or "").lower()
        broker = str(account.get("broker") or "").lower()
        if environment in {"paper", "simulated"} or broker in {"paper", "simulated"}:
            return {
                "tested": ["local account availability", "balance state readability"],
                "not_tested": [
                    "remote authentication",
                    "order submission",
                    "fund transfer",
                ],
                "capabilities": ["read", "trade"],
                "segments": list(account.get("segments") or ()),
            }
        credentials = account.get("credentials")
        bindings = credentials if isinstance(credentials, list) else []
        if not bindings and account.get("credential_id"):
            bindings = [
                {
                    "name": "default",
                    "credential_id": account["credential_id"],
                    "role": account.get("credential_role") or "readonly",
                }
            ]
        if not bindings or not isinstance(bindings[0], Mapping):
            raise ValueError("account has no credential binding")
        binding = bindings[0]
        result = _cli(self.workspace).run(
            [
                "credential",
                "add",
                "--account-id",
                str(account["account_id"]),
                "--name",
                str(binding.get("name") or "default"),
                "--credential-id",
                str(binding.get("credential_id") or ""),
                "--role",
                str(binding.get("role") or "readonly"),
                "--force",
            ]
        )
        updated = result.get("account", result) if isinstance(result, Mapping) else {}
        permissions = updated.get("permissions") if isinstance(updated, Mapping) else {}
        capabilities = [
            str(name)
            for name, state in (
                permissions.items() if isinstance(permissions, Mapping) else ()
            )
            if str(state).lower() in {"granted", "true", "enabled"}
        ]
        return {
            "capabilities": capabilities or ["read"],
            "segments": list(updated.get("segments") or ()),
            "remote_identity": updated.get("remote_identity"),
        }

    def _with_verification(self, account: dict[str, Any]) -> dict[str, Any]:
        return {**account, **self.verification(str(account["account_id"]))}

    def _configuration_fingerprint(self, account: Mapping[str, Any]) -> str:
        credential_ids = {
            str(value)
            for value in (account.get("credential_id"),)
            if isinstance(value, str) and value
        }
        bindings = account.get("credentials")
        if isinstance(bindings, list):
            credential_ids.update(
                str(item.get("credential_id"))
                for item in bindings
                if isinstance(item, Mapping) and item.get("credential_id")
            )
        credentials: list[dict[str, Any]] = []
        owner = CredentialConfigurationApplication(self.workspace)
        for credential_id in sorted(credential_ids):
            try:
                value = owner.show(credential_id)
            except (KeyError, OSError, ValueError):
                value = {"credential_id": credential_id, "missing": True}
            credentials.append(
                {
                    "credential_id": credential_id,
                    "provider": value.get("provider"),
                    "role": value.get("role"),
                    "secret_refs": value.get("secret_refs", {}),
                    "legacy_plaintext": value.get("legacy_plaintext", False),
                }
            )
        payload = {
            key: account.get(key)
            for key in (
                "account_id",
                "broker",
                "integration_provider",
                "environment",
                "segments",
                "segment_products",
                "segment_trading_modes",
                "account_model",
                "credential_id",
                "credentials",
                "credential_role",
                "status",
            )
        }
        payload["credential_references"] = credentials
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _credential_identities(
        self, account: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        credential_ids = {
            str(value)
            for value in (account.get("credential_id"),)
            if isinstance(value, str) and value
        }
        bindings = account.get("credentials")
        if isinstance(bindings, list):
            credential_ids.update(
                str(item.get("credential_id"))
                for item in bindings
                if isinstance(item, Mapping) and item.get("credential_id")
            )
        owner = CredentialConfigurationApplication(self.workspace)
        result: list[dict[str, Any]] = []
        for credential_id in sorted(credential_ids):
            try:
                value = owner.show(credential_id)
            except (KeyError, OSError, ValueError):
                result.append({"credential_id": credential_id, "missing": True})
                continue
            result.append(
                {
                    "credential_id": credential_id,
                    "provider": value.get("provider"),
                    "role": value.get("role"),
                    "secret_refs": value.get("secret_refs", {}),
                }
            )
        return result

    def _verification_path(self, account_id: str) -> Path:
        return self.workspace.paths.child(
            "state",
            "configuration",
            "accounts",
            f"{_text(account_id, 'account_id')}.json",
        )

    def _read_verification(self, account_id: str) -> dict[str, Any] | None:
        path = self._verification_path(account_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def schemas(self) -> dict[str, Any]:
        return dict(_cli(self.workspace).run(["schemas"]))

    def schema(self, broker: str) -> dict[str, Any]:
        return dict(
            _cli(self.workspace).run(["schema", "--broker", _text(broker, "broker")])
        )

    def connect(
        self,
        account_id: str,
        *,
        broker: str = "binance",
        integration_provider: str | None = None,
        segment: str = "spot",
        environment: str = "live",
        credential: str | None = None,
        credential_role: str = "readonly",
        alias: str | None = None,
        product_family: str | None = None,
        account_model: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        account_id = _text(account_id, "account_id")
        if (
            environment == "live"
            and broker not in {"paper", "ibkr"}
            and not credential
            and not force
        ):
            raise ValueError("live account requires --credential or --force")
        if integration_provider is None and credential is not None:
            integration_provider = str(
                CredentialApplication(self.workspace).show(credential)["provider"]
            )
        if integration_provider is None:
            raise ValueError(
                "integration_provider is required when the account has no credential"
            )
        _cli(self.workspace).run(
            [
                "register",
                "--account-id",
                account_id,
                "--broker",
                broker,
                "--integration-provider",
                integration_provider,
                "--segment",
                segment,
                "--environment",
                environment,
                *(["--account-model", account_model] if account_model else []),
            ]
        )
        if credential:
            _cli(self.workspace).run(
                [
                    "modify",
                    "--account-id",
                    account_id,
                    "--credential-id",
                    credential,
                    "--credential-role",
                    credential_role,
                    *(["--alias", alias] if alias else []),
                ]
            )
        elif alias:
            _cli(self.workspace).run(
                ["modify", "--account-id", account_id, "--alias", alias]
            )
        return self.show(account_id)

    def simulate(
        self,
        account_id: str,
        *,
        broker: str = "paper",
        segment: str = "spot",
        environment: str = "paper",
        account_model: str | None = None,
        initial_balances: tuple[str, ...] = (),
        fee_rate: str = "0",
        force: bool = False,
    ) -> dict[str, Any]:
        value = _cli(self.workspace).run(
            [
                "simulate",
                "--account-id",
                _text(account_id, "account_id"),
                "--segment",
                segment,
                *(["--account-model", account_model] if account_model else []),
                *sum((["--balance", balance] for balance in initial_balances), []),
                "--fee-rate",
                fee_rate,
            ]
        )
        result = _account(value)
        result["mode"] = "paper"
        return result

    def modify(
        self, account_id: str, *, _force: bool = False, **changes: Any
    ) -> dict[str, Any]:
        account_id = _text(account_id, "account_id")
        flags = {
            "broker": "--broker",
            "exchange": "--exchange",
            "alias": "--alias",
            "environment": "--environment",
            "segment": "--segment",
            "account_model": "--account-model",
            "credential": "--credential-id",
            "credential_role": "--credential-role",
            "status": "--status",
            "fee_rate": "--fee-rate",
        }
        arguments = ["modify", "--account-id", account_id]
        for key, flag in flags.items():
            value = changes.get(key)
            if value is not None:
                arguments.extend([flag, str(value)])
        for balance in changes.get("initial_balances") or ():
            arguments.extend(["--balance", str(balance)])
        return _account(_cli(self.workspace).run(arguments))

    def bind_credential(
        self, account_id: str, *, name: str, ref: str, role: str = "readonly"
    ) -> dict[str, Any]:
        value = _cli(self.workspace).run(
            [
                "credential",
                "add",
                "--account-id",
                _text(account_id, "account_id"),
                "--name",
                _text(name, "name"),
                "--credential-id",
                _text(ref, "credential_ref"),
                "--role",
                role,
                "--force",
            ]
        )
        return _account(value)

    def switch_model(
        self, account_id: str, target: str, *, reason: str = ""
    ) -> dict[str, Any]:
        return dict(
            _cli(self.workspace).run(
                [
                    "model",
                    "switch",
                    "--account-id",
                    _text(account_id, "account_id"),
                    "--target",
                    _text(target, "target"),
                    "--reason",
                    reason,
                ]
            )
        )

    def delete(self, account_id: str, *, force: bool = False) -> dict[str, Any]:
        value = dict(
            _cli(self.workspace).run(
                [
                    "remove",
                    "--account-id",
                    _text(account_id, "account_id"),
                    *(["--force"] if force else []),
                ]
            )
        )
        self._verification_path(account_id).unlink(missing_ok=True)
        return {
            "account_id": account_id,
            "status": "deleted" if value.get("removed") else "not_found",
        }

    def doctor(self, account_id: str | None = None) -> dict[str, Any]:
        value = dict(_cli(self.workspace).run(["doctor"]))
        if account_id:
            value["accounts"] = [self.show(account_id)]
        value["path"] = str(self.path)
        return value


@dataclass(frozen=True, slots=True)
class CredentialApplication:
    """Thin Python adapter over Workspace-owned credential use cases."""

    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.credential_config()

    def list(self) -> list[dict[str, Any]]:
        value = _cli(self.workspace).run(["credential-list"])
        return (
            list(value)
            if isinstance(value, list)
            else list(value.get("credentials", []))
        )

    def add(
        self,
        credential_id: str,
        *,
        provider: str,
        fields: tuple[str, ...] = (),
        kind: str | None = None,
        force: bool = True,
    ) -> dict[str, Any]:
        credential_id = _text(credential_id, "credential_id")
        provider = _text(provider, "provider")
        environment = _credential_environment_names(credential_id, fields)
        references = {
            field: SecretRef("env", environment[field.upper()]) for field in fields
        }
        CredentialConfigurationApplication(self.workspace).configure(
            credential_id,
            provider=provider,
            fields=references,
            role=kind or "readonly",
            overwrite=force,
        )
        return {
            "credential_id": credential_id,
            "provider": provider,
            "kind": kind or "api",
            "fields": list(fields),
            "secret_refs": {
                field: {"source": "env", "id": environment[field.upper()]}
                for field in fields
            },
            "secret_storage": "environment-or-external-secret-store",
        }

    def show(self, credential_id: str) -> dict[str, Any]:
        return dict(
            _cli(self.workspace).run(
                [
                    "credential-show",
                    "--credential-id",
                    _text(credential_id, "credential_id"),
                ]
            )
        )

    def delete(self, credential_id: str, *, force: bool = False) -> dict[str, Any]:
        try:
            value = dict(
                _cli(self.workspace).run(
                    [
                        "credential-delete",
                        "--credential-id",
                        _text(credential_id, "credential_id"),
                        *(["--force"] if force else []),
                    ]
                )
            )
        except RuntimeError as error:
            if "bound to an account" in str(error):
                raise ValueError(str(error)) from error
            raise
        if not force and not value.get("removed"):
            raise FileNotFoundError(f"credential does not exist: {credential_id}")
        return {"credential_id": credential_id, "status": "deleted"}

    def environment(self, credential_id: str) -> dict[str, str]:
        entry = self.show(credential_id)
        provider = str(entry.get("provider", "")).lower()
        fields = {
            "feishu": ("webhook_url", "signing_secret", "api_key", "api_secret"),
            "telegram": ("bot_token", "api_key"),
            "okx": ("api_key", "api_secret", "passphrase"),
            "okex": ("api_key", "api_secret", "passphrase"),
        }.get(provider, ("api_key", "api_secret"))
        prefix = "KAIROS_CREDENTIAL_" + "".join(
            c if c.isalnum() else "_" for c in credential_id.upper()
        )
        return {
            field.upper(): os.environ[name]
            for field in fields
            if (name := f"{prefix}_{field.upper()}") in os.environ
        }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _mask_identity(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * min(8, len(value) - 4)}{value[-2:]}"


def _probe_error_category(error: Exception) -> str:
    text = str(error).lower()
    if any(
        value in text for value in ("auth", "credential", "permission", "unauthorized")
    ):
        return "authentication_or_permission"
    if any(value in text for value in ("timeout", "network", "connect", "dns")):
        return "network"
    if any(value in text for value in ("rate", "429", "quota")):
        return "rate_limited"
    return "provider_response"


@dataclass(frozen=True, slots=True)
class TradeLeaseApplication:
    """Workspace-owned launch lease adapter."""

    workspace: Workspace
    stale_after_seconds: float = 60.0

    @property
    def path(self) -> Path:
        return self.workspace.paths.account_leases()

    def for_account(self, account_id: str) -> list[dict[str, Any]]:
        return [item for item in self.list() if item.get("account_id") == account_id]

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.path.exists():
            return rows
        for directory in sorted(self.path.iterdir()):
            owner = directory / "owner.json"
            if not owner.exists():
                continue
            try:
                record = json.loads(owner.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            record["account_key"] = directory.name
            record["path"] = str(directory)
            record["stale"] = self._stale(record)
            rows.append(record)
        return rows

    def acquire(
        self,
        *,
        broker: str,
        account_id: str,
        environment: str,
        launch_id: str,
        launch_instance_id: str,
        mode: str,
        pid: int | None = None,
    ) -> dict[str, Any]:
        key = self._key(broker, account_id)
        path = self.path / key
        self.path.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "broker": broker,
            "account_id": account_id,
            "environment": environment,
            "launch_id": launch_id,
            "launch_instance_id": launch_instance_id,
            "mode": mode,
            "pid": pid or os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": now,
            "heartbeat_at": now,
            "fencing_token": uuid4().hex,
        }
        try:
            path.mkdir()
        except FileExistsError as error:
            existing = next(
                (item for item in self.list() if item["account_key"] == key), None
            )
            if existing and existing.get("stale"):
                shutil.rmtree(path)
                path.mkdir()
            elif existing and existing.get("launch_instance_id") == launch_instance_id:
                record["fencing_token"] = existing.get("fencing_token") or uuid4().hex
            else:
                raise ValueError(f"account {key} trading is already leased") from error
        _write_json(path / "owner.json", record)
        return {"account_key": key, **record, "path": str(path), "stale": False}

    @classmethod
    def account_key(cls, broker: str, account_id: str) -> str:
        """Return the canonical workspace key for an account lease."""
        return cls._key(broker, account_id)

    def release_account(
        self,
        broker: str,
        account_id: str,
        *,
        launch_instance_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Release a lease using the same canonical key as acquisition."""
        return self.release(
            self.account_key(broker, account_id),
            launch_instance_id=launch_instance_id,
            force=force,
        )

    def heartbeat(self, account_key: str, *, launch_instance_id: str) -> dict[str, Any]:
        record = self._find(account_key)
        if record.get("launch_instance_id") != launch_instance_id:
            raise ValueError(f"account {account_key} is leased by another instance")
        record["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(
            Path(record["path"]) / "owner.json",
            {
                key: value
                for key, value in record.items()
                if key not in {"account_key", "path", "stale"}
            },
        )
        return record

    def release(
        self,
        account_key: str,
        *,
        launch_instance_id: str | None = None,
        force: bool = False,
        stale_only: bool = False,
    ) -> dict[str, Any]:
        record = self._find(account_key)
        if stale_only and not record.get("stale"):
            raise ValueError(f"account {account_key} trading lease is not stale")
        if (
            not force
            and launch_instance_id
            and record.get("launch_instance_id") != launch_instance_id
        ):
            raise ValueError(f"account {account_key} is leased by another instance")
        shutil.rmtree(record["path"])
        return {"account_key": account_key, "status": "released"}

    def _find(self, key: str) -> dict[str, Any]:
        for record in self.list():
            if record.get("account_key") == key:
                return record
        raise FileNotFoundError(f"account lease does not exist: {key}")

    def _stale(self, record: dict[str, Any]) -> bool:
        try:
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(record["heartbeat_at"])
            ).total_seconds()
        except (KeyError, ValueError):
            return True
        if age <= self.stale_after_seconds:
            return False
        try:
            os.kill(int(record.get("pid", 0)), 0)
        except (ProcessLookupError, ValueError):
            return True
        except PermissionError:
            return False
        return False

    @staticmethod
    def _key(broker: str, account_id: str) -> str:
        return ".".join(
            "_".join(
                "".join(c if c.isalnum() else "_" for c in value.lower()).split("_")
            )
            for value in (broker, account_id)
            if value
        )


from .cli import AccountCliApplication  # noqa: E402

# Compatibility name for callers that have not yet migrated to the explicit
# configuration-boundary vocabulary.
AccountAdminApplication = AccountConfigurationApplication

from .draft import (  # noqa: E402
    AccountConfigurationDraft,
    AccountConfigurationDraftApplication,
)


__all__ = [
    "AccountConfigurationDraft",
    "AccountConfigurationDraftApplication",
    "AccountApplication",
    "AccountAdminApplication",
    "AccountConfigurationApplication",
    "AccountEvent",
    "AccountLookupError",
    "AccountNotEnabledError",
    "AccountSegmentNotFoundError",
    "AccountSegmentSnapshot",
    "AccountSnapshot",
    "AccountsSnapshot",
    "AccountStatusChange",
    "AccountStatusChangedEvent",
    "Balance",
    "BalanceNotFoundError",
    "BalanceChangedEvent",
    "AccountCliApplication",
    "CredentialApplication",
    "DataFreshness",
    "EarnHolding",
    "EarnHoldingChangedEvent",
    "EarnHoldingState",
    "EarnLiquidity",
    "EquityChange",
    "EquityChangedEvent",
    "ObservedOrder",
    "ObservedOrderChangedEvent",
    "Position",
    "PositionSide",
    "PositionNotFoundError",
    "PositionChangedEvent",
    "SegmentCompleteness",
    "SegmentSyncLifecycle",
    "SegmentSyncMode",
    "TradeLeaseApplication",
    "COIN_M_FUTURES",
    "CROSS_MARGIN",
    "EQUITY",
    "FUNDING",
    "ISOLATED_MARGIN",
    "OPTIONS",
    "SPOT",
    "USD_M_FUTURES",
]

"""Account-facing adapters.

Account state and account administration are owned by the Rust account
application.  This module only adapts Python callers and keeps workspace
lease handling in the system layer until that protocol is moved there.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..workspace import Workspace


def _text(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _cli(workspace: Workspace) -> "AccountCliApplication":
    return AccountCliApplication(workspace)


def _account(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("account"), dict):
        value = value["account"]
    if not isinstance(value, dict):
        raise ValueError("account CLI returned an invalid account result")
    result = dict(value)
    if "broker" not in result and "provider" in result:
        result["broker"] = result["provider"]
    if "credential" not in result and result.get("credential_id") is not None:
        result["credential"] = result["credential_id"]
    if "segment" not in result and result.get("segments"):
        result["segment"] = result["segments"][0]
    return result


@dataclass(frozen=True, slots=True)
class AccountAdminApplication:
    """Thin Python adapter over ``kairos-account-cli`` administration."""

    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.account_config()

    def list(self) -> list[dict[str, Any]]:
        value = _cli(self.workspace).run(["list"])
        return list(value) if isinstance(value, list) else list(value.get("accounts", []))

    def show(self, account_id: str) -> dict[str, Any]:
        return _account(_cli(self.workspace).run(["show", "--account-id", _text(account_id, "account_id")]))

    def schemas(self) -> dict[str, Any]:
        return dict(_cli(self.workspace).run(["schemas"]))

    def schema(self, broker: str) -> dict[str, Any]:
        return dict(_cli(self.workspace).run(["schema", "--provider", _text(broker, "broker")]))

    def connect(
        self,
        account_id: str,
        *,
        broker: str = "binance",
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
        if environment == "live" and broker not in {"paper", "ibkr"} and not credential and not force:
            raise ValueError("live account requires --credential or --force")
        _cli(self.workspace).run([
            "register", "--account-id", account_id, "--provider", broker,
            "--segment", segment, "--environment", environment,
            *(["--account-model", account_model] if account_model else []),
            *(["--venue", broker] if product_family is None else ["--venue", product_family]),
        ])
        if credential:
            _cli(self.workspace).run([
                "modify", "--account-id", account_id,
                "--credential-id", credential, "--credential-role", credential_role,
                *(["--alias", alias] if alias else []),
            ])
        elif alias:
            _cli(self.workspace).run(["modify", "--account-id", account_id, "--alias", alias])
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
        value = _cli(self.workspace).run([
            "simulate", "--account-id", _text(account_id, "account_id"),
            "--segment", segment,
            *(["--account-model", account_model] if account_model else []),
            *sum((["--balance", balance] for balance in initial_balances), []),
            "--fee-rate", fee_rate,
        ])
        result = _account(value)
        result["mode"] = "paper"
        return result

    def modify(self, account_id: str, *, _force: bool = False, **changes: Any) -> dict[str, Any]:
        account_id = _text(account_id, "account_id")
        flags = {
            "broker": "--provider", "venue": "--venue", "alias": "--alias",
            "environment": "--environment", "segment": "--segment",
            "account_model": "--account-model", "credential": "--credential-id",
            "credential_role": "--credential-role", "status": "--status",
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

    def bind_credential(self, account_id: str, *, name: str, ref: str, role: str = "readonly") -> dict[str, Any]:
        value = _cli(self.workspace).run([
            "credential", "add", "--account-id", _text(account_id, "account_id"),
            "--name", _text(name, "name"), "--credential-id", _text(ref, "credential_ref"),
            "--role", role, "--force",
        ])
        return _account(value)

    def switch_model(self, account_id: str, target: str, *, reason: str = "") -> dict[str, Any]:
        return dict(_cli(self.workspace).run([
            "model", "switch", "--account-id", _text(account_id, "account_id"),
            "--target", _text(target, "target"), "--reason", reason,
        ]))

    def delete(self, account_id: str, *, force: bool = False) -> dict[str, Any]:
        value = dict(_cli(self.workspace).run([
            "remove", "--account-id", _text(account_id, "account_id"),
            *(["--force"] if force else []),
        ]))
        return {"account_id": account_id, "status": "deleted" if value.get("removed") else "not_found"}

    def doctor(self, account_id: str | None = None) -> dict[str, Any]:
        value = dict(_cli(self.workspace).run(["doctor"]))
        if account_id:
            value["accounts"] = [self.show(account_id)]
        value["path"] = str(self.path)
        return value


@dataclass(frozen=True, slots=True)
class CredentialApplication:
    """Thin Python adapter over account credential use cases."""

    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.credential_config()

    def list(self) -> list[dict[str, Any]]:
        value = _cli(self.workspace).run(["credential-list"])
        return list(value) if isinstance(value, list) else list(value.get("credentials", []))

    def add(self, credential_id: str, *, provider: str, fields: tuple[str, ...] = (), kind: str | None = None, force: bool = True) -> dict[str, Any]:
        _cli(self.workspace).run([
            "credential-create", "--credential-id", _text(credential_id, "credential_id"),
            "--provider", _text(provider, "provider"),
        ])
        return {"credential_id": credential_id, "provider": provider, "kind": kind or "api", "fields": list(fields), "secret_storage": "environment-or-external-secret-store"}

    def show(self, credential_id: str) -> dict[str, Any]:
        return dict(_cli(self.workspace).run(["credential-show", "--credential-id", _text(credential_id, "credential_id")]))

    def delete(self, credential_id: str, *, force: bool = False) -> dict[str, Any]:
        try:
            value = dict(_cli(self.workspace).run([
                "credential-delete", "--credential-id", _text(credential_id, "credential_id"),
                *(["--force"] if force else []),
            ]))
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
        fields = ("api_key", "api_secret", "passphrase") if provider in {"okx", "okex"} else ("api_key", "api_secret")
        prefix = "KAIROS_CREDENTIAL_" + "".join(c if c.isalnum() else "_" for c in credential_id.upper())
        return {
            field.upper(): os.environ[name]
            for field in fields
            if (name := f"{prefix}_{field.upper()}") in os.environ
        }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


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

    def acquire(self, *, broker: str, account_id: str, environment: str, launch_id: str, launch_instance_id: str, mode: str, pid: int | None = None) -> dict[str, Any]:
        key = self._key(broker, account_id)
        path = self.path / key
        self.path.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        record = {"broker": broker, "account_id": account_id, "environment": environment, "launch_id": launch_id, "launch_instance_id": launch_instance_id, "mode": mode, "pid": pid or os.getpid(), "host": socket.gethostname(), "acquired_at": now, "heartbeat_at": now}
        try:
            path.mkdir()
        except FileExistsError as error:
            existing = next((item for item in self.list() if item["account_key"] == key), None)
            if existing and existing.get("stale"):
                shutil.rmtree(path)
                path.mkdir()
            elif existing and existing.get("launch_instance_id") == launch_instance_id:
                pass
            else:
                raise ValueError(f"account {key} trading is already leased") from error
        _write_json(path / "owner.json", record)
        return {"account_key": key, **record, "path": str(path), "stale": False}

    def heartbeat(self, account_key: str, *, launch_instance_id: str) -> dict[str, Any]:
        record = self._find(account_key)
        if record.get("launch_instance_id") != launch_instance_id:
            raise ValueError(f"account {account_key} is leased by another instance")
        record["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(Path(record["path"]) / "owner.json", {key: value for key, value in record.items() if key not in {"account_key", "path", "stale"}})
        return record

    def release(self, account_key: str, *, launch_instance_id: str | None = None, force: bool = False, stale_only: bool = False) -> dict[str, Any]:
        record = self._find(account_key)
        if stale_only and not record.get("stale"):
            raise ValueError(f"account {account_key} trading lease is not stale")
        if not force and launch_instance_id and record.get("launch_instance_id") != launch_instance_id:
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
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(record["heartbeat_at"])).total_seconds()
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
        return ".".join("_".join("".join(c if c.isalnum() else "_" for c in value.lower()).split("_")) for value in (broker, account_id) if value)


TradeLockApplication = TradeLeaseApplication

from .cli import AccountCliApplication

__all__ = ["AccountAdminApplication", "AccountCliApplication", "CredentialApplication", "TradeLeaseApplication", "TradeLockApplication"]

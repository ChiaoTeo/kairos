"""Account use cases owned by a workspace.

The account module owns configuration and trading leases.  Provider clients,
secrets and the live account actor remain composition/runtime concerns.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..workspace import Workspace


def _text(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Write runtime lease/output metadata; account configuration uses TOML below."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def _toml_document(value: dict[str, Any]) -> str:
    lines: list[str] = []
    nested: list[tuple[str, dict[str, Any]]] = []
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, dict):
            nested.append((key, item))
        else:
            lines.append(f"{key} = {_toml_value(item)}")
    for section, items in nested:
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, item in items.items():
            if item is not None and not isinstance(item, dict):
                lines.append(f"{key} = {_toml_value(item)}")
    return "\n".join(lines) + "\n"


def _account_toml(value: dict[str, Any]) -> str:
    account_id = value.get("account_id", "")
    lines = [
        "[account]",
        f"id = {_toml_value(str(account_id))}",
        f"broker = {_toml_value(str(value.get('broker', '')))}",
        f"environment = {_toml_value(str(value.get('environment', 'paper')))}",
    ]
    for key in ("venue", "credential", "default_segment"):
        if value.get(key) is not None:
            lines.append(f"{key} = {_toml_value(str(value[key]))}")
    if value.get("account_model") is not None:
        lines.append(f"model = {_toml_value(str(value['account_model']))}")
    segments = value.get("segments") or [value.get("product_family", value.get("segment", "spot"))]
    for segment in segments if isinstance(segments, list) else [segments]:
        segment_name = str(segment)
        lines.extend(["", f"[segments.{_safe_filename(segment_name)}]", f"product_family = {_toml_value(segment_name)}"])
        if value.get("account_model") is not None:
            lines.append(f"model = {_toml_value(str(value['account_model']))}")
    balances = value.get("initial_balances")
    if isinstance(balances, list):
        lines.append("")
        lines.append("[initial_balances]")
        for item in balances:
            asset, separator, amount = str(item).partition("=")
            if separator and asset.strip():
                lines.append(f"{asset.strip()} = {_toml_value(amount.strip())}")
    if value.get("credential") is not None:
        lines.extend([
            "",
            "[credentials.default]",
            f"ref = {_toml_value(str(value['credential']))}",
            f"role = {_toml_value(str(value.get('credential_role') or 'readonly'))}",
        ])
    return "\n".join(lines) + "\n"


def _credential_toml(value: dict[str, Any]) -> str:
    lines = [
        "[credential]",
        f"id = {_toml_value(str(value.get('credential_id', '')))}",
        f"broker = {_toml_value(str(value.get('provider', '')))}",
        f"role = {_toml_value(str(value.get('role', 'readonly')))}",
    ]
    if value.get("kind") is not None:
        lines.append(f"kind = {_toml_value(str(value['kind']))}")
    fields = value.get("fields")
    if isinstance(fields, list):
        lines.append(f"fields = {_toml_value([str(item) for item in fields])}")
    if value.get("secret_storage") is not None:
        lines.append(f"secret_storage = {_toml_value(str(value['secret_storage']))}")
    return "\n".join(lines) + "\n"


def _normalize_record(value: dict[str, Any], identifier: str) -> dict[str, Any] | None:
    if isinstance(value.get(identifier), str):
        return value
    section_name, field_name = ("account", "id") if identifier == "account_id" else ("credential", "id")
    section = value.get(section_name)
    if not isinstance(section, dict) or not isinstance(section.get(field_name), str):
        return None
    result = dict(value)
    result.update(section)
    result[identifier] = result.pop(field_name)
    if identifier == "account_id":
        result.setdefault("broker", result.get("provider"))
        result.setdefault("segment", result.get("default_segment", "spot"))
        if "account_model" not in result and isinstance(result.get("model"), str):
            result["account_model"] = result["model"]
        segments = result.get("segments")
        if isinstance(segments, dict):
            result["segments"] = list(segments)
        credentials = result.get("credentials")
        if isinstance(credentials, dict) and credentials:
            first = next(iter(credentials.values()))
            if isinstance(first, dict):
                result.setdefault("credential", first.get("ref"))
                result.setdefault("credential_role", first.get("role"))
    else:
        result.setdefault("provider", result.get("broker"))
    return result


def _safe_filename(value: str) -> str:
    result = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return result or "record"


def _read_records(path: Path, key: str, identifier: str) -> list[dict[str, Any]]:
    """Read canonical per-record TOML configuration."""
    records: list[dict[str, Any]] = []
    if path.parent.is_dir():
        for candidate in sorted(path.parent.glob("*.toml")):
            if candidate == path:
                continue
            try:
                import tomllib
                value = tomllib.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                record = _normalize_record(value, identifier)
                if record is not None:
                    records.append(record)
    return records


def _write_records(path: Path, key: str, identifier: str, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = {_safe_filename(str(item[identifier])) + ".toml" for item in records}
    for candidate in path.parent.glob("*.toml"):
        if candidate != path and candidate.name not in expected:
            candidate.unlink()
    for item in records:
        target = path.parent / f"{_safe_filename(str(item[identifier]))}.toml"
        temporary = target.with_suffix(".toml.tmp")
        document = _account_toml(item) if identifier == "account_id" else _credential_toml(item)
        temporary.write_text(document, encoding="utf-8")
        temporary.replace(target)


@dataclass(frozen=True, slots=True)
class AccountAdminApplication:
    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.account_config()

    def list(self) -> list[dict[str, Any]]:
        return _read_records(self.path, "accounts", "account_id")

    def show(self, account_id: str) -> dict[str, Any]:
        account_id = _text(account_id, "account_id")
        for account in self.list():
            if account.get("account_id") == account_id:
                return account
        raise FileNotFoundError(f"account does not exist: {account_id}")

    def schemas(self) -> dict[str, Any]:
        return {
            "binance": {"credential_fields": ["api_key", "api_secret"], "segments": ["spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding", "options"]},
            "okx": {"credential_fields": ["api_key", "api_secret", "passphrase"], "segments": ["spot", "cross_margin", "isolated_margin", "swap", "futures", "options"]},
            "ibkr": {"credential_fields": [], "connection_fields": ["host", "port", "client_id"], "segments": ["equity"]},
            "paper": {"credential_fields": [], "segments": ["spot", "margin", "futures"]},
        }

    def schema(self, broker: str) -> dict[str, Any]:
        broker = _text(broker, "broker").lower()
        broker = "okx" if broker == "okex" else broker
        try:
            return {"broker": broker, **self.schemas()[broker]}
        except KeyError as error:
            raise ValueError(f"unsupported broker: {broker}") from error

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
        broker = _text(broker, "broker").lower()
        broker = "okx" if broker == "okex" else broker
        segment = _text(segment, "segment").lower()
        environment = _text(environment, "environment").lower()
        if broker not in self.schemas():
            raise ValueError(f"unsupported broker: {broker}")
        if credential_role not in {"readonly", "trade"}:
            raise ValueError("credential_role must be readonly or trade")
        if environment == "live" and broker not in {"paper", "ibkr"} and not credential and not force:
            raise ValueError("live account requires --credential or --force")
        account = {
            "account_id": account_id,
            "alias": alias or account_id,
            "broker": broker,
            "segment": segment,
            "environment": environment,
            "venue": broker,
            "product_family": product_family or segment,
            "account_model": account_model,
            "credential": credential,
            "credential_role": credential_role if credential else None,
            "status": "configured",
        }
        return self._upsert(account, force=force)

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
        account = self.connect(account_id, broker=broker, segment=segment, environment=environment, account_model=account_model, force=True)
        return self.modify(account_id, initial_balances=list(initial_balances), fee_rate=fee_rate, status="simulated", _force=force) | {"mode": "paper"}

    def modify(self, account_id: str, *, _force: bool = False, **changes: Any) -> dict[str, Any]:
        account = dict(self.show(account_id))
        changes = {key: value for key, value in changes.items() if value is not None and not key.startswith("_")}
        if "credential_role" in changes and changes["credential_role"] not in {"readonly", "trade"}:
            raise ValueError("credential_role must be readonly or trade")
        if "environment" in changes and changes["environment"] == "live" and not account.get("credential") and not _force:
            raise ValueError("live account requires a credential")
        account.update(changes)
        return self._upsert(account, force=True)

    def bind_credential(self, account_id: str, *, name: str, ref: str, role: str = "readonly") -> dict[str, Any]:
        _text(name, "name")
        credential = CredentialApplication(self.workspace).show(_text(ref, "credential_ref"))
        return self.modify(account_id, credential=credential["credential_id"], credential_role=role)

    def switch_model(self, account_id: str, target: str, *, reason: str = "") -> dict[str, Any]:
        account = self.show(account_id)
        target = _text(target, "target")
        current = account.get("account_model")
        return {
            "account_id": account_id,
            "from": current,
            "to": target,
            "status": "rejected",
            "reason": "venue adapter does not support automatic account model switching; manual confirmation required",
            "requested_reason": reason,
        }

    def delete(self, account_id: str, *, force: bool = False) -> dict[str, Any]:
        self.show(account_id)
        if not force and TradeLeaseApplication(self.workspace).for_account(account_id):
            raise ValueError("account has an active trading lease; use --force to delete")
        records = [item for item in self.list() if item.get("account_id") != account_id]
        _write_records(self.path, "accounts", "account_id", records)
        return {"account_id": account_id, "status": "deleted"}

    def doctor(self, account_id: str | None = None) -> dict[str, Any]:
        accounts = self.list()
        if account_id:
            accounts = [self.show(account_id)]
        issues: list[str] = []
        for account in accounts:
            if account.get("environment") == "live" and not account.get("credential"):
                issues.append(f"{account.get('account_id')}: live account has no credential")
        return {"accounts": accounts, "issues": issues, "path": str(self.path)}

    def _upsert(self, account: dict[str, Any], *, force: bool) -> dict[str, Any]:
        existing = [item for item in self.list() if item.get("account_id") == account["account_id"]]
        if existing and not force:
            raise ValueError(f"account already exists: {account['account_id']}")
        accounts = [item for item in self.list() if item.get("account_id") != account["account_id"]]
        accounts.append(account)
        _write_records(self.path, "accounts", "account_id", sorted(accounts, key=lambda item: str(item.get("account_id"))))
        return account


@dataclass(frozen=True, slots=True)
class CredentialApplication:
    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.credential_config()

    def list(self) -> list[dict[str, Any]]:
        return _read_records(self.path, "credentials", "credential_id")

    def add(self, credential_id: str, *, provider: str, fields: tuple[str, ...] = (), kind: str | None = None, force: bool = True) -> dict[str, Any]:
        credential_id = _text(credential_id, "credential_id")
        provider = _text(provider, "provider").lower()
        current = self.list()
        if any(item.get("credential_id") == credential_id for item in current) and not force:
            raise ValueError(f"credential already exists: {credential_id}")
        entry = {"credential_id": credential_id, "provider": provider, "kind": kind or "api", "fields": list(fields), "secret_storage": "environment-or-external-secret-store"}
        entries = [item for item in current if item.get("credential_id") != credential_id] + [entry]
        _write_records(self.path, "credentials", "credential_id", sorted(entries, key=lambda item: item["credential_id"]))
        return entry

    def show(self, credential_id: str) -> dict[str, Any]:
        for item in self.list():
            if item.get("credential_id") == credential_id:
                return item
        raise FileNotFoundError(f"credential does not exist: {credential_id}")

    def delete(self, credential_id: str, *, force: bool = False) -> dict[str, Any]:
        self.show(credential_id)
        bound = [a["account_id"] for a in AccountAdminApplication(self.workspace).list() if a.get("credential") == credential_id]
        if bound and not force:
            raise ValueError(f"credential is bound to accounts: {', '.join(bound)}")
        _write_records(self.path, "credentials", "credential_id", [item for item in self.list() if item.get("credential_id") != credential_id])
        return {"credential_id": credential_id, "status": "deleted"}

    def environment(self, credential_id: str) -> dict[str, str]:
        """Resolve namespaced environment secrets without persisting values.

        A credential named ``binance-live`` reads
        ``KAIROS_CREDENTIAL_BINANCE_LIVE_API_KEY`` and corresponding fields.
        The process composition maps these values to the provider SDK's
        conventional variables only for the child process.
        """
        entry = self.show(credential_id)
        prefix = "KAIROS_CREDENTIAL_" + "".join(
            c if c.isalnum() else "_" for c in credential_id.upper()
        )
        return {field.upper(): os.environ[name] for field in entry.get("fields", ()) if (name := f"{prefix}_{field.upper()}") in os.environ}


@dataclass(frozen=True, slots=True)
class TradeLeaseApplication:
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


# Kept as a name that describes the use case; it is not a free-standing lock file.
TradeLockApplication = TradeLeaseApplication

from .cli import AccountCliApplication


__all__ = ["AccountAdminApplication", "AccountCliApplication", "CredentialApplication", "TradeLeaseApplication", "TradeLockApplication"]

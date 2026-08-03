from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from kairospy.application.usecases.account.application.authorization import AccountAuthorizationService, AccountTradeAuthorizationRequest, AccountTradeAuthorizationResult, trade_lock_state
from kairospy.application.usecases.account.application.queries import AccountQueryService
from kairospy.application.usecases.execution.application.query import ExecutionOrderQueries
from kairospy.application.support.system.domain.workspace import KairosWorkspace
from kairospy.application.support.system.domain.config import SYSTEM_LAUNCH_ID
from kairospy.domain.views import ViewEnvelope

from .commands import SystemCommand, SystemCommandResult


@dataclass(frozen=True, slots=True)
class SystemCommandDispatcher:
    directory: Path

    def dispatch(self, command: SystemCommand) -> SystemCommandResult:
        if command.kind == "runtime.stop":
            return self._runtime_stop(command)
        if command.kind in {"account.current", "account.balances", "account.positions", "account.open_orders", "account.pending_orders"}:
            return self._account_query(command)
        if command.kind == "account.trade-status":
            return self._trade_status(command)
        if command.kind == "account.trade-acquire":
            return self._trade_acquire(command)
        if command.kind == "account.trade-release":
            return self._trade_release(command)
        if command.kind == "order.status":
            return self._order_query(command)
        return SystemCommandResult.rejected(command, f"unsupported system command: {command.kind}")

    def _runtime_stop(self, command: SystemCommand) -> SystemCommandResult:
        reason = str(command.payload.get("reason") or "requested by system command")
        return SystemCommandResult.accepted(command, {"desired_state": "stopped", "reason": reason})

    def _account_query(self, command: SystemCommand) -> SystemCommandResult:
        service = AccountQueryService(_ArtifactViewSource(self.directory))
        account = _optional_text(command.payload.get("account"))
        if command.kind == "account.current":
            result = {"account": account, "current": service.current(account)}
        elif command.kind == "account.balances":
            result = {"account": account, "balances": service.balances(account=account)}
        elif command.kind == "account.positions":
            result = {"account": account, "positions": service.positions(account=account)}
        elif command.kind == "account.open_orders":
            result = {"account": account, "open_orders": service.open_orders(account=account)}
        else:
            result = {"account": account, "pending_orders": service.pending_orders(account=account)}
        return SystemCommandResult.accepted(command, result)

    def _trade_status(self, command: SystemCommand) -> SystemCommandResult:
        try:
            workspace = KairosWorkspace.resolve(self.directory)
        except Exception as error:
            return SystemCommandResult.rejected(command, str(error))
        account_filter = _optional_text(command.payload.get("account"))
        system_instance_id = _system_instance_id(self.directory)
        rows = []
        for account in workspace.accounts.list():
            if account_filter is not None and account_filter not in {account.account_id, account.account_key}:
                continue
            lock = workspace.account_locks.get(account.account_key)
            owned_by_system = lock is not None and system_instance_id is not None and lock.launch_instance_id == system_instance_id
            tradable_books = _tradable_books(account)
            can_trade = any(
                _trade_authorization(account, book, lock=lock, owned_by_system=owned_by_system).allowed
                for book in tuple(getattr(account, "books", ()) or ())
            )
            rows.append(
                {
                    "account": account.account_id,
                    "account_key": account.account_key,
                    "environment": account.environment,
                    "tradable_books": tradable_books,
                    "trade_state": trade_lock_state(lock, owned=owned_by_system),
                    "can_trade": can_trade,
                    "lock": None if lock is None else lock.to_dict(),
                }
            )
        return SystemCommandResult.accepted(command, {"account": account_filter, "accounts": rows, "count": len(rows)})

    def _trade_acquire(self, command: SystemCommand) -> SystemCommandResult:
        try:
            workspace = KairosWorkspace.resolve(self.directory)
            account = _require_workspace_account(workspace, command.payload.get("account"))
        except Exception as error:
            return SystemCommandResult.rejected(command, str(error))
        system_instance_id = _system_instance_id(self.directory)
        if system_instance_id is None:
            return SystemCommandResult.rejected(command, "system instance id is unavailable")
        if not _tradable_books(account):
            return SystemCommandResult.accepted(
                command,
                {
                    "account": account.account_id,
                    "account_key": account.account_key,
                    "trade_state": "untradable",
                    "acquired": False,
                    "lock": None,
                },
            )
        try:
            lock = workspace.account_locks.acquire(
                account.identity,
                environment=account.environment,
                launch_id=SYSTEM_LAUNCH_ID,
                launch_instance_id=system_instance_id,
                mode="system",
            )
        except Exception:
            existing = workspace.account_locks.get(account.account_key)
            return SystemCommandResult.accepted(
                command,
                {
                    "account": account.account_id,
                    "account_key": account.account_key,
                    "trade_state": trade_lock_state(existing, owned=False),
                    "acquired": False,
                    "lock": None if existing is None else existing.to_dict(),
                },
            )
        return SystemCommandResult.accepted(
            command,
            {
                "account": account.account_id,
                "account_key": account.account_key,
                "trade_state": "owned",
                "acquired": True,
                "lock": lock.record.to_dict(),
            },
        )

    def _trade_release(self, command: SystemCommand) -> SystemCommandResult:
        try:
            workspace = KairosWorkspace.resolve(self.directory)
            account = _require_workspace_account(workspace, command.payload.get("account"))
        except Exception as error:
            return SystemCommandResult.rejected(command, str(error))
        system_instance_id = _system_instance_id(self.directory)
        if system_instance_id is None:
            return SystemCommandResult.rejected(command, "system instance id is unavailable")
        try:
            released = workspace.account_locks.release(account.account_key, launch_instance_id=system_instance_id)
        except Exception as error:
            return SystemCommandResult.rejected(command, str(error))
        return SystemCommandResult.accepted(
            command,
            {
                "account": account.account_id,
                "account_key": account.account_key,
                "released": released,
                "trade_state": "available" if released else trade_lock_state(workspace.account_locks.get(account.account_key), owned=False),
            },
        )

    def _order_query(self, command: SystemCommand) -> SystemCommandResult:
        order_id = _optional_text(command.payload.get("order_id"))
        if order_id is None:
            return SystemCommandResult.rejected(command, "order.status requires order_id")
        try:
            status = ExecutionOrderQueries(_ArtifactViewSource(self.directory)).status(order_id)
        except KeyError as error:
            return SystemCommandResult.rejected(command, str(error))
        return SystemCommandResult.accepted(
            command,
            {
                "account": _optional_text(command.payload.get("account")),
                "order_id": order_id,
                "status": status["status"],
                "order": status["order"],
            },
        )


class _ArtifactViewSource:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._payloads = {
            "account.current.artifact": _account_current_payload(directory),
            **_latest_timeline_view_payloads(directory),
        }

    def get(self, key: str, default: object = None) -> object:
        if key in self._payloads:
            return self._payloads[key]
        if key.startswith("account.current.") and "account.current.artifact" in self._payloads:
            return self._payloads["account.current.artifact"]
        return default

    def require(self, key: str) -> object:
        value = self.get(key, None)
        if value is not None:
            return value
        raise KeyError(f"view has no value: {key}")

    def envelopes(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                key: ViewEnvelope(
                    key=key,
                    schema_version="1",
                    owner="system",
                    payload=payload,
                )
                for key, payload in self._payloads.items()
            }
        )


def _account_current_payload(directory: Path) -> Mapping[str, object]:
    path = directory / "account" / "current.json"
    payload = _read_json(path)
    view = payload.get("account_view")
    if isinstance(view, Mapping):
        return dict(view)
    if payload:
        return payload
    return {}


def _latest_timeline_view_payloads(directory: Path) -> dict[str, object]:
    path = directory / "timeline.jsonl"
    for row in reversed(_read_jsonl(path)):
        views = row.get("views")
        if not isinstance(views, Mapping):
            continue
        payloads: dict[str, object] = {}
        for key, envelope in views.items():
            if not isinstance(key, str) or not isinstance(envelope, Mapping):
                continue
            payload = envelope.get("payload")
            if isinstance(payload, Mapping):
                payloads[key] = dict(payload)
            elif payload is not None:
                payloads[key] = payload
        if payloads:
            return payloads
    return {}


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, Mapping) else {}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            rows.append(dict(value))
    return rows


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tradable_books(account: object) -> list[str]:
    books = []
    for book in tuple(getattr(account, "books", ()) or ()):
        ref = book.to_ref(getattr(account, "identity"))
        if _trade_authorization(account, book).allowed:
            books.append(str(ref.book))
    return books


def _trade_authorization(
    account: object,
    book: object,
    *,
    lock: object | None = None,
    owned_by_system: bool = False,
) -> AccountTradeAuthorizationResult:
    ref = book.to_ref(getattr(account, "identity"))
    return AccountAuthorizationService(str(ref.broker)).authorize_trade(
        AccountTradeAuthorizationRequest(
            ref,
            has_trade_credential=_account_has_trade_credential(account),
            lock=lock,
            lock_owned=owned_by_system,
        )
    )


def _account_has_trade_credential(account: object) -> bool:
    credentials = tuple(getattr(account, "credentials", ()) or ())
    if credentials:
        return any(getattr(credential, "role", "") == "trade" and getattr(credential, "ref", None) for credential in credentials)
    if getattr(account, "credential", None) or getattr(account, "credential_values", None):
        return True
    environment = str(getattr(account, "environment", "")).strip().lower()
    if environment in {"live", "testnet"}:
        return False
    return True


def _system_instance_id(directory: Path) -> str | None:
    payload = _read_json(directory / "state.json")
    value = payload.get("launch_instance_id")
    return value if isinstance(value, str) and value.strip() else None


def _require_workspace_account(workspace: KairosWorkspace, value: object) -> object:
    account = _optional_text(value)
    if account is None:
        raise ValueError("account is required")
    for record in workspace.accounts.list():
        if account in {record.account_id, record.account_key}:
            return record
    raise ValueError(f"unknown account: {account}")


__all__ = ["SystemCommandDispatcher"]

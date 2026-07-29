from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping

from kairospy.application.system.facade.resources import DriverName, ExchangeName, broker
from kairospy.application.system.workspace import AccountRecord, KairosWorkspace
from kairospy.config import ConfigError


class OrderFacade:
    def open_orders(
        self,
        *,
        account_id: str,
        symbol: str | None,
        limit: int | None,
        params: Mapping[str, object] | None,
    ) -> dict[str, object]:
        workspace, account = _account(account_id)
        rows = tuple(self._broker(account).fetch_open_orders(symbol, limit=limit, params=params))
        payload = {"account": account.account_id, "orders": rows, "count": len(rows)}
        _write_journal(workspace, account, "open", {"symbol": symbol, "limit": limit}, payload)
        return payload

    def history(
        self,
        *,
        account_id: str,
        symbol: str | None,
        since: str | None,
        limit: int | None,
        params: Mapping[str, object] | None,
    ) -> dict[str, object]:
        workspace, account = _account(account_id)
        rows = tuple(self._broker(account).fetch_closed_orders(symbol, since=since, limit=limit, params=params))
        payload = {"account": account.account_id, "orders": rows, "count": len(rows)}
        _write_journal(workspace, account, "history", {"symbol": symbol, "since": since, "limit": limit}, payload)
        return payload

    def place(
        self,
        *,
        account_id: str,
        symbol: str,
        side: str,
        type_: str,
        amount: str,
        price: str | None,
        params: Mapping[str, object],
        submit: bool,
        confirm_live: bool,
    ) -> dict[str, object]:
        workspace, account = _account(account_id)
        request = {"account": account.account_id, "symbol": symbol, "side": side, "type": type_, "amount": amount, "price": price, "params": params}
        if not submit:
            payload = {"dry_run": True, "request": request}
            _write_journal(workspace, account, "place_dry_run", request, payload)
            return payload
        _require_live_confirmation(account, confirm_live=confirm_live)
        result = self._broker(account).create_order(symbol, side=side, type=type_, amount=amount, price=price, params=params)
        payload = {"dry_run": False, "request": request, "result": result}
        _write_journal(workspace, account, "place", request, payload)
        return payload

    def cancel(
        self,
        *,
        account_id: str,
        order_id: str,
        symbol: str | None,
        params: Mapping[str, object],
        submit: bool,
        confirm_live: bool,
    ) -> dict[str, object]:
        workspace, account = _account(account_id)
        request = {"account": account.account_id, "order_id": order_id, "symbol": symbol, "params": params}
        if not submit:
            payload = {"dry_run": True, "request": request}
            _write_journal(workspace, account, "cancel_dry_run", request, payload)
            return payload
        _require_live_confirmation(account, confirm_live=confirm_live)
        result = self._broker(account).cancel_order(order_id, symbol=symbol, params=params)
        payload = {"dry_run": False, "request": request, "result": result}
        _write_journal(workspace, account, "cancel", request, payload)
        return payload

    def replace(
        self,
        *,
        account_id: str,
        order_id: str,
        symbol: str,
        side: str,
        type_: str,
        amount: str,
        price: str | None,
        params: Mapping[str, object],
        submit: bool,
        confirm_live: bool,
    ) -> dict[str, object]:
        workspace, account = _account(account_id)
        request = {
            "account": account.account_id,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "type": type_,
            "amount": amount,
            "price": price,
            "params": params,
        }
        if not submit:
            payload = {"dry_run": True, "request": request}
            _write_journal(workspace, account, "replace_dry_run", request, payload)
            return payload
        _require_live_confirmation(account, confirm_live=confirm_live)
        client = self._broker(account)
        cancel_result = client.cancel_order(order_id, symbol=symbol, params=params)
        create_result = client.create_order(symbol, side=side, type=type_, amount=amount, price=price, params=params)
        payload = {"dry_run": False, "request": request, "result": {"cancel": cancel_result, "create": create_result}}
        _write_journal(workspace, account, "replace", request, payload)
        return payload

    def show(self, *, account_id: str, order_id: str) -> dict[str, object]:
        workspace, account = _account(account_id)
        journal = workspace.workspace_root / "orders" / "journals" / f"{account.account_id}.jsonl"
        matches = [row for row in _read_jsonl(journal) if _contains_order_id(row, order_id)]
        if not matches:
            raise ValueError(f"order was not found in local journal: {order_id}")
        return {"account": account.account_id, "order_id": order_id, "journal": str(journal), "records": matches, "count": len(matches)}

    def _broker(self, account: AccountRecord):
        return broker(_exchange(account), DriverName.ccxt, credential=account.credential)


def _account(account_id: str) -> tuple[KairosWorkspace, AccountRecord]:
    workspace = KairosWorkspace.resolve()
    try:
        return workspace, workspace.accounts.get(account_id)
    except ConfigError as error:
        raise ValueError(str(error)) from error


def _exchange(account: AccountRecord) -> ExchangeName:
    value = (account.venue or account.provider).strip().lower()
    try:
        return ExchangeName(value)
    except ValueError as error:
        raise ValueError(f"unsupported order account venue/provider: {value}") from error


def _require_live_confirmation(account: AccountRecord, *, confirm_live: bool) -> None:
    if account.environment == "live" and not confirm_live:
        raise ValueError("live order submission requires --confirm-live")


def _write_journal(
    workspace: KairosWorkspace,
    account: AccountRecord,
    action: str,
    request: Mapping[str, object],
    payload: Mapping[str, object],
) -> None:
    root = workspace.workspace_root / "orders" / "journals"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{account.account_id}.jsonl"
    event = {
        "event_time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "account": account.account_id,
        "request": request,
        "payload": _jsonable(payload),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True) + "\n")
    workspace.operations.append(
        f"order.{action}",
        target={"account": account.account_id},
        payload={"journal": path, "request": request, "dry_run": bool(payload.get("dry_run", False))},
    )


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
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _contains_order_id(value: object, order_id: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_order_id(item, order_id) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_order_id(item, order_id) for item in value)
    return isinstance(value, str) and value == order_id


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["OrderFacade"]

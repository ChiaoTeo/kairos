from __future__ import annotations

"""System-facing order administration adapter."""

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping

from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.application.support.launch.application.control.facade import LaunchApplication
from kairospy.application.usecases.execution.application.ports import OrderCommandResources
from kairospy.application.usecases.execution.application.results import OrderActionResult, OrderJournalResult, OrderQueryResult, OrderRuntimeResult
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderOptions, ConnectionOrderSubmissionRequest
from kairospy.application.usecases.workspace.domain.workspace import AccountRecord, KairosWorkspace
from kairospy.application.usecases.workspace.domain.config import ConfigError
from kairospy.domain.account import AccountBookKind, AccountBookRef
from kairospy.domain.order import OrderSide, OrderType


class OrderCommandApplication:
    def __init__(self, resources: OrderCommandResources, launches: LaunchApplication | None = None) -> None:
        self._resources = resources
        self._launches = launches or LaunchApplication()

    def open_orders(
        self,
        *,
        account_id: str,
        symbol: str | None,
        limit: int | None,
        params: Mapping[str, object] | None,
    ) -> OrderQueryResult:
        workspace, account = _account(account_id)
        rows = tuple(self._order_query_client(account).fetch_open_orders(symbol, limit=limit, params=params))
        result = OrderQueryResult(account.account_id, rows)
        _write_journal(workspace, account, "open", {"symbol": symbol, "limit": limit}, _result_payload(result))
        return result

    def history(
        self,
        *,
        account_id: str,
        symbol: str | None,
        since: str | None,
        limit: int | None,
        params: Mapping[str, object] | None,
    ) -> OrderQueryResult:
        workspace, account = _account(account_id)
        rows = tuple(self._order_query_client(account).fetch_closed_orders(symbol, since=since, limit=limit, params=params))
        result = OrderQueryResult(account.account_id, rows)
        _write_journal(workspace, account, "history", {"symbol": symbol, "since": since, "limit": limit}, _result_payload(result))
        return result

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
    ) -> OrderActionResult:
        workspace, account = _account(account_id)
        request = {"account": account.account_id, "symbol": symbol, "side": side, "type": type_, "amount": amount, "price": price, "params": params}
        if not submit:
            result = OrderActionResult(True, request)
            _write_journal(workspace, account, "place_dry_run", request, _result_payload(result))
            return result
        _require_live_confirmation(account, confirm_live=confirm_live)
        result = self._order_connection(account).submit(
            ConnectionOrderSubmissionRequest(
                account=_account_book_ref(account),
                symbol=symbol,
                side=OrderSide(side),
                order_type=OrderType(type_),
                quantity=Decimal(amount),
                limit_price=Decimal(price) if price is not None else None,
                options=_connection_options(params),
            )
        )
        action = OrderActionResult(False, request, asdict(result))
        _write_journal(workspace, account, "place", request, _result_payload(action))
        return action

    def submit_runtime(
        self,
        *,
        launch: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
        account_id: str | None,
        symbol: str,
        side: str,
        type_: str,
        amount: str,
        price: str | None,
        params: Mapping[str, object],
        wait: bool,
        timeout_seconds: float,
    ) -> OrderRuntimeResult:
        return OrderRuntimeResult(self._launches.submit_command(
            target=launch,
            root=root,
            launch_id=launch_id,
            mode=mode,
            kind="order.submit",
            payload={
                "account": account_id,
                "symbol": symbol,
                "side": side,
                "type": type_,
                "amount": amount,
                "price": price,
                "params": dict(params),
            },
            wait=wait,
            timeout_seconds=timeout_seconds,
        ))

    def cancel(
        self,
        *,
        account_id: str,
        order_id: str,
        symbol: str | None,
        params: Mapping[str, object],
        submit: bool,
        confirm_live: bool,
    ) -> OrderActionResult:
        workspace, account = _account(account_id)
        request = {"account": account.account_id, "order_id": order_id, "symbol": symbol, "params": params}
        if not submit:
            result = OrderActionResult(True, request)
            _write_journal(workspace, account, "cancel_dry_run", request, _result_payload(result))
            return result
        _require_live_confirmation(account, confirm_live=confirm_live)
        result = self._order_connection(account).cancel(
            ConnectionOrderCancelRequest(
                account=_account_book_ref(account),
                order_venue_id=order_id,
                symbol=symbol,
                options=_connection_options(params),
            )
        )
        action = OrderActionResult(False, request, asdict(result))
        _write_journal(workspace, account, "cancel", request, _result_payload(action))
        return action

    def cancel_runtime(
        self,
        *,
        launch: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
        account_id: str | None,
        order_id: str,
        symbol: str | None,
        params: Mapping[str, object],
        wait: bool,
        timeout_seconds: float,
    ) -> OrderRuntimeResult:
        return OrderRuntimeResult(self._launches.submit_command(
            target=launch,
            root=root,
            launch_id=launch_id,
            mode=mode,
            kind="order.cancel",
            payload={
                "account": account_id,
                "order_id": order_id,
                "symbol": symbol,
                "params": dict(params),
            },
            wait=wait,
            timeout_seconds=timeout_seconds,
        ))

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
    ) -> OrderActionResult:
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
            result = OrderActionResult(True, request)
            _write_journal(workspace, account, "replace_dry_run", request, _result_payload(result))
            return result
        _require_live_confirmation(account, confirm_live=confirm_live)
        connection = self._order_connection(account)
        cancel_result = connection.cancel(
            ConnectionOrderCancelRequest(
                account=_account_book_ref(account),
                order_venue_id=order_id,
                symbol=symbol,
                options=_connection_options(params),
            )
        )
        create_result = connection.submit(
            ConnectionOrderSubmissionRequest(
                account=_account_book_ref(account),
                symbol=symbol,
                side=OrderSide(side),
                order_type=OrderType(type_),
                quantity=Decimal(amount),
                limit_price=Decimal(price) if price is not None else None,
                options=_connection_options(params),
            )
        )
        result = OrderActionResult(False, request, {"cancel": asdict(cancel_result), "create": asdict(create_result)})
        _write_journal(workspace, account, "replace", request, _result_payload(result))
        return result

    def show(self, *, account_id: str, order_id: str) -> OrderJournalResult:
        workspace, account = _account(account_id)
        journal = workspace.workspace_root / "orders" / "journals" / f"{account.account_id}.jsonl"
        matches = [row for row in _read_jsonl(journal) if _contains_order_id(row, order_id)]
        if not matches:
            raise ValueError(f"order was not found in local journal: {order_id}")
        return OrderJournalResult(account.account_id, order_id, str(journal), tuple(matches))

    def status_runtime(
        self,
        *,
        launch: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
        account_id: str | None,
        order_id: str,
        wait: bool,
        timeout_seconds: float,
    ) -> OrderRuntimeResult:
        return OrderRuntimeResult(self._launches.submit_command(
            target=launch,
            root=root,
            launch_id=launch_id,
            mode=mode,
            kind="order.status",
            payload={"account": account_id, "order_id": order_id},
            wait=wait,
            timeout_seconds=timeout_seconds,
        ))

    def _order_query_client(self, account: AccountRecord):
        return self._resources.account_query_access(_account_book_ref(account), "ccxt", credential=_read_credential_ref(account))

    def _order_connection(self, account: AccountRecord):
        credential = _trade_credential_ref(account)
        if credential is None:
            raise ValueError(f"account {account.account_id} has no trade credential")
        return self._resources.execution_access(_account_book_ref(account), "ccxt", credential=credential)


def _connection_options(params: Mapping[str, object]) -> ConnectionOrderOptions | None:
    if not params:
        return None
    return ConnectionOrderOptions(
        time_in_force=_text_option(params, "time_in_force", "timeInForce"),
        reduce_only=_bool_option(params, "reduce_only", "reduceOnly"),
        post_only=_bool_option(params, "post_only", "postOnly"),
    )


def _result_payload(result: object) -> dict[str, object]:
    value = asdict(result) if hasattr(result, "__dataclass_fields__") else result
    return dict(value) if isinstance(value, Mapping) else {"result": value}


def _text_option(params: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = params.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _bool_option(params: Mapping[str, object], *keys: str) -> bool | None:
    for key in keys:
        if key not in params or params[key] is None:
            continue
        value = params[key]
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"invalid boolean order option: {value!r}")
    return None


def _account(account_id: str) -> tuple[KairosWorkspace, AccountRecord]:
    workspace = resolve_workspace()
    try:
        return workspace, workspace.accounts.get(account_id)
    except ConfigError as error:
        raise ValueError(str(error)) from error


def _account_book_ref(account: AccountRecord) -> AccountBookRef:
    return AccountBookRef(account.venue or account.broker, account.account_id, account.market or _first_account_book(account) or AccountBookKind.SPOT.value)


def _first_account_book(account: AccountRecord) -> str | None:
    if not account.books:
        return None
    value = account.books[0].kind or account.books[0].key
    return value or None


def _read_credential_ref(account: AccountRecord) -> str | None:
    for credential in account.credentials:
        if credential.role == "readonly" and credential.ref:
            return credential.ref
    for credential in account.credentials:
        if credential.ref:
            return credential.ref
    return account.credential


def _trade_credential_ref(account: AccountRecord) -> str | None:
    for credential in account.credentials:
        if credential.role == "trade" and credential.ref:
            return credential.ref
    return account.credential if not account.credentials else None


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


__all__ = ["OrderCommandApplication"]

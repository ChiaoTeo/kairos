"""Live account state queries exposed to command-line and other surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Protocol, Sequence

from kairospy.application.support.diagnostics.application import record_exception
from kairospy.application.support.query.pagination import PageRequest, paginate
from kairospy.application.usecases.account.application.configuration import AccountConfigurationError, AccountRecord, AccountStore
from kairospy.application.usecases.account.application.ports import AccountCommandResources
from kairospy.application.usecases.account.application.results import (
    AccountBalanceError,
    AccountBalanceResult,
    AccountBalanceRow,
    AccountOpenOrdersResult,
    AccountPositionRow,
    AccountPositionsResult,
    AccountSnapshotResult,
    AccountSnapshotSummary,
)
from kairospy.application.usecases.account.application.runtime import account_segment_route
from kairospy.application.usecases.account.application.serialization import snapshot_payload
from kairospy.application.usecases.account.services.configuration import AccountConfigurationWriter
from kairospy.application.usecases.market.application.commands import DriverName
from kairospy.application.usecases.account.protocol import AccountReadRequest
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.domain.account import (
    AccountRuntimeContext,
    AccountSegment,
    AccountSnapshot,
    AccountBalance,
    AccountSource,
    ExternalAccountIdentity,
    Environment,
    ProductFamily,
    account_segment_from_name,
)


class BalanceProgress(Protocol):
    def __call__(self, event: dict[str, object]) -> None:
        ...


class AccountLiveQueryApplication:
    """Read remote account state through the injected account reader port."""

    def __init__(self, resources: AccountCommandResources) -> None:
        self._resources = resources
        self._configuration = AccountConfigurationWriter()

    def balance(
        self,
        account_id: str,
        *,
        segments: Sequence[str] | None,
        include_zero: bool,
        page: int,
        page_size: int,
        progress: BalanceProgress | None = None,
    ) -> AccountBalanceResult:
        account = _account(account_id)
        selected_segments = _balance_segments(account, segments)
        snapshots: dict[str, AccountSnapshot] = {}
        errors: list[AccountBalanceError] = []
        rows: list[AccountBalanceRow] = []
        if progress is not None:
            progress({"event": "start", "account": account.account_id, "segments": list(selected_segments), "total": len(selected_segments)})
        for index, segment in enumerate(selected_segments, start=1):
            segment_ref = _account_segment_ref(account, segment)
            route = account_segment_route(segment_ref, broker=account.broker)
            if progress is not None:
                progress({"event": "segment_start", "segment": segment, "index": index, "total": len(selected_segments), "params": dict(route.balance_params)})
            started_at = time.monotonic()
            try:
                reader = self._resources.account_reader(segment_ref, DriverName.ccxt, credential=_read_credential_ref(account))
                snapshot = reader.read_account(
                    AccountReadRequest(
                        context=_account_context(account, segment),
                        observed_at=datetime.now(timezone.utc),
                        fetch_orders=False,
                    )
                )
            except Exception as error:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                diagnostic = record_exception(
                    error,
                    operation="account.balance.fetch_segment",
                    command="account balance",
                    context={
                        "account": account.account_id,
                        "broker": account.broker,
                        "venue": account.venue,
                        "segment": segment,
                        "params": dict(route.balance_params),
                        "duration_ms": elapsed_ms,
                    },
                )
                errors.append(
                    AccountBalanceError(
                        segment_ref,
                        str(error),
                        str(diagnostic["error_type"]),
                        elapsed_ms,
                        str(diagnostic["diagnostic_id"]),
                        Path(str(diagnostic["diagnostic_path"])),
                    )
                )
                if progress is not None:
                    progress({
                        "event": "segment_error",
                        "segment": segment,
                        "index": index,
                        "total": len(selected_segments),
                        "error": str(error),
                        "error_type": diagnostic["error_type"],
                        "diagnostic_id": diagnostic["diagnostic_id"],
                        "duration_ms": elapsed_ms,
                    })
                continue
            snapshots[segment] = snapshot
            segment_rows = _snapshot_balance_rows(account, segment=segment, snapshot=snapshot, include_zero=include_zero)
            rows.extend(segment_rows)
            if progress is not None:
                progress({"event": "segment_done", "segment": segment, "index": index, "total": len(selected_segments), "rows": len(segment_rows)})
        paged_rows, page_result = paginate(rows, PageRequest(page=page, page_size=page_size))
        return AccountBalanceResult(
            account.account_id,
            str(account.broker),
            tuple(_account_segment_ref(account, segment) for segment in selected_segments),
            tuple(paged_rows),
            page_result,
            tuple(
                AccountSnapshotSummary(_account_segment_ref(account, segment), snapshot.observed_at, snapshot.source)
                for segment, snapshot in snapshots.items()
            ),
            tuple(errors),
        )

    def open_orders(
        self,
        account_id: str,
        *,
        symbol: str | None,
        limit: int | None,
    ) -> AccountOpenOrdersResult:
        account = _account(account_id)
        reader = self._resources.account_reader(_account_segment_ref(account), DriverName.ccxt, credential=_read_credential_ref(account))
        snapshot = reader.read_account(AccountReadRequest(_account_context(account, account.default_segment or "spot"), datetime.now(timezone.utc), symbol=symbol, fetch_orders=True))
        orders = snapshot.open_orders
        if limit is not None:
            orders = orders[:limit]
        return AccountOpenOrdersResult(account.account_id, tuple(orders), snapshot.observed_at)

    def positions(
        self,
        account_id: str,
        *,
        segments: Sequence[str] | None,
        symbol: str | None,
    ) -> AccountPositionsResult:
        account = _account(account_id)
        selected_segments = _balance_segments(account, segments)
        rows: list[AccountPositionRow] = []
        errors: list[AccountBalanceError] = []
        observed_at: datetime | None = None
        for segment in selected_segments:
            segment_ref = _account_segment_ref(account, segment)
            started_at = time.monotonic()
            try:
                reader = self._resources.account_reader(segment_ref, DriverName.ccxt, credential=_read_credential_ref(account))
                snapshot = reader.read_account(
                    AccountReadRequest(
                        context=_account_context(account, segment),
                        observed_at=datetime.now(timezone.utc),
                        symbol=symbol,
                        fetch_orders=False,
                    )
                )
                observed_at = snapshot.observed_at or observed_at
                rows.extend(AccountPositionRow(segment_ref, position) for position in snapshot.positions)
            except Exception as error:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                diagnostic = record_exception(
                    error,
                    operation="account.positions.fetch_segment",
                    command="account query positions",
                    context={"account": account.account_id, "broker": account.broker, "segment": segment},
                )
                errors.append(
                    AccountBalanceError(
                        segment_ref,
                        str(error),
                        str(diagnostic["error_type"]),
                        elapsed_ms,
                        str(diagnostic["diagnostic_id"]),
                        Path(str(diagnostic["diagnostic_path"])),
                    )
                )
        return AccountPositionsResult(account.account_id, str(account.broker), tuple(_account_segment_ref(account, segment) for segment in selected_segments), tuple(rows), observed_at, tuple(errors))

    def snapshot(self, account_id: str, *, symbol: str | None) -> AccountSnapshotResult:
        workspace = resolve_workspace()
        account = _account(account_id)
        reader = self._resources.account_reader(_account_segment_ref(account), DriverName.ccxt, credential=_read_credential_ref(account))
        snapshot = reader.read_account(AccountReadRequest(_account_context(account, account.default_segment or "spot"), datetime.now(timezone.utc), symbol=symbol, fetch_orders=True))
        payload = snapshot_payload(snapshot)
        path = workspace.workspace_root / "accounts" / "journals" / f"{account.account_id}.jsonl"
        self._configuration.append_jsonl(path, payload)
        workspace.operations.append("account.snapshot", target={"account": account.account_id}, payload={"journal": path})
        return AccountSnapshotResult(account.account_id, snapshot, path)


def _account(account_id: str) -> AccountRecord:
    try:
        return AccountStore.load(resolve_workspace().accounts_root).get(account_id)
    except AccountConfigurationError as error:
        raise ValueError(str(error)) from error


def _account_segment_ref(account: AccountRecord, segment: str | None = None) -> AccountSegment:
    selected_segment = segment or account.default_segment or _first_account_segment(account) or ProductFamily.SPOT.value
    return account_segment_from_name(ExternalAccountIdentity(account.broker, account.account_id), selected_segment)


def _account_context(account: AccountRecord, segment: str) -> AccountRuntimeContext:
    try:
        environment = Environment(account.environment)
    except ValueError as error:
        raise ValueError(f"unsupported account environment: {account.environment}") from error
    return AccountRuntimeContext(_account_segment_ref(account, segment), environment)


def _first_account_segment(account: AccountRecord) -> str | None:
    if not account.segments:
        return None
    segment = account.segments[0]
    return segment.product_family.value if segment.product_family is not None else segment.model.value


def _balance_segments(account: AccountRecord, segments: Sequence[str] | None) -> tuple[str, ...]:
    requested = tuple(segment.strip().lower().replace("-", "_") for segment in segments or () if segment.strip())
    if requested:
        return requested
    configured = tuple(segment.key for segment in account.segments)
    if account.remote_identity and configured:
        return configured
    return (account.default_segment or ProductFamily.SPOT.value,)


def _snapshot_balance_rows(account: AccountRecord, *, segment: str, snapshot: AccountSnapshot, include_zero: bool) -> list[AccountBalanceRow]:
    rows: list[AccountBalanceRow] = []
    for balance in snapshot.balances:
        if not include_zero and balance.total == 0 and balance.free == 0 and balance.locked == 0:
            continue
        rows.append(AccountBalanceRow(_account_segment_ref(account, segment), balance))
    return rows


def _read_credential_ref(account: AccountRecord) -> str | None:
    for credential in account.credentials:
        if credential.role == "readonly" and credential.ref:
            return credential.ref
    for credential in account.credentials:
        if credential.ref:
            return credential.ref
    return account.credential


__all__ = ["AccountLiveQueryApplication", "BalanceProgress"]

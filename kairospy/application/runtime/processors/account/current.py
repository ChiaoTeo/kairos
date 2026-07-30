from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.service.runtime import RuntimeAccountService
from kairospy.core.account import (
    AccountContext,
    AccountSnapshot,
    AccountState,
    AccountCurrentView,
    AccountDetailView,
    account_current_view_key,
    account_current_schema,
    account_detail_view_key,
    account_detail_schema,
)


class AccountCurrentViewState:
    def __init__(
        self,
        service: RuntimeAccountService,
        context: AccountContext,
        *,
        key: str | None = None,
        equity_currency: str | None = None,
        initial_equity: Decimal | str | int | float | None = None,
    ) -> None:
        self.service = service
        self.context = context
        self.key = key or account_current_view_key(context)
        self.equity_currency = equity_currency
        self._event_count = 0
        self._last_event: RuntimeEnvelope | None = None
        self._last_payload: object | None = None
        self._initial_equity = None if initial_equity is None else Decimal(str(initial_equity))
        self._last_equity: Decimal | None = None
        self.schema = account_current_schema(self.key)
        self.detail_key = account_detail_view_key(context)
        self.detail_schema = account_detail_schema(self.detail_key)

    def on_event(self, event: RuntimeEnvelope) -> None:
        if event.domain != "account":
            return
        payload = _account_payload(event.payload)
        if payload is None or _payload_context(payload) != self.context:
            return
        self._event_count += 1
        self._last_event = event
        self._last_payload = payload
        equity = _payload_equity(payload)
        if equity is not None:
            if self._initial_equity is None:
                self._initial_equity = equity
            self._last_equity = equity

    def view(self) -> AccountCurrentView:
        view = self.service.current_view(
            self.context,
            event_count=self._event_count,
            last_event_time=None if self._last_event is None else self._last_event.time,
            payload=self._last_payload,
            equity_currency=self.equity_currency,
            latest_equity=self._last_equity,
            initial_equity=self._initial_equity,
        )
        if self._initial_equity is None and view.initial_equity is not None:
            self._initial_equity = view.initial_equity
        return view

    def detail(self) -> AccountDetailView:
        return self.service.detail_view(
            self.context,
            event_count=self._event_count,
            last_event_time=None if self._last_event is None else self._last_event.time,
            metadata=None if self._last_event is None else dict(getattr(self._last_event, "metadata", {}) or {}),
        )


def _account_payload(payload: object) -> object | None:
    if isinstance(payload, (AccountState, AccountSnapshot)):
        return payload
    return payload if hasattr(payload, "context") else None


def _payload_context(payload: object) -> AccountContext | None:
    return getattr(payload, "context", None)


def _payload_equity(payload: object | None) -> Decimal | None:
    value = getattr(payload, "equity", None)
    return None if value is None else Decimal(str(value))


__all__ = ["AccountCurrentViewState"]

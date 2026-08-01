from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kairospy.application.launch import LaunchAccountBinding, LaunchAccountDirectory
from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import RuntimeAccountService
from kairospy.core.account import (
    ACCOUNT_BOOKS_SCHEMA,
    ACCOUNT_CAPABILITIES_SCHEMA,
    ACCOUNT_FEES_SCHEMA,
    AccountBalance,
    AccountBookKind,
    AccountBookSummary,
    AccountBooksView,
    AccountCapabilitiesView,
    AccountCapability,
    AccountContext,
    AccountCurrentView,
    AccountFeesView,
    AccountFeeSchedule,
    AccountPortfolioView,
    AccountBookRef,
    AccountSnapshot,
    AccountViewKeys,
    account_portfolio_schema,
)
from kairospy.core.views import ViewStore

from .current import AccountCurrentViewState


class AccountProcessor:
    def __init__(self, service: RuntimeAccountService) -> None:
        self.service = service
        self.directory = service.directory()
        self.states = tuple(AccountCurrentViewState(self.service, account) for account in self.directory.contexts())

    def on_event(self, event: RuntimeEnvelope) -> None:
        snapshot = _account_snapshot_event(event)
        if snapshot is not None:
            self.service.apply_snapshot(snapshot)
        for state in self.states:
            state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(ACCOUNT_BOOKS_SCHEMA.key) is None:
            views.register(ACCOUNT_BOOKS_SCHEMA)
        if views.registry.get(ACCOUNT_CAPABILITIES_SCHEMA.key) is None:
            views.register(ACCOUNT_CAPABILITIES_SCHEMA)
        if views.registry.get(ACCOUNT_FEES_SCHEMA.key) is None:
            views.register(ACCOUNT_FEES_SCHEMA)
        for state in self.states:
            if views.registry.get(state.schema.key) is None:
                views.register(state.schema)
            if views.registry.get(state.detail_schema.key) is None:
                views.register(state.detail_schema)
        for context in self._portfolio_contexts():
            key = AccountViewKeys.portfolio(context)
            if views.registry.get(key) is None:
                views.register(account_portfolio_schema(key))

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(AccountViewKeys.books, self._books_view(), as_of=as_of, available_time=as_of)
        views.put_runtime(AccountViewKeys.capabilities, self._capabilities_view(), as_of=as_of, available_time=as_of)
        views.put_runtime(AccountViewKeys.fees, self._fees_view(), as_of=as_of, available_time=as_of)
        current_views: list[AccountCurrentView] = []
        for state in self.states:
            current = state.view()
            current_views.append(current)
            views.put_runtime(state.key, current, as_of=as_of, available_time=as_of)
            views.put_runtime(state.detail_key, state.detail(), as_of=as_of, available_time=as_of)
        for portfolio in _portfolio_views(current_views):
            key = AccountViewKeys.portfolio(portfolio.books[0].context) if portfolio.books else ".".join((AccountViewKeys.portfolio_prefix, portfolio.account_key))
            views.put_runtime(key, portfolio, as_of=as_of, available_time=as_of)

    def _books_view(self) -> AccountBooksView:
        books = tuple(_book_summary(state.key, state.context, binding=self._binding_for(state.context)) for state in self.states)
        return AccountBooksView(total_count=len(books), books=books)

    def _capabilities_view(self) -> AccountCapabilitiesView:
        capabilities = self.service.capabilities()
        if not capabilities:
            capabilities = tuple(_default_capability(state.context.book) for state in self.states)
        return AccountCapabilitiesView(total_count=len(capabilities), capabilities=capabilities)

    def _fees_view(self) -> AccountFeesView:
        fees = self.service.fees()
        return AccountFeesView(total_count=len(fees), fees=fees)

    def _portfolio_contexts(self) -> tuple[AccountContext, ...]:
        contexts: dict[tuple[str, str, str], AccountContext] = {}
        for state in self.states:
            context = state.context
            contexts[(context.environment.value, str(context.identity.broker), str(context.identity.account_id))] = context
        return tuple(contexts.values())

    def _binding_for(self, context: AccountContext) -> LaunchAccountBinding:
        for binding in self.directory.bindings:
            if context in binding.books:
                return binding
        return LaunchAccountBinding(_account_key(context), 0, (context,))


def _book_summary(key: str, context: AccountContext, *, binding: LaunchAccountBinding) -> AccountBookSummary:
    book = context.book
    account_key = _account_key(context)
    return AccountBookSummary(
        key=key,
        alias=_default_alias(context),
        account_alias=binding.alias,
        account_index=binding.index,
        account_key=_account_key(context),
        book_key=_book_key(context),
        environment=str(context.environment.value),
        broker=str(book.broker),
        account_id=str(book.account_id),
        book_kind=str(book.book),
        book_qualifier=book.qualifier,
    )


def _default_alias(context: AccountContext) -> str:
    parts = [_account_key(context), _book_key(context)]
    return ".".join(part for part in parts if part)


def _account_key(context: AccountContext) -> str:
    book = context.book
    return ".".join(_key_part(part) for part in (book.broker, book.account_id) if part)


def _book_key(context: AccountContext) -> str:
    book = context.book
    return ".".join(_key_part(part) for part in book.book_key.split(":") if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


def _account_snapshot_event(event: RuntimeEnvelope) -> AccountSnapshot | None:
    if event.domain != "account":
        return None
    return event.payload if isinstance(event.payload, AccountSnapshot) else None


def _default_capability(book: AccountBookRef) -> AccountCapability:
    kind = str(book.book)
    can_trade = kind not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}
    can_hold_position = kind not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}
    can_borrow = kind in {AccountBookKind.CROSS_MARGIN.value, AccountBookKind.ISOLATED_MARGIN.value}
    return AccountCapability(
        book,
        can_trade=can_trade,
        can_hold_cash=True,
        can_hold_position=can_hold_position,
        can_borrow=can_borrow,
    )


def _portfolio_views(books: list[AccountCurrentView]) -> tuple[AccountPortfolioView, ...]:
    groups: dict[tuple[str, str, str, str], list[AccountCurrentView]] = {}
    for book in books:
        groups.setdefault(
            (
                book.context.environment.value,
                str(book.context.identity.broker),
                str(book.context.identity.account_id),
                _account_key(book.context),
            ),
            [],
        ).append(book)
    return tuple(_portfolio_view(key, items) for key, items in sorted(groups.items()))


def _portfolio_view(key: tuple[str, str, str, str], books: list[AccountCurrentView]) -> AccountPortfolioView:
    environment, broker, account_id, account_key = key
    balances = _aggregate_balances(tuple(balance for book in books for balance in book.balances))
    cash_values = tuple(book.cash for book in books if book.cash is not None)
    equity_values = tuple(book.equity for book in books if book.equity is not None)
    updated = tuple(book.last_event_time for book in books if book.last_event_time is not None)
    return AccountPortfolioView(
        account_key=account_key,
        environment=environment,
        broker=broker,
        account_id=account_id,
        books=tuple(books),
        balances=balances,
        margins=tuple(margin for book in books for margin in book.margins),
        liabilities=tuple(liability for book in books for liability in book.liabilities),
        positions=tuple(position for book in books for position in book.positions),
        open_orders=tuple(order for book in books for order in book.open_orders),
        cash=sum(cash_values, Decimal("0")) if cash_values else None,
        equity=sum(equity_values, Decimal("0")) if equity_values else None,
        stale=any(book.stale for book in books),
        updated_at=max(updated) if updated else None,
    )


def _aggregate_balances(balances: tuple[AccountBalance, ...]) -> tuple[AccountBalance, ...]:
    if not balances:
        return ()
    by_currency: dict[str, tuple[Decimal, Decimal, Decimal, object]] = {}
    for balance in balances:
        total, free, locked, source = by_currency.get(balance.currency, (Decimal("0"), Decimal("0"), Decimal("0"), balance.source))
        by_currency[balance.currency] = (
            total + balance.total,
            free + balance.free,
            locked + balance.locked,
            source,
        )
    return tuple(
        AccountBalance(currency, total, free, locked, source)  # type: ignore[arg-type]
        for currency, (total, free, locked, source) in sorted(by_currency.items())
    )


__all__ = ["AccountProcessor"]

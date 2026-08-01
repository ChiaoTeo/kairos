from __future__ import annotations

from kairospy.core.account import AccountBookKind, AccountBookRef, AccountContext, Environment


def test_account_book_ref_names_one_book_inside_account_identity() -> None:
    book = AccountBookRef("binance", "main", AccountBookKind.USD_M_FUTURES)

    assert book.identity.value == "binance:main"
    assert book.value == "binance:main:usd_m_futures"
    assert book.book_key == "usd_m_futures"


def test_account_context_exposes_book() -> None:
    book = AccountBookRef("binance", "main", "spot")
    context = AccountContext(book, Environment.LIVE)

    assert context.book is book
    assert context.identity == book.identity
    assert context.value == "live:binance:main:spot"

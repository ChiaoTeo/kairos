from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.actor.account.application.actor import AccountActor, RefreshAccountMarketProfileCommand
from kairospy.application.support.messaging import Message
from kairospy.infrastructure.messaging import InMemoryMessageBus
from kairospy.application.usecases.strategy.application.context import StrategyContext
from kairospy.domain.account import (
    ACCOUNT_BOOKS_SCHEMA,
    ACCOUNT_MARKET_PROFILES_SCHEMA,
    AccountBookKind,
    AccountBookRef,
    AccountBookSummary,
    AccountBooksView,
    AccountContext,
    AccountCurrentView,
    AccountFeeSchedule,
    AccountMarketProfile,
    AccountMarketProfilesView,
    AccountViewKeys,
    Environment,
    account_current_schema,
)
from kairospy.domain.reference import MarketRef


def test_strategy_can_navigate_account_book_market_and_read_effective_fee() -> None:
    account = AccountContext(AccountBookRef("binance", "main", AccountBookKind.USD_M_FUTURES), Environment.LIVE)
    market = MarketRef.ephemeral(venue="binance", market="usd_m_futures", source_symbol="BTC/USDT:USDT")
    fee = AccountFeeSchedule(account.book, Decimal("0.00036"), Decimal("0.00045"), market=market, currency="BNB")
    profile = AccountMarketProfile(account, market, fee=fee, source="test", observed_at=datetime.now(timezone.utc))
    views = ViewStore()
    views.register(ACCOUNT_BOOKS_SCHEMA)
    views.register(ACCOUNT_MARKET_PROFILES_SCHEMA)
    views.register(account_current_schema(AccountViewKeys.current(account)))
    views.put_runtime(
        AccountViewKeys.books,
        AccountBooksView(1, (AccountBookSummary(AccountViewKeys.current(account), "usd_m_futures", "main", 0, "binance.main", "usd_m_futures", "live", "binance", "main", "usd_m_futures"),)),
    )
    views.put_runtime(AccountViewKeys.current(account), AccountCurrentView(account, book=account.book))
    views.put_runtime(AccountViewKeys.market_profiles, AccountMarketProfilesView(1, (profile,)))
    context = StrategyContext("strategy", views=views)
    scope = context.accounts[0].book("usd_m_futures").market(market)
    assert scope.fee is not None
    assert scope.fee.taker == Decimal("0.00045")


def test_account_actor_refreshes_market_profile_and_publishes_fact() -> None:
    import asyncio

    async def scenario() -> None:
        account = AccountContext(AccountBookRef("binance", "main", AccountBookKind.USD_M_FUTURES), Environment.LIVE)
        market = MarketRef.ephemeral(venue="binance", market="usd_m_futures", source_symbol="BTC/USDT:USDT")
        profile = AccountMarketProfile(account, market, source="test", observed_at=datetime.now(timezone.utc))

        class Application:
            def market_profile(self, *_args, **_kwargs):
                return profile

            def update_market_profile(self, value):
                assert value is profile

        bus = InMemoryMessageBus()
        inbox = bus.open_inbox()
        actor = AccountActor(object(), bus, account_application=Application())
        await actor.start()
        await actor.handle(Message("account.market_profile.refresh", RefreshAccountMarketProfileCommand(account.book, market), datetime.now(timezone.utc), "test", 1))
        updated = await inbox.receive()
        assert updated.topic == "account.market_profile.updated"
        assert updated.payload.profile is profile
        await actor.stop()
        await inbox.close()
        await bus.close()

    asyncio.run(scenario())

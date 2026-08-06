from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.actor.account.application.actor import AccountActor, RefreshAccountMarketProfileCommand
from kairospy.application.support.messaging import Message
from kairospy.infrastructure.messaging import InMemoryMessageBus
from kairospy.application.usecases.strategy.application.context import StrategyContext
from kairospy.domain.account import (
    ACCOUNT_SCOPES_SCHEMA,
    ACCOUNT_MARKET_PROFILES_SCHEMA,
    AccountModel,
    ProductFamily,
    AccountSegment,
    AccountSegmentSummary,
    AccountSegmentsView,
    AccountRuntimeContext,
    AccountCurrentView,
    AccountFeeSchedule,
    AccountMarketProfile,
    AccountMarketProfilesView,
    AccountViewKeys,
    AccountViewReader,
    Environment,
    account_current_schema,
)
from kairospy.domain.reference import MarketRef


def test_strategy_can_navigate_account_segment_market_and_read_effective_fee() -> None:
    account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.LIVE)
    market = MarketRef.ephemeral(venue="binance", market="usd_m_futures", source_symbol="BTC/USDT:USDT")
    fee = AccountFeeSchedule(account.segment, Decimal("0.00036"), Decimal("0.00045"), market=market, currency="BNB")
    profile = AccountMarketProfile(account, market, fee=fee, source="test", observed_at=datetime.now(timezone.utc))
    views = ViewStore()
    views.register(ACCOUNT_SCOPES_SCHEMA)
    views.register(ACCOUNT_MARKET_PROFILES_SCHEMA)
    views.register(account_current_schema(AccountViewKeys.current(account)))
    views.put_runtime(
        AccountViewKeys.segments,
        AccountSegmentsView(1, (AccountSegmentSummary(AccountViewKeys.current(account), "usd_m_futures", "main", 0, "binance.main", "usd_m_futures", "live", "binance", "main", "usd_m_futures"),)),
    )
    views.put_runtime(AccountViewKeys.current(account), AccountCurrentView(account, segment=account.segment))
    views.put_runtime(AccountViewKeys.market_profiles, AccountMarketProfilesView(1, (profile,)))
    context = StrategyContext("strategy", views=views)
    segment = context.accounts[0].segment("usd_m_futures").market(market)
    assert segment.fee is not None
    assert segment.fee.taker == Decimal("0.00045")

    account_view = context.accounts.account("main").segment("usd_m_futures").view()
    assert account_view.segment == account.segment
    assert context.accounts.only().segment == account.segment


def test_account_reader_accepts_segment_id_for_composite_segment_key() -> None:
    account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.NO_MARGIN, ProductFamily.SPOT), Environment.LIVE)
    views = ViewStore()
    views.register(ACCOUNT_SCOPES_SCHEMA)
    views.register(account_current_schema(AccountViewKeys.current(account)))
    views.put_runtime(
        AccountViewKeys.segments,
        AccountSegmentsView(
            1,
            (AccountSegmentSummary(
                AccountViewKeys.current(account),
                "binance_main.spot.no_margin.spot",
                "main",
                0,
                "binance.main",
                "spot.no_margin.spot",
                "live",
                "binance",
                "main",
                "no_margin",
                "",
            ),),
        ),
    )
    views.put_runtime(AccountViewKeys.current(account), AccountCurrentView(account, segment=account.segment))

    view = AccountViewReader(views).account("binance.main").segment("spot").view()

    assert view.segment == account.segment


def test_account_actor_refreshes_market_profile_and_publishes_fact() -> None:
    import asyncio

    async def scenario() -> None:
        account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.LIVE)
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
        await actor.handle(Message("account.market_profile.refresh", RefreshAccountMarketProfileCommand(account.segment, market), datetime.now(timezone.utc), "test", 1))
        updated = await inbox.receive()
        assert updated.topic == "account.market_profile.updated"
        assert updated.payload.profile is profile
        await actor.stop()
        await inbox.close()
        await bus.close()

    asyncio.run(scenario())

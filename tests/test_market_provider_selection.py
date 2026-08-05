from kairospy.application.usecases.market.domain.planning import MarketStreamPlanningService
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef, ProviderId


def test_market_subscriptions_keep_provider_identity_separate() -> None:
    market = MarketRef.ephemeral(venue="nasdaq", market="spot", source_symbol="AAPL")
    massive = MarketDataSubscriptionSpec(market, (Quote,), provider=ProviderId("massive"))
    broker_feed = MarketDataSubscriptionSpec(market, (Quote,), provider=ProviderId("ibkr"))

    assert massive.key != broker_feed.key
    planner = MarketStreamPlanningService()
    massive_plan = planner.feed_watches(DataSubscription(massive.key, massive))[0]
    broker_plan = planner.feed_watches(DataSubscription(broker_feed.key, broker_feed))[0]
    assert massive_plan.params["provider"] == "massive"
    assert broker_plan.params["provider"] == "ibkr"


def test_historical_market_spec_keeps_provider_identity() -> None:
    spec = MarketDataSpec("AAPL", "quote", venue="nasdaq", market="equity", provider="massive")
    assert str(spec.provider) == "massive"

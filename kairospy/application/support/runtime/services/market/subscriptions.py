from __future__ import annotations

from kairospy.application.usecases.market.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.usecases.market.subscriptions import MarketSubscription


def data_subscription_from_market(subscription: MarketSubscription) -> DataSubscription:
    spec = MarketDataSubscriptionSpec(
        subscription.spec.market_ref,
        subscription.spec.selectors,
        identity=subscription.spec.identity,
        params=subscription.spec.params,
    )
    return DataSubscription(spec.key, spec)


__all__ = ["data_subscription_from_market"]

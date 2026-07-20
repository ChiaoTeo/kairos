from datetime import datetime, timezone

from kairospy.data import DatasetClient
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.data.bootstrap import register_default_products


register_default_products()
data = DatasetClient()
plan = data.plan(
    BTC_SPOT_DAILY.product,
    start=datetime(2025, 1, 1, tzinfo=timezone.utc),
    end=datetime(2025, 2, 1, tzinfo=timezone.utc),
    provider="binance",
    venue="binance",
)
print("complete:", plan.complete)
print("local release:", plan.local_release_id)
print("missing:", plan.missing)
print("selected source:", plan.selected)

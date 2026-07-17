from datetime import datetime, timezone

from trading.data import ResearchDataClient
from trading.data.products import BTC_SPOT_DAILY
from trading.data.bootstrap import register_default_products


register_default_products("data")
data = ResearchDataClient("data")
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

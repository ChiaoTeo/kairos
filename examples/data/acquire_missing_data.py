from kairospy.data import AcquirePolicy, OutputFormat, DatasetClient
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.data.bootstrap import default_provider_registry, register_default_products


register_default_products()
data = DatasetClient(providers=default_provider_registry())
frame = data.get(
    BTC_SPOT_DAILY.product,
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
    provider="binance",
    venue="binance",
    acquire=AcquirePolicy.IF_MISSING,
).collect(OutputFormat.POLARS)
print(frame)

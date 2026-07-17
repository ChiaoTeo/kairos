from trading.data import AcquirePolicy, OutputFormat, ResearchDataClient
from trading.data.products import BTC_SPOT_DAILY
from trading.data.bootstrap import default_provider_registry, register_default_products


register_default_products("data")
data = ResearchDataClient("data", providers=default_provider_registry("data"))
frame = data.get(
    BTC_SPOT_DAILY.product,
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
    provider="binance",
    venue="binance",
    acquire=AcquirePolicy.IF_MISSING,
).collect(OutputFormat.POLARS)
print(frame)

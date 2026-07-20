from kairospy.data import OutputFormat, DatasetClient
from kairospy.data.products import BTC_SPOT_DAILY


data = DatasetClient("data")
frame = data.get(
    BTC_SPOT_DAILY.product,
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
    fields=("period_start", "open", "high", "low", "close", "volume"),
).collect(OutputFormat.POLARS)
print(frame)

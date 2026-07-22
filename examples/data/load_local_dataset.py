from kairospy.data import OutputFormat
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.surface.product import Data


data = Data().reader()
frame = data.get(
    BTC_SPOT_DAILY.product,
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
    fields=("period_start", "open", "high", "low", "close", "volume"),
).collect(OutputFormat.POLARS)
print(frame)

from datetime import datetime, timezone
from itertools import islice

from kairospy.data import DataView, RunMode
from kairospy.data.products import Datasets
from kairospy.product_surface import Data


# Workspace replay accepts Q2 data. Use RunMode.BACKTEST only after the exact
# Release has passed Q3 review and is approved_for_backtest.
data = Data().reader(run_mode=RunMode.BACKTEST)
feed = data.replay(
    Datasets.MARKET_EVENTS_OPTIONS_US_SPXW,
    datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc),
    datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc),
    event_types=("quote", "trade"),
    view=DataView.RAW_AS_RECEIVED,
)
print("release:", feed.release_id, "hash:", feed.content_hash)
for event in islice(feed, 10):
    print(event.available_time, event.record_type, event.instrument_id)

from kairospy import __version__
from kairospy.data import DatasetClient
from kairospy.data.products import BTC_SPOT_DAILY


data = DatasetClient("data")
query = data.get(
    BTC_SPOT_DAILY.product,
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
    fields=("period_start", "close"),
)
data.freeze_study(
    "data/studies/example/data_snapshot.json",
    "example",
    (query,),
    code_version=__version__,
)

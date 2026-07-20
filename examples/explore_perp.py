from kairos.data import DatasetClient, OutputFormat

client = DatasetClient("data")

query = client.get(
    "market.ohlcv.crypto.binance.usdm-perpetual.1h",
    start="2026-07-01T00:00:00+00:00",
    end="2026-07-19T00:00:00+00:00",
    fields=("period_start", "symbol", "instrument_id", "close", "volume"),
)

df = query.collect(OutputFormat.PANDAS)
print(df.head())
print(df.shape)

df = df.sort_values(["instrument_id", "period_start"])
df["ret_1h"] = df.groupby("instrument_id")["close"].pct_change()

top = (
    df.dropna(subset=["ret_1h"])
      .sort_values(["period_start", "ret_1h"], ascending=[True, False])
      .groupby("period_start", as_index=False)
      .head(10)
)

print(top[["period_start", "symbol", "ret_1h"]].tail(30))

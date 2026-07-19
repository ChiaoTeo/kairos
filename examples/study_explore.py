from trading.research import open_study

study = open_study("crypto-hourly-momentum", root="data", version="1.0.0")
print(study.describe())

df = study.client.get(
    study.workspace.input_release_id,
    start="2025-07-01T00:00:00+00:00",
    end="2026-07-19T00:00:00+00:00",
    fields=("period_start", "symbol", "instrument_id", "close", "volume"),
).collect("pandas")

print(df.head())
import pandas as pd

from trading.product_workflow import fixture_sma_bars


bars = fixture_sma_bars()

df = pd.DataFrame(
    {
        "instrument_id": [bar.instrument_id.value for bar in bars],
        "start": [bar.start for bar in bars],
        "end": [bar.end for bar in bars],
        "open": [float(bar.open) for bar in bars],
        "high": [float(bar.high) for bar in bars],
        "low": [float(bar.low) for bar in bars],
        "close": [float(bar.close) for bar in bars],
        "volume": [float(bar.volume) for bar in bars],
    }
)

print("前 5 行")
print(df.head().to_string(index=False))

print("\n后 5 行")
print(df.tail().to_string(index=False))

print("\n字段类型")
print(df.dtypes)

print("\n缺失值")
print(df.isna().sum())

print("\n重复时间")
print(df.duplicated(subset=["instrument_id", "end"]).sum())

print("\n价格统计")
print(df[["open", "high", "low", "close", "volume"]].describe())
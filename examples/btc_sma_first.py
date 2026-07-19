from trading.research import open_study


study = open_study(
    "btc-sma-first",
    root="example-output/first-research",
)

df = study.data.pandas()

print(df.head().to_string(index=False))
print(study.profile().as_dict())
from kairospy.study_platform import open_study


study = open_study(
    "btc-sma-first",
    root="example-output/first-study",
)

df = study.data.pandas()

print(df.head().to_string(index=False))
print(study.profile().as_dict())
from kairos import BacktestRequest, BacktestRunner


def main() -> None:
    request = BacktestRequest(
        strategy="sma-cross-v1",
        dataset="fixture:sma-bars-v1",
        parameters={"fast": 5, "slow": 20},
        artifact_root="data/backtests",
    )
    result = BacktestRunner(lake_root="data").run(request)
    print(result.summary())


if __name__ == "__main__":
    main()

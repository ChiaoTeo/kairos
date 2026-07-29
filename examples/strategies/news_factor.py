from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

from kairospy import RunEnvironment, ensure_run_registered
from kairospy.application.strategy import StrategyBase
from kairospy.core.intent import target_position_intent


class NewsFactorStrategy(StrategyBase):
    strategy_id = "news-factor"

    def __init__(
        self,
        *,
        threshold: Decimal,
        instrument_id: str,
        market_id: str,
        target_quantity: Decimal,
    ) -> None:
        self.threshold = threshold
        self.instrument_id = instrument_id
        self.market_id = market_id
        self.target_quantity = target_quantity
        self.in_position = False

    def on_data(self, context, signal) -> None:
        if signal.kind != "news.sentiment":
            return None
        observation = signal.payload
        sentiment = Decimal(str(observation.value["sentiment"]))
        target = self.target_quantity if sentiment >= self.threshold else Decimal("0")
        if (target > 0) == self.in_position:
            return None
        self.in_position = target > 0
        context.intent(
            target_position_intent(
                strategy_id=self.strategy_id,
                instrument_id=self.instrument_id,
                market_id=self.market_id,
                target_quantity=target,
                at=signal.time,
                reason=f"news.sentiment={sentiment}",
            )
        )
        return None


def strategy(env: RunEnvironment) -> NewsFactorStrategy:
    return NewsFactorStrategy(
        threshold=Decimal(str(env.params["sentiment_threshold"])),
        instrument_id=str(env.params["instrument_id"]),
        market_id=str(env.params["market_id"]),
        target_quantity=Decimal(str(env.params["target_quantity"])),
    )


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "news-factor-backtest"
    if target == "news-factor-backtest":
        config_path = Path(__file__).resolve().parents[1] / "configs" / "news_factor_backtest.toml"
        ensure_run_registered(target, config_path)
    env = RunEnvironment.open(target)
    news = env.sources.csv_events(
        env.params["news_path"],
        source_id="example-news",
        domain="data",
        kind="news.sentiment",
        time_field="available_at",
        observed_at_field="observed_at",
        available_at_field="available_at",
        subject_type="instrument",
        subject_id_field="symbol",
        metadata_fields=("provider",),
    )
    result = env.run(strategy=strategy(env), sources=[news], echo=True)
    print(f"run_id={result.run_id}")
    print(f"strategy_id={result.runtime.strategy_id}")
    print(f"events={result.runtime.event_count}")
    print(f"intents={result.runtime.intent_count}")
    print(f"artifacts={env.instance_dir}")


if __name__ == "__main__":
    main()

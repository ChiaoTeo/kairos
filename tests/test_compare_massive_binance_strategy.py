from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from kairospy.domain.market import MarketEvent, MarketSubject, Quote


_STRATEGY_PATH = Path(__file__).parents[1] / "examples/strategies/compare_massive_binance_aapl.py"
_SPEC = importlib.util.spec_from_file_location("compare_massive_binance_aapl", _STRATEGY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
CompareMassiveBinanceAaplStrategy = _MODULE.CompareMassiveBinanceAaplStrategy


def _signal(source: str, midpoint: str) -> Signal:
    now = datetime.now(timezone.utc)
    quote = Quote(
        instrument_id=f"{source}:equity:aapl",
        market_id=f"{source}:equity:aapl",
        market_key=f"{source}_equity_aapl",
        time=now,
        bid=Decimal(midpoint) - Decimal("0.01"),
        ask=Decimal(midpoint) + Decimal("0.01"),
        source=source,
    )
    event = MarketEvent(
        subject=MarketSubject("market", quote.market_id),
        observed_at=now,
        available_at=now,
        value=quote,
        source=source,
    )
    return SimpleNamespace(time=now, payload=event)


def test_strategy_waits_for_both_feeds_then_prints_spread(capsys) -> None:
    strategy = CompareMassiveBinanceAaplStrategy()
    strategy.on_data(None, _signal("massive", "180.50"))
    assert capsys.readouterr().out == ""

    strategy.on_data(None, _signal("binance", "180.00"))
    output = capsys.readouterr().out
    assert "massive_mid=180.50" in output
    assert "binance_mid=180.00" in output
    assert "spread(massive-binance)=0.50" in output

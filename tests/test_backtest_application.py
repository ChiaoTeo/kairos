from __future__ import annotations

from kairospy.application import backtest as backtest_module


class FakeAccount:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self._snapshot = {"accounts": [{"equity": "100"}]}

    def apply_simulated_fill(self, fill):
        self.calls.append(("fill", fill["occurred_at_unix_nanos"]))
        return {"status": "applied"}

    def mark_to_market(self, update):
        self.calls.append(("mark", update["observed_at_unix_nanos"]))
        return {"status": "applied"}

    def account_state(self):
        return self._snapshot


def test_run_backtest_preserves_fill_before_mark_time_order(monkeypatch) -> None:
    monkeypatch.setattr(
        backtest_module,
        "backtest_run",
        lambda _socket, _request: {
            "fills": [
                {
                    "fill_id": "fill-1",
                    "order_id": "order-1",
                    "instrument_id": "BTCUSDT",
                    "side": "Buy",
                    "quantity": "1",
                    "price": "100",
                    "fee": "0",
                    "occurred_at_unix_nanos": 2,
                }
            ],
            "orders": [],
        },
    )
    account = FakeAccount()
    result = backtest_module.run_backtest(
        "execution.sock",
        account,
        {
            "market_events": [
                {
                    "Quote": {
                        "instrument_id": "BTCUSDT",
                        "bid_price": "99",
                        "ask_price": "101",
                        "observed_at_unix_nanos": 1,
                    }
                },
                {
                    "Quote": {
                        "instrument_id": "BTCUSDT",
                        "bid_price": "109",
                        "ask_price": "111",
                        "observed_at_unix_nanos": 2,
                    }
                },
            ]
        },
    )
    assert account.calls == [("mark", 1), ("fill", 2), ("mark", 2)]
    assert len(result["equity_curve"]) == 2
    assert result["applied_fills"] == 1


def test_run_backtest_marks_account_from_a_replayed_bar(monkeypatch) -> None:
    monkeypatch.setattr(
        backtest_module,
        "backtest_run",
        lambda _socket, _request: {"fills": [], "orders": []},
    )
    account = FakeAccount()
    result = backtest_module.run_backtest(
        "execution.sock",
        account,
        {
            "market_events": [
                {
                    "Bar": {
                        "instrument_id": "instrument:equity:US:AAPL:common",
                        "close": "100.5",
                        "observed_at_unix_nanos": 10,
                    }
                }
            ]
        },
    )
    assert account.calls == [("mark", 10)]
    assert len(result["equity_curve"]) == 1

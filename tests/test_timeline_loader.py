from __future__ import annotations

import json

from kairospy.surface.timeline.loader import TimelineDataLoader


def test_timeline_loader_derives_records_from_sampled_views(tmp_path) -> None:
    instance = tmp_path / ".kairos" / "runs" / "backtest" / "bt-1" / "instances" / "i-1"
    instance.mkdir(parents=True)
    _write_jsonl(
        instance / "timeline.jsonl",
        [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "sequence": 1,
                "trigger": "interval",
                "event": {"domain": "market", "kind": "bar", "summary": "bar btc"},
                "context_hash": "hash-1",
                "views": {
                    "account.equity_curve": {
                        "schema_version": "1",
                        "payload_hash": "equity-1",
                        "payload": {
                            "points": [
                                {
                                    "time": "2026-01-01T00:00:00+00:00",
                                    "equity": "1000",
                                    "cash": "1000",
                                    "positions": [],
                                }
                            ]
                        },
                    },
                    "account.risk_snapshots": {
                        "schema_version": "1",
                        "payload_hash": "risk-1",
                        "payload": {
                            "snapshots": [
                                {
                                    "time": "2026-01-01T00:00:00+00:00",
                                    "account_id": "main",
                                    "cash": "1000",
                                    "equity": "1000",
                                    "gross_notional": "0",
                                    "net_notional": "0",
                                    "positions": [],
                                    "funding_rates": [],
                                }
                            ]
                        },
                    },
                    "strategy.decision_trace": {
                        "schema_version": "1",
                        "payload_hash": "decision-1",
                        "payload": {
                            "records": [
                                {
                                    "time": "2026-01-01T00:00:00+00:00",
                                    "strategy_id": "s",
                                    "name": "decision",
                                    "payload": {"score": "1"},
                                    "intent_ids": ["intent-1"],
                                }
                            ]
                        },
                    },
                    "intent.journal": {
                        "schema_version": "1",
                        "payload_hash": "intent-1",
                        "payload": {
                            "states": [
                                {
                                    "intent_id": "intent-1",
                                    "instrument_id": "instrument:spot:btc:usdt",
                                    "status": "active",
                                    "active": True,
                                }
                            ]
                        },
                    },
                    "market.bars": {
                        "schema_version": "1",
                        "payload_hash": "bars-1",
                        "payload": {
                            "bars": [
                                {
                                    "market_id": "market:binance:spot:btc_usdt",
                                    "instrument_id": "instrument:spot:btc:usdt",
                                    "market_key": "binance_spot_btc_usdt",
                                    "time": "2026-01-01T00:00:00+00:00",
                                    "timeframe": "1m",
                                    "open": "100",
                                    "high": "102",
                                    "low": "99",
                                    "close": "101",
                                    "volume": "10",
                                }
                            ]
                        },
                    },
                },
            }
        ],
    )

    data = TimelineDataLoader(instance).load()

    assert data["instance"]["counts"]["timelineRecords"] == 1
    assert data["instance"]["counts"]["equity"] == 1
    assert data["records"]["equity"][0]["equity"] == "1000"
    assert data["records"]["riskSnapshots"][0]["account_id"] == "main"
    assert data["records"]["decisionTrace"][0]["name"] == "decision"
    assert data["records"]["intents"][0]["intent_id"] == "intent-1"
    assert data["records"]["trades"][0]["source"] == "ohlcv"
    assert data["series"]["equity"][0]["cash"] == "1000"
    assert data["series"]["risk"][0]["positionCount"] == 0
    assert data["series"]["markets"][0]["close"] == "101"


def test_timeline_loader_derives_market_views_and_multi_account_equity(tmp_path) -> None:
    instance = tmp_path / ".kairos" / "runs" / "backtest" / "bt-2" / "instances" / "i-1"
    instance.mkdir(parents=True)
    _write_jsonl(
        instance / "timeline.jsonl",
        [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "sequence": 2,
                "trigger": "interval",
                "event": {"domain": "market", "kind": "quote", "summary": "quote btc"},
                "context_hash": "hash-2",
                "views": {
                    "account.current.paper.binance.main": {
                        "schema_version": "1",
                        "payload_hash": "account-main",
                        "payload": {
                            "last_event_time": "2026-01-01T00:01:00+00:00",
                            "equity": "1000",
                            "cash": "900",
                            "positions": [],
                        },
                    },
                    "account.current.paper.okx.alt": {
                        "schema_version": "1",
                        "payload_hash": "account-alt",
                        "payload": {
                            "last_event_time": "2026-01-01T00:01:00+00:00",
                            "equity": "500",
                            "cash": "500",
                            "positions": [],
                        },
                    },
                    "market.quotes": {
                        "schema_version": "1",
                        "payload_hash": "quote-1",
                        "payload": {
                            "quotes": [
                                {
                                    "market_id": "market:binance:spot:btc_usdt",
                                    "instrument_id": "instrument:spot:btc:usdt",
                                    "market_key": "binance_spot_btc_usdt",
                                    "time": "2026-01-01T00:01:00+00:00",
                                    "bid": "100",
                                    "ask": "102",
                                }
                            ]
                        },
                    },
                    "market.trades": {
                        "schema_version": "1",
                        "payload_hash": "trade-1",
                        "payload": {
                            "trades": [
                                {
                                    "market_id": "market:binance:spot:btc_usdt",
                                    "instrument_id": "instrument:spot:btc:usdt",
                                    "market_key": "binance_spot_btc_usdt",
                                    "time": "2026-01-01T00:01:00+00:00",
                                    "price": "101",
                                    "size": "0.5",
                                }
                            ]
                        },
                    },
                    "market.rates": {
                        "schema_version": "1",
                        "payload_hash": "rate-1",
                        "payload": {
                            "rates": [
                                {
                                    "rate_id": "funding:btc",
                                    "market_id": "market:binance:swap:btc_usdt",
                                    "time": "2026-01-01T00:01:00+00:00",
                                    "rate": "0.0001",
                                    "mark_price": "101",
                                }
                            ]
                        },
                    },
                },
            }
        ],
    )

    data = TimelineDataLoader(instance).load()

    assert len(data["records"]["equity"]) == 2
    assert {row["equity"] for row in data["records"]["equity"]} == {"1000", "500"}
    assert {row["source"] for row in data["records"]["trades"]} == {"quote", "trade", "rate"}
    market_series = data["series"]["markets"]
    assert any(row["key"] == "price:binance_spot_btc_usdt" and row["value"] == "101.0" for row in market_series)
    assert any(row["key"] == "trade:binance_spot_btc_usdt" and row["value"] == "101" for row in market_series)
    assert any(row["key"] == "rate:market:binance:swap:btc_usdt" and row["rate"] == "0.0001" for row in market_series)


def _write_jsonl(path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")

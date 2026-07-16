from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from trading.adapters.binance.historical_archive import BinanceSpotArchiveProvider
from trading.adapters.deribit.historical import DeribitDvolProvider
from trading.adapters.binance.options_archive import BinanceOptionsEohArchiveProvider, normalize_eoh_rows
from trading.adapters.deribit.trade_history import DeribitOptionTradeHistoryProvider, normalize_deribit_trades
from trading.adapters.deribit.option_chain import DeribitOptionChainProvider
from trading.storage.data_lake import utc_midnight, write_daily_dataset, write_intraday_dataset, write_event_dataset, append_snapshot_dataset, write_json, sha256_bytes
import json

from .catalog import DataCatalog
from .capabilities import capabilities_payload


class BtcOptionsDataPipeline:
    def __init__(self, root: str | Path = "data") -> None:
        self.root, self.catalog = Path(root), DataCatalog(root)

    def prepare(self, start: date, end: date) -> tuple[dict[str, object], dict[str, object]]:
        source = self.root / "source"
        spot = BinanceSpotArchiveProvider().fetch_daily("BTCUSDT", start, end, source)
        dvol = DeribitDvolProvider().fetch_daily("BTC", start, end, source)
        if not spot or not dvol:
            raise RuntimeError("market data provider returned an empty dataset")
        receipts = sorted(str(item.relative_to(self.root)) for item in source.rglob("receipt.json"))
        requested = {"start": utc_midnight(start), "end": utc_midnight(end + timedelta(days=1)), "boundary": "[start,end)"}
        spot_lineage = _lineage(DataCatalog.BTC_SPOT_DAILY.dataset_id, "binance", "spot_klines", "binance_spot_kline_to_market_ohlcv", requested,
                                [p for p in receipts if "provider=binance" in p])
        dvol_lineage = _lineage(DataCatalog.BTC_DVOL_DAILY.dataset_id, "deribit", "volatility_index", "deribit_dvol_to_vendor_volatility_index", requested,
                                [p for p in receipts if "provider=deribit" in p])
        spot_manifest = write_daily_dataset(self.catalog.path(DataCatalog.BTC_SPOT_DAILY.dataset_id), _ohlcv(spot, "binance", "BTC-USDT"),
            dataset_id=DataCatalog.BTC_SPOT_DAILY.dataset_id, schema=_schema(DataCatalog.BTC_SPOT_DAILY.schema_id, True, "USDT_per_BTC"), lineage=spot_lineage,
            capabilities=capabilities_payload(DataCatalog.BTC_SPOT_DAILY.dataset_id))
        dvol_manifest = write_daily_dataset(self.catalog.path(DataCatalog.BTC_DVOL_DAILY.dataset_id), _ohlcv(dvol, "deribit", "BTC-DVOL"),
            dataset_id=DataCatalog.BTC_DVOL_DAILY.dataset_id, schema=_schema(DataCatalog.BTC_DVOL_DAILY.schema_id, False, "annualized_volatility_percent"), lineage=dvol_lineage,
            capabilities=capabilities_payload(DataCatalog.BTC_DVOL_DAILY.dataset_id))
        return spot_manifest, dvol_manifest

    def prepare_option_quotes(self, start: date, end: date) -> dict[str, object]:
        source = self.root / "source"
        rows = normalize_eoh_rows(BinanceOptionsEohArchiveProvider().fetch("BTCUSDT", start, end, source))
        if not rows:
            raise RuntimeError("Binance option EOHSummary returned no rows; public archive coverage starts 2023-05-18")
        receipts = sorted(str(item.relative_to(self.root)) for item in source.rglob("receipt.json") if "option_eoh_summary" in str(item))
        lineage = {"lineage_version": 1, "dataset_id": DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id,
                   "producer": {"name": "trading.data.pipeline", "transform": "binance_option_eoh_to_canonical_quotes", "version": 1},
                   "source": {"provider": "binance", "dataset": "option_eoh_summary", "transport": "public_archive", "authentication": "none"},
                   "request_window": {"start": utc_midnight(start), "end": utc_midnight(end + timedelta(days=1)), "boundary": "[start,end)"},
                   "source_receipts": receipts, "pricing_fields": {"implied_volatility": "vendor", "greeks": "vendor"}}
        schema = {"schema_id": DataCatalog.BTC_OPTION_QUOTES_HOURLY.schema_id, "schema_version": 1,
                  "time_boundary": "[period_start,period_end)", "primary_key": ["period_start", "instrument_id"],
                  "columns": {
                      "period_start": {"type": "datetime", "timezone": "UTC"}, "period_end": {"type": "datetime", "timezone": "UTC"},
                      "event_time": {"type": "datetime", "timezone": "UTC"}, "available_time": {"type": "datetime", "timezone": "UTC"},
                      "venue": {"type": "string"}, "underlying_id": {"type": "string"}, "instrument_id": {"type": "string"},
                      "expiry": {"type": "datetime", "timezone": "UTC"}, "option_right": {"type": "enum", "values": ["call", "put"]},
                      "strike": {"type": "number", "unit": "USDT_per_BTC"}, "best_bid_price": {"type": "nullable_number"},
                      "best_ask_price": {"type": "nullable_number"}, "bid_iv": {"type": "nullable_number", "unit": "absolute_volatility"},
                      "ask_iv": {"type": "nullable_number", "unit": "absolute_volatility"},
                      "mark_price": {"type": "number"}, "mark_iv": {"type": "number", "unit": "absolute_volatility"},
                      "vendor_delta": {"type": "number"}, "vendor_gamma": {"type": "number"}, "vendor_vega": {"type": "number"},
                      "vendor_theta": {"type": "number"}, "volume_contracts": {"type": "number"}, "open_interest_contracts": {"type": "number"}}}
        return write_intraday_dataset(self.catalog.path(DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id), rows,
                                      dataset_id=DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id, schema=schema,
                                      lineage=lineage, interval=timedelta(hours=1),
                                      capabilities=capabilities_payload(DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id))

    def prepare_deribit_option_trades(self, start: date, end: date) -> dict[str, object]:
        source = self.root / "source"
        rows = normalize_deribit_trades(DeribitOptionTradeHistoryProvider().fetch("BTC", start, end, source))
        if not rows:
            raise RuntimeError("Deribit historical API returned no BTC option trades")
        receipts = sorted(str(item.relative_to(self.root)) for item in source.rglob("receipt.json") if "dataset=option_trades" in str(item))
        lineage = {"lineage_version": 1, "dataset_id": DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id,
                   "producer": {"name": "trading.data.pipeline", "transform": "deribit_option_trade_to_canonical", "version": 1},
                   "source": {"provider": "deribit", "dataset": "historical_option_trades", "transport": "history_public_api", "authentication": "none"},
                   "request_window": {"start": utc_midnight(start), "end": utc_midnight(end+timedelta(days=1)), "boundary": "[start,end)"},
                   "source_receipts": receipts, "limitations": ["trades_only", "not_a_complete_quote_chain"]}
        schema = {"schema_id": DataCatalog.BTC_DERIBIT_OPTION_TRADES.schema_id, "schema_version": 1,
                  "primary_key": ["venue", "trade_id"], "columns": {
                      "event_time": {"type": "datetime", "timezone": "UTC"}, "available_time": {"type": "datetime", "timezone": "UTC"},
                      "venue": {"type": "string"}, "underlying_id": {"type": "string"}, "instrument_id": {"type": "string"},
                      "trade_id": {"type": "string"}, "expiry": {"type": "datetime", "timezone": "UTC"},
                      "option_right": {"type": "enum", "values": ["call", "put"]}, "strike": {"type": "number", "unit": "USD_per_BTC"},
                      "price_btc": {"type": "number", "unit": "BTC"}, "amount_btc": {"type": "number", "unit": "BTC"},
                      "direction": {"type": "enum", "values": ["buy", "sell"]},
                      "trade_iv": {"type": "number", "unit": "absolute_volatility"},
                      "mark_price_btc": {"type": "number", "unit": "BTC"}, "index_price_usd": {"type": "number", "unit": "USD_per_BTC"},
                      "tick_direction": {"type": "integer"}}}
        return write_event_dataset(self.catalog.path(DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id), rows,
                                   dataset_id=DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id, schema=schema, lineage=lineage,
                                   capabilities=capabilities_payload(DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id))

    def capture_deribit_option_chain(self) -> dict[str, object]:
        payload, rows = DeribitOptionChainProvider().snapshot("BTC")
        timestamp = rows[0]["period_start"].replace(":", "").replace("-", "")
        source = self.root/"source"/"provider=deribit"/"dataset=option_chain_summary"/f"snapshot={timestamp}"
        source.mkdir(parents=True, exist_ok=True); raw = source/"payload.json"
        raw.write_text(json.dumps(payload,separators=(",",":")),encoding="utf-8")
        content=raw.read_bytes(); write_json(source/"receipt.json",{"receipt_version":1,"provider":"deribit","dataset":"option_chain_summary",
            "requested_at":rows[0]["period_start"],"request":{"url":DeribitOptionChainProvider.url,"parameters":{"currency":"BTC","kind":"option"}},
            "response":{"status":200,"bytes":len(content),"sha256":sha256_bytes(content),"records":len(rows)},"authentication":"none"})
        lineage={"lineage_version":1,"dataset_id":DataCatalog.BTC_DERIBIT_OPTION_QUOTES.dataset_id,
            "producer":{"name":"trading.data.pipeline","transform":"deribit_book_summary_to_option_chain","version":1},
            "source":{"provider":"deribit","dataset":"get_book_summary_by_currency","authentication":"none"},
            "latest_source_payload":str(raw.relative_to(self.root)),"limitations":["summary_bid_ask_has_no_size","manual_or_scheduled_snapshots"]}
        schema={"schema_id":DataCatalog.BTC_DERIBIT_OPTION_QUOTES.schema_id,"schema_version":1,
            "primary_key":["period_start","instrument_id"],"columns":{
                "period_start":{"type":"datetime","timezone":"UTC"},"event_time":{"type":"datetime","timezone":"UTC"},
                "instrument_id":{"type":"string"},"expiry":{"type":"datetime","timezone":"UTC"},"option_right":{"type":"enum"},
                "strike":{"type":"number","unit":"USD_per_BTC"},"bid_price_btc":{"type":"nullable_number"},
                "ask_price_btc":{"type":"nullable_number"},"mark_iv":{"type":"number","unit":"absolute_volatility"},
                "underlying_price_usd":{"type":"number"},"open_interest":{"type":"number"}}}
        return append_snapshot_dataset(self.catalog.path(DataCatalog.BTC_DERIBIT_OPTION_QUOTES.dataset_id),rows,
            dataset_id=DataCatalog.BTC_DERIBIT_OPTION_QUOTES.dataset_id,schema=schema,lineage=lineage,
            capabilities=capabilities_payload(DataCatalog.BTC_DERIBIT_OPTION_QUOTES.dataset_id))


def _ohlcv(values, venue, instrument):
    return [{"period_start": utc_midnight(day), "period_end": utc_midnight(day + timedelta(days=1)),
             "event_time": utc_midnight(day + timedelta(days=1)), "available_time": utc_midnight(day + timedelta(days=1)),
             "venue": venue, "instrument_id": instrument, "interval": "P1D", "open": value["open"], "high": value["high"],
             "low": value["low"], "close": value["close"], "volume": value.get("volume", "")}
            for day in sorted(values) for value in [values[day]]]


def _lineage(dataset_id, provider, source_dataset, transform, window, receipts):
    return {"lineage_version": 1, "dataset_id": dataset_id, "producer": {"name": "trading.data.pipeline", "transform": transform, "version": 1},
            "source": {"provider": provider, "dataset": source_dataset, "authentication": "none"},
            "request_window": window, "source_receipts": receipts}


def _schema(schema_id, volume, unit):
    return {"schema_id": schema_id, "schema_version": 1, "time_boundary": "[period_start,period_end)",
            "primary_key": ["venue", "instrument_id", "period_start", "interval"], "columns": {
                "period_start": {"type": "datetime", "timezone": "UTC", "meaning": "inclusive bar start"},
                "period_end": {"type": "datetime", "timezone": "UTC", "meaning": "exclusive bar end"},
                "event_time": {"type": "datetime", "timezone": "UTC", "meaning": "bar completion time"},
                "available_time": {"type": "datetime", "timezone": "UTC", "meaning": "earliest backtest visibility"},
                "venue": {"type": "string"}, "instrument_id": {"type": "string"}, "interval": {"type": "duration"},
                "open": {"type": "number", "unit": unit}, "high": {"type": "number", "unit": unit},
                "low": {"type": "number", "unit": unit}, "close": {"type": "number", "unit": unit},
                "volume": {"type": "number" if volume else "nullable", "unit": "base_asset" if volume else "not_applicable"}}}

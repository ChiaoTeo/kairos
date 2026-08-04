from __future__ import annotations

from typing import Mapping, Literal

from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.data import MarketDataSpec
from .resources import DriverName, ExchangeName, MarketCommandResources


MarketDataMode = Literal["historical", "live"]


class MarketSourceQueryService:
    """Queries integration market capabilities without owning a connection."""

    def __init__(self, resources: MarketCommandResources) -> None:
        self._resources = resources

    def capabilities(
        self,
        *,
        exchange_name: ExchangeName | None = None,
        market: str | None = None,
        driver_name: DriverName | None = None,
    ) -> dict[str, object]:
        driver = driver_name or DriverName.ccxt
        venues = (exchange_name,) if exchange_name is not None else tuple(ExchangeName)
        markets = [
            _market_capability(venue.value, namespace, driver)
            for venue in venues
            for namespace in _candidate_markets(venue, market)
        ]
        return {"driver": driver.value, "markets": markets, "count": len(markets)}

    def check(
        self,
        *,
        symbol: str,
        exchange_name: ExchangeName,
        market: str,
        kind: str,
        data_mode: MarketDataMode,
        timeframe: str | None = None,
        driver_name: DriverName = DriverName.ccxt,
    ) -> dict[str, object]:
        capability = _market_capability(exchange_name.value, market, driver_name)
        spec = MarketDataSpec(
            symbol=symbol,
            kind=_historical_kind(kind) if data_mode == "historical" else _live_kind(kind),
            venue=exchange_name.value,
            market=market,
            timeframe=timeframe,
        )
        available = _capability_supports(capability, kind=spec.kind, data_mode=data_mode)
        if data_mode == "historical" and spec.kind == "ohlcv" and spec.timeframe is None:
            available = False
            reason = "historical bar data requires --timeframe"
        else:
            reason = None if available else str(capability.get("reason") or f"{data_mode} {spec.kind} is not supported")
        resolved = MarketApplication(
            store=self._resources.data_store(None, None),
            resolver=None,
        ).queries.resolve(spec)
        return {
            "valid": available,
            "reason": reason,
            "driver": driver_name.value,
            "venue": exchange_name.value,
            "market": market,
            "symbol": symbol,
            "kind": spec.kind,
            "mode": data_mode,
            "timeframe": timeframe,
            "dataset": resolved.dataset_id if data_mode == "historical" else None,
            "capability": capability,
        }

    def doctor(self, *, exchange_name: ExchangeName, driver_name: DriverName) -> dict[str, object]:
        self._resources.public_market_access(exchange_name, driver_name)
        return {"valid": True, "exchange": exchange_name.value, "driver": driver_name.value}


def _market_capability(venue: str, market: str, driver_name: DriverName) -> dict[str, object]:
    venue_name = _normalize_venue(venue)
    market_name = _normalize_market(market)
    configured = driver_name is DriverName.ccxt and market_name in _configured_markets(venue_name)
    status = "configured" if configured else "not_configured"
    reason = None if configured else f"{venue_name} {market_name} market data is not configured"
    return {
        "venue": venue_name,
        "exchange": venue_name,
        "market": market_name,
        "driver": driver_name.value,
        "status": status,
        "reason": reason,
        "historical": _historical_capabilities(market_name) if configured else (),
        "live": _live_capabilities(market_name) if configured else (),
    }


def _candidate_markets(exchange_name: ExchangeName, market: str | None) -> tuple[str, ...]:
    if market is not None:
        return (_normalize_market(market),)
    if exchange_name is ExchangeName.binance:
        return ("spot", "future", "swap", "option")
    if exchange_name is ExchangeName.hyperliquid:
        return ("swap", "spot", "option")
    if exchange_name in {ExchangeName.okx, ExchangeName.okex}:
        return ("spot", "swap", "future", "option")
    return ()


def _configured_markets(venue: str) -> set[str]:
    if venue == "binance":
        return {"spot", "future", "swap", "option"}
    if venue == "hyperliquid":
        return {"swap"}
    if venue in {"okx", "okex"}:
        return {"spot", "swap"}
    return set()


def _historical_capabilities(market: str) -> tuple[dict[str, object], ...]:
    if market == "option":
        return ()
    return ({"kind": "ohlcv", "label": "bars", "selector": "Bar", "timeframe_required": True, "command_kind": "ohlcv"},)


def _live_capabilities(market: str) -> tuple[dict[str, object], ...]:
    capabilities = [
        {"kind": "ticker", "label": "quotes", "selector": "Quote", "command_kind": "ticker"},
        {"kind": "orderbook", "label": "orderbook", "selector": "OrderBookSnapshot", "command_kind": "orderbook"},
        {"kind": "trades", "label": "trades", "selector": "TradePrint", "command_kind": "trades"},
    ]
    if market == "option":
        capabilities.append({"kind": "option_greeks", "label": "option greeks", "selector": "OptionGreeks", "command_kind": "option_greeks"})
    return tuple(capabilities)


def _capability_supports(capability: Mapping[str, object], *, kind: str, data_mode: MarketDataMode) -> bool:
    if capability.get("status") != "configured":
        return False
    rows = capability.get(data_mode)
    return isinstance(rows, tuple) and _normalized_kind(kind) in {str(row.get("kind")) for row in rows if isinstance(row, Mapping)}


def _historical_kind(kind: str) -> str:
    value = _normalized_kind(kind)
    return "ohlcv" if value in {"bar", "bars", "ohlcv"} else value


def _live_kind(kind: str) -> str:
    value = _normalized_kind(kind)
    if value in {"quote", "quotes", "ticker"}:
        return "ticker"
    if value in {"trade", "trades"}:
        return "trades"
    if value in {"option_greeks", "greeks", "option-greeks"}:
        return "option_greeks"
    return value


def _normalized_kind(kind: str) -> str:
    return str(kind).strip().lower()


def _normalize_venue(value: object) -> str:
    text = str(value).strip().lower()
    return "okx" if text == "okex" else text


def _normalize_market(value: object) -> str:
    aliases = {"linear": "swap", "perpetual": "swap", "perp": "swap", "futures": "future", "options": "option"}
    return aliases.get(str(value).strip().lower(), str(value).strip().lower())


__all__ = ["MarketDataMode", "MarketSourceQueryService"]

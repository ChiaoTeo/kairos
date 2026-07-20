from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from kairos.ports import Environment
from kairos.domain.identity import AccountKey, AssetId, InstrumentId

from .rest_transport import BinanceTransport, RateLimiter


@dataclass(frozen=True, slots=True)
class UserFillUpdate:
    execution_id: str
    order_id: str
    client_order_id: str
    account: AccountKey
    instrument_id: InstrumentId
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_asset: AssetId
    event_time: datetime


@dataclass(frozen=True, slots=True)
class BalanceUpdate:
    balances: tuple[tuple[AssetId, Decimal, Decimal], ...]
    event_time: datetime


class BinanceUserDataStreamService:
    """Creates and maintains listen keys without exposing withdrawal capabilities."""

    def __init__(self, transport: BinanceTransport, api_key: str, *, futures: bool = False, inverse: bool = False, options: bool = False, limiter: RateLimiter | None = None) -> None:
        if options and (futures or inverse) or inverse and not futures:
            raise ValueError("invalid Binance user stream market selection")
        self.transport, self.api_key = transport, api_key
        self.futures, self.inverse, self.options = futures, inverse, options
        self.limiter = limiter or RateLimiter(1200, 60)

    @property
    def path(self) -> str:
        if self.options:
            return "/eapi/v1/listenKey"
        if self.inverse:
            return "/dapi/v1/listenKey"
        if self.futures:
            return "/fapi/v1/listenKey"
        return "/api/v3/userDataStream"

    def create(self) -> str:
        self.limiter.acquire()
        row = self.transport.request("POST", self.path, headers={"X-MBX-APIKEY": self.api_key})
        return str(row["listenKey"])

    def keepalive(self, listen_key: str) -> None:
        self.limiter.acquire()
        self.transport.request("PUT", self.path, {"listenKey": listen_key}, {"X-MBX-APIKEY": self.api_key})

    def close(self, listen_key: str) -> None:
        self.limiter.acquire()
        self.transport.request("DELETE", self.path, {"listenKey": listen_key}, {"X-MBX-APIKEY": self.api_key})


def parse_user_stream_event(row: dict, account, instrument_lookup: dict[str, InstrumentId]):
    event_type = row.get("e")
    if event_type == "executionReport" and row.get("x") == "TRADE":
        symbol = row["s"]
        return UserFillUpdate(
            str(row["t"]), str(row["i"]), str(row.get("c") or row["i"]),
            account, instrument_lookup[symbol], row["S"].lower(),
            Decimal(row["l"]), Decimal(row["L"]), Decimal(row["n"]), AssetId(row["N"]),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "outboundAccountPosition":
        return BalanceUpdate(
            tuple((AssetId(item["a"]), Decimal(item["f"]), Decimal(item["l"])) for item in row["B"]),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "ORDER_TRADE_UPDATE" and row.get("o", {}).get("x") == "TRADE":
        order = row["o"]
        symbol = order["s"]
        return UserFillUpdate(
            str(order["t"]), str(order["i"]), str(order.get("c") or order["i"]),
            account, instrument_lookup[symbol], order["S"].lower(),
            Decimal(order["l"]), Decimal(order["L"]), Decimal(order.get("n", "0")),
            AssetId(order.get("N") or order.get("ma") or "USDT"),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "ACCOUNT_UPDATE":
        balances = row.get("a", {}).get("B", [])
        return BalanceUpdate(
            tuple((AssetId(item["a"]), Decimal(item["wb"]), Decimal("0")) for item in balances),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    return None


class BinanceUserStreamProcessor:
    def __init__(self, account: AccountKey, instrument_lookup: dict[str, InstrumentId]) -> None:
        self.account, self.instrument_lookup = account, instrument_lookup
        self._execution_ids: set[str] = set()

    def process(self, row: dict):
        event = parse_user_stream_event(row, self.account, self.instrument_lookup)
        if isinstance(event, UserFillUpdate):
            if event.execution_id in self._execution_ids:
                return None
            self._execution_ids.add(event.execution_id)
        return event

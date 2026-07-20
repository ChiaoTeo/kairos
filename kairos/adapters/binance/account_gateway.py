from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairos.ports import AccountState, Environment, VenueBalance
from kairos.domain.identity import AssetId, InstitutionId, InstrumentId, VenueId

from .request_signing import BinanceSigner
from .rest_transport import BinanceTransport, RateLimiter


class BinanceAccountGateway:
    institution_id = InstitutionId("binance")
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, *, futures: bool = False, inverse: bool = False, limiter: RateLimiter | None = None, instrument_lookup: dict[str, InstrumentId] | None = None) -> None:
        self.transport, self.signer, self.environment, self.futures, self.inverse = transport, signer, environment, futures, inverse
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_lookup = instrument_lookup or {}

    def account_state(self, account) -> AccountState:
        signed, headers = self.signer.signed()
        path = "/dapi/v1/account" if self.inverse else "/fapi/v2/account" if self.futures else "/api/v3/account"
        self.limiter.acquire()
        row = self.transport.request("GET", path, signed, headers)
        if self.futures:
            balances = tuple(
                VenueBalance(
                    AssetId(item["asset"]), Decimal(item["walletBalance"]),
                    Decimal(item.get("availableBalance", item["walletBalance"])),
                    Decimal(item["walletBalance"]) - Decimal(item.get("availableBalance", item["walletBalance"])),
                    Decimal(item.get("borrowed", "0")), collateral=Decimal(item.get("crossWalletBalance", item["walletBalance"])),
                )
                for item in row.get("assets", [])
            )
            positions = tuple((self.instrument_lookup.get(item["symbol"], InstrumentId(f"crypto:binance:perpetual:{item['symbol']}")), Decimal(item["positionAmt"])) for item in row.get("positions", []) if Decimal(item["positionAmt"]) != 0)
        else:
            balances = tuple(
                VenueBalance(AssetId(item["asset"]), Decimal(item["free"]) + Decimal(item["locked"]), Decimal(item["free"]), Decimal(item["locked"]))
                for item in row.get("balances", []) if Decimal(item["free"]) + Decimal(item["locked"]) != 0
            )
            positions = ()
        signed_orders, order_headers = self.signer.signed()
        open_orders_path = "/dapi/v1/openOrders" if self.inverse else "/fapi/v1/openOrders" if self.futures else "/api/v3/openOrders"
        self.limiter.acquire()
        open_orders = self.transport.request("GET", open_orders_path, signed_orders, order_headers)
        return AccountState(account, balances, positions, tuple(str(item["orderId"]) for item in open_orders), datetime.now(timezone.utc))


class BinanceOptionsAccountGateway:
    institution_id = InstitutionId("binance")
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, limiter: RateLimiter | None = None, instrument_lookup: dict[str, InstrumentId] | None = None) -> None:
        if environment is not Environment.LIVE:
            raise ValueError("Binance options account is live-only; no equivalent options testnet is available")
        self.transport, self.signer, self.environment = transport, signer, environment
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_lookup = instrument_lookup or {}

    def account_state(self, account) -> AccountState:
        signed, headers = self.signer.signed()
        self.limiter.acquire()
        account_row = self.transport.request("GET", "/eapi/v1/account", signed, headers)
        balances = tuple(
            VenueBalance(
                AssetId(item["asset"]),
                Decimal(item.get("marginBalance", item.get("equity", item.get("available", "0")))),
                Decimal(item.get("available", "0")),
                Decimal(item.get("locked", "0")),
            )
            for item in account_row.get("asset", account_row.get("assets", []))
        )
        signed_positions, position_headers = self.signer.signed()
        self.limiter.acquire()
        position_rows = self.transport.request("GET", "/eapi/v1/position", signed_positions, position_headers)
        positions = tuple(
            (self.instrument_lookup[item["symbol"]], Decimal(item.get("quantity", item.get("positionAmt", "0"))))
            for item in position_rows
            if item.get("symbol") in self.instrument_lookup and Decimal(item.get("quantity", item.get("positionAmt", "0"))) != 0
        )
        signed_orders, order_headers = self.signer.signed()
        self.limiter.acquire()
        open_orders = self.transport.request("GET", "/eapi/v1/openOrders", signed_orders, order_headers)
        return AccountState(
            account, balances, positions, tuple(str(item["orderId"]) for item in open_orders),
            datetime.now(timezone.utc),
        )



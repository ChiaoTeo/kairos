from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairospy.ports import Environment
from kairospy.domain.execution import FundingPayment
from kairospy.domain.identity import AccountKey, AssetId, InstrumentId, VenueId

from .request_signing import BinanceSigner
from .rest_transport import BinanceTransport, RateLimiter


class BinanceFundingSettlementClient:
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, *, inverse: bool = False, limiter: RateLimiter | None = None, instrument_lookup: dict[str, InstrumentId] | None = None) -> None:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise ValueError("Binance funding history requires testnet or live")
        self.transport, self.signer, self.environment, self.inverse = transport, signer, environment, inverse
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_lookup = instrument_lookup or {}

    def funding_history(self, account: AccountKey, start: datetime, end: datetime) -> tuple[FundingPayment, ...]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("funding history requires an aware, increasing time range")
        signed, headers = self.signer.signed({
            "incomeType": "FUNDING_FEE",
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
        })
        self.limiter.acquire()
        path = "/dapi/v1/income" if self.inverse else "/fapi/v1/income"
        rows = self.transport.request("GET", path, signed, headers)
        payments = []
        for row in rows:
            symbol = row.get("symbol")
            instrument_id = self.instrument_lookup.get(symbol)
            if instrument_id is None:
                raise LookupError(f"unknown Binance funding instrument: {symbol}")
            external_id = row.get("tranId") or row.get("tradeId") or f"{symbol}:{row['time']}:{row['income']}"
            payments.append(FundingPayment(
                uuid5(NAMESPACE_URL, f"binance-funding:{external_id}"),
                datetime.fromtimestamp(int(row["time"]) / 1000, timezone.utc),
                account, instrument_id, AssetId(row["asset"]), Decimal(row["income"]),
                Decimal(row.get("fundingRate", "0")), Decimal(row.get("positionNotional", "0")),
            ))
        return tuple(sorted(payments, key=lambda item: (item.timestamp, str(item.payment_id))))

from __future__ import annotations

from dataclasses import dataclass

from kairos.ports import AccountState
from kairos.domain.identity import AccountKey

from .account_gateway import BinanceAccountGateway
from .execution_gateway import BinanceExecutionGateway
from .request_signing import BinanceSigner
from .rest_transport import BinanceTransport, RateLimiter
from .user_data_stream import BinanceUserStreamProcessor, UserFillUpdate


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    open_order_ids: tuple[str, ...]
    fills: tuple[UserFillUpdate, ...]
    account_state: AccountState


class BinanceRecoveryService:
    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, execution: BinanceExecutionGateway, account_gateway: BinanceAccountGateway, processor: BinanceUserStreamProcessor, limiter: RateLimiter | None = None) -> None:
        self.transport, self.signer, self.execution, self.account_gateway, self.processor = transport, signer, execution, account_gateway, processor
        self.limiter = limiter or execution.limiter

    def recover(self, account: AccountKey, *, since_ms: int) -> RecoverySnapshot:
        state = self.account_gateway.account_state(account)
        open_order_ids = self.execution.open_orders(account)
        path = "/dapi/v1/userTrades" if self.execution.inverse else "/fapi/v1/userTrades" if self.execution.futures else "/api/v3/myTrades"
        signed, headers = self.signer.signed({"startTime": since_ms})
        self.limiter.acquire()
        rows = self.transport.request("GET", path, signed, headers)
        fills = []
        for row in rows:
            normalized = {
                "e": "executionReport", "x": "TRADE", "s": row["symbol"],
                "t": row.get("id") or row.get("tradeId"), "i": row.get("orderId"),
                "c": row.get("clientOrderId") or row.get("origClientOrderId") or row.get("orderId"),
                "S": "BUY" if row.get("isBuyer", row.get("side") == "BUY") else "SELL",
                "l": row.get("qty") or row.get("executedQty"), "L": row.get("price"),
                "n": row.get("commission", "0"), "N": row.get("commissionAsset") or row.get("quoteAsset") or "USDT",
                "E": row.get("time") or row.get("updateTime"),
            }
            event = self.processor.process(normalized)
            if isinstance(event, UserFillUpdate):
                fills.append(event)
        return RecoverySnapshot(open_order_ids, tuple(fills), state)

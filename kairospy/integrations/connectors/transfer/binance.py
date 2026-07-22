from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.integrations.connectors.binance.request_signing import BinanceSigner
from kairospy.integrations.connectors.binance.rest_transport import BinanceTransport, RateLimiter
from kairospy.identity import AssetId
from kairospy.reference import ReferenceCatalog
from kairospy.reference.identity import LocationId
from kairospy.portfolio.treasury.transfer_gateway import TransferSubmission
from kairospy.portfolio.treasury.transfer_contracts import (
    CryptoTransferInstruction, InternalTransferInstruction,
    TransferInstruction, TransferStatus,
)


@dataclass(frozen=True, slots=True)
class BinanceWalletRoute:
    source_location_id: LocationId
    destination_location_id: LocationId
    transfer_type: str

    def __post_init__(self) -> None:
        if not self.transfer_type.strip():
            raise ValueError("Binance wallet transfer type cannot be empty")


class BinanceTransferGateway:
    """Dedicated Binance asset-movement gateway.

    Withdrawal capability is disabled by default and intentionally separate from
    execution gateways and user-stream processors.
    """

    def __init__(
        self,
        transport: BinanceTransport,
        signer: BinanceSigner,
        catalog: ReferenceCatalog,
        wallet_routes: tuple[BinanceWalletRoute, ...] = (),
        *,
        enable_withdrawals: bool = False,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.transport = transport
        self.signer = signer
        self.catalog = catalog
        self.wallet_routes = {(item.source_location_id, item.destination_location_id): item for item in wallet_routes}
        self.enable_withdrawals = enable_withdrawals
        self.limiter = limiter or RateLimiter(600, 60)

    def submit(self, instruction: TransferInstruction) -> TransferSubmission:
        if isinstance(instruction, InternalTransferInstruction):
            return self._submit_internal(instruction)
        if isinstance(instruction, CryptoTransferInstruction):
            return self._submit_withdrawal(instruction)
        raise TypeError(f"Binance transfer gateway does not support {type(instruction).__name__}")

    def status(self, provider_reference: str) -> TransferSubmission:
        if provider_reference.startswith("binance:internal:"):
            transfer_id = provider_reference.rsplit(":", 1)[-1]
            row = self._signed_request("GET", "/sapi/v1/asset/transfer", {"tranId": transfer_id})
            values = row.get("rows", row if isinstance(row, list) else ())
            item = values[0] if values else {}
            status = TransferStatus.COMPLETED if str(item.get("status", "")).upper() in {"SUCCESS", "CONFIRMED"} else TransferStatus.PROCESSING
            return TransferSubmission(provider_reference, status)
        if provider_reference.startswith("binance:withdrawal:"):
            withdrawal_id = provider_reference.rsplit(":", 1)[-1]
            rows = self._signed_request("GET", "/sapi/v1/capital/withdraw/history", {"id": withdrawal_id})
            item = rows[0] if rows else {}
            status = _withdrawal_status(item.get("status"))
            fee = Decimal(str(item["transactionFee"])) if item.get("transactionFee") is not None else None
            return TransferSubmission(
                provider_reference, status,
                Decimal(str(item["amount"])) if item.get("amount") is not None else None,
                fee, AssetId(item["coin"]) if fee is not None and item.get("coin") else None,
                item.get("txId"),
            )
        raise LookupError(f"unknown Binance transfer reference: {provider_reference}")

    def _submit_internal(self, instruction: InternalTransferInstruction) -> TransferSubmission:
        route = self.wallet_routes.get((instruction.source_location_id, instruction.destination_location_id))
        if route is None:
            raise LookupError("no Binance wallet route for internal transfer")
        row = self._signed_request("POST", "/sapi/v1/asset/transfer", {
            "type": route.transfer_type,
            "asset": instruction.asset_id.value,
            "amount": str(instruction.amount),
            "clientTranId": instruction.idempotency_key,
        })
        return TransferSubmission(f"binance:internal:{row['tranId']}", TransferStatus.SUBMITTED)

    def _submit_withdrawal(self, instruction: CryptoTransferInstruction) -> TransferSubmission:
        if not self.enable_withdrawals:
            raise PermissionError("Binance withdrawals are disabled")
        network_asset = self.catalog.network_assets.get(instruction.network_asset_id, _now_from_signer(self.signer))
        network = self.catalog.networks.get(network_asset.network_id, _now_from_signer(self.signer))
        params = {
            "coin": network_asset.asset_id.value,
            "network": network.network_id.value,
            "address": instruction.destination_address,
            "amount": str(instruction.amount),
            "withdrawOrderId": instruction.idempotency_key,
        }
        if instruction.memo:
            params["addressTag"] = instruction.memo
        row = self._signed_request("POST", "/sapi/v1/capital/withdraw/apply", params)
        return TransferSubmission(f"binance:withdrawal:{row['id']}", TransferStatus.SUBMITTED)

    def _signed_request(self, method: str, path: str, params: dict):
        self.limiter.acquire()
        signed, headers = self.signer.signed(params)
        return self.transport.request(method, path, signed, headers)


def _withdrawal_status(value) -> TransferStatus:
    # Binance: 0 email sent, 1 cancelled, 2 awaiting approval, 3 rejected,
    # 4 processing, 5 failure, 6 completed.
    mapping = {
        0: TransferStatus.PROCESSING, 1: TransferStatus.CANCELLED,
        2: TransferStatus.MANUAL_REVIEW, 3: TransferStatus.REJECTED,
        4: TransferStatus.BROADCAST, 5: TransferStatus.FAILED,
        6: TransferStatus.CONFIRMED,
    }
    return mapping.get(int(value), TransferStatus.MANUAL_REVIEW) if value is not None else TransferStatus.MANUAL_REVIEW


def _now_from_signer(signer: BinanceSigner):
    from datetime import datetime, timezone
    from time import time
    return datetime.fromtimestamp((time() * 1000 + signer.clock_offset_ms) / 1000, timezone.utc)

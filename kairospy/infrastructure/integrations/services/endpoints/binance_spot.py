from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BinanceSpotEndpointKind(StrEnum):
    PUBLIC_MARKET_REST = "public_market_rest"
    MARKET_STREAM = "market_stream"
    PRIVATE_ACCOUNT_REST = "private_account_rest"
    USER_STREAM = "user_stream"
    REQUEST_API = "request_api"


@dataclass(frozen=True, slots=True)
class BinanceSpotEndpoint:
    kind: BinanceSpotEndpointKind
    base_url: str

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("Binance endpoint base URL is required")


__all__ = ["BinanceSpotEndpoint", "BinanceSpotEndpointKind"]

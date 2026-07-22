from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlparse


MASSIVE_REST_BASE = "https://api.massiveprivateserver.site"
MASSIVE_SOCKET_BASE = "wss://socket.massiveprivateserver.site"
_ALLOWED_REST_HOST = "api.massiveprivateserver.site"
_ALLOWED_SOCKET_HOST = "socket.massiveprivateserver.site"


@dataclass(frozen=True, slots=True)
class MassiveConfig:
    api_key: str
    rest_base: str = MASSIVE_REST_BASE
    socket_base: str = MASSIVE_SOCKET_BASE
    timeout_seconds: int = 30
    max_retries: int = 4
    monthly_flat_file_limit_bytes: int = 150_000_000_000

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY cannot be empty")
        rest = urlparse(self.rest_base)
        socket = urlparse(self.socket_base)
        if rest.hostname != _ALLOWED_REST_HOST or rest.scheme != "https":
            raise ValueError("Massive REST requests must use https://api.massiveprivateserver.site")
        if socket.hostname != _ALLOWED_SOCKET_HOST or socket.scheme != "wss":
            raise ValueError("Massive WebSocket requests must use wss://socket.massiveprivateserver.site")
        if self.timeout_seconds <= 0 or self.max_retries <= 0:
            raise ValueError("timeout and retries must be positive")

    @classmethod
    def from_env(cls) -> "MassiveConfig":
        value = os.environ.get("KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY")
        if not value:
            raise RuntimeError("KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY is required")
        return cls(value)

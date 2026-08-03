from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import requests


@dataclass(slots=True)
class HttpDriver:
    timeout_seconds: float = 30.0
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive")

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        session = self.session or requests.Session()
        return session.request(
            method.upper(),
            url,
            params=dict(params or {}),
            headers=dict(headers or {}),
            timeout=self.timeout_seconds,
        )


__all__ = ["HttpDriver"]

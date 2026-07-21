from __future__ import annotations

from typing import Mapping, Protocol

from .artifacts import ProviderHealth
from .services import ProviderService


class ProviderConnector(Protocol):
    provider_id: str

    def services(self) -> Mapping[str, ProviderService]:
        ...

    def health(self) -> ProviderHealth:
        ...


from .resources import ProviderResource  # noqa: E402
from .services import HistoricalMarketDataService  # noqa: E402

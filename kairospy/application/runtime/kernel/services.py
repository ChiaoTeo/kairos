from __future__ import annotations

from dataclasses import dataclass

from .context import RuntimeContextFactory
from .output import RuntimeOutputProcessor
from .requests import RuntimeRequestProviders


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    context_factory: RuntimeContextFactory
    output: RuntimeOutputProcessor
    request_providers: RuntimeRequestProviders


__all__ = ["RuntimeServices"]

"""Aggregate System use-case facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SystemApplication:
    workspace: Any
    credentials: Any
    configuration: Any
    operations: Any
    launch: Any
    components: Any
    integration: Any
    strategy_process_factory: Any

    def compose_strategy_process(self, **request: Any) -> Any:
        return self.strategy_process_factory(**request)


__all__ = ["SystemApplication"]

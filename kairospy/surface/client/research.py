"""Python client adapter for Project-scoped Research use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...application.research import ResearchApplication
from ...application.workspace import Workspace
from ...research import (
    BacktestBatchResult,
    BacktestCase,
    ResearchSpec,
)
from .data import DataClient


@dataclass(frozen=True, slots=True)
class ResearchClient:
    """Thin Python surface over the Research Application."""

    workspace: Workspace

    @property
    def data(self) -> DataClient:
        return DataClient(self.workspace)

    @property
    def _application(self) -> ResearchApplication:
        return ResearchApplication(self.workspace)

    async def run_backtests(
        self,
        spec: ResearchSpec,
        cases: tuple[BacktestCase, ...],
        *,
        max_concurrency: int | None = None,
    ) -> BacktestBatchResult:
        return await self._application.run_backtests(
            spec, cases, max_concurrency=max_concurrency
        )

    def pin_plan(self, spec: ResearchSpec) -> Mapping[str, Any]:
        return self._application.pin_plan(spec)

    def publish_gate(
        self,
        spec: ResearchSpec,
        *,
        results: Mapping[str, Mapping[str, Any]],
        conclusion: str,
        limitations: tuple[str, ...],
    ) -> Mapping[str, Any]:
        return self._application.publish_gate(
            spec,
            results=results,
            conclusion=conclusion,
            limitations=limitations,
        )

    def gate_report(self, research_plan_hash: str) -> Mapping[str, Any]:
        return self._application.gate_report(research_plan_hash)

    def plan(self, research_plan_hash: str) -> Mapping[str, Any]:
        return self._application.plan(research_plan_hash)


__all__ = ["ResearchClient"]

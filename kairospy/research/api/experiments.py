"""Research parameter-case and batch-result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from kairospy.system.apps.launch.application.specs import BacktestResult, BacktestSpec


@dataclass(frozen=True, slots=True)
class BacktestCase:
    case_id: str
    spec: BacktestSpec
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout: float = 3600.0

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("backtest case_id is required")
        if self.timeout <= 0:
            raise ValueError("backtest case timeout must be positive")
        object.__setattr__(self, "params", dict(self.params))


@dataclass(frozen=True, slots=True)
class BacktestCaseResult:
    case_id: str
    params: Mapping[str, Any]
    status: str
    result: BacktestResult | None = None
    error_type: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed"}:
            raise ValueError("backtest case result status is invalid")
        if self.status == "completed" and self.result is None:
            raise ValueError("completed backtest case requires a result")
        if self.status == "failed" and (not self.error_type or not self.error):
            raise ValueError("failed backtest case requires an explicit error")

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "params": dict(self.params),
            "status": self.status,
            "result": (
                {
                    "launch_id": self.result.launch_id,
                    "instance_id": self.result.instance_id,
                    "status": self.result.status,
                    "normalized_config_hash": self.result.normalized_config_hash,
                    "report": dict(self.result.report),
                }
                if self.result is not None
                else None
            ),
            "error_type": self.error_type,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class BacktestBatchResult:
    cases: tuple[BacktestCaseResult, ...]
    max_concurrency: int

    @property
    def status(self) -> str:
        return (
            "completed"
            if all(case.status == "completed" for case in self.cases)
            else "failed"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "max_concurrency": self.max_concurrency,
            "cases": [case.as_dict() for case in self.cases],
        }


__all__ = [
    "BacktestBatchResult",
    "BacktestCase",
    "BacktestCaseResult",
]

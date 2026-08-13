"""Stable Research plans and policies consumed by user-authored research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from ..application.data import DatasetSetRef


_COST_SCENARIOS = (
    "mid_sensitivity",
    "conservative_bid_ask",
    "fees",
    "stress_slippage",
)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Research period timestamps must be timezone-aware")
    return parsed


@dataclass(frozen=True, slots=True)
class ResearchPeriod:
    start: str
    end: str

    def __post_init__(self) -> None:
        if _time(self.start) >= _time(self.end):
            raise ValueError("Research period start must be before end")


@dataclass(frozen=True, slots=True)
class ResearchExperimentPolicy:
    """Parameter-search policy fixed before validation and Holdout inspection."""

    parameter_space: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    maximum_trials: int = 1
    max_concurrency: int = 1
    selection_rule: str = "pre-registered single specification"
    holdout_parameter_changes: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.maximum_trials, bool)
            or not isinstance(self.maximum_trials, int)
            or self.maximum_trials <= 0
        ):
            raise ValueError("Research maximum_trials must be a positive integer")
        if (
            isinstance(self.max_concurrency, bool)
            or not isinstance(self.max_concurrency, int)
            or self.max_concurrency <= 0
        ):
            raise ValueError("Research max_concurrency must be a positive integer")
        if self.max_concurrency > self.maximum_trials:
            raise ValueError("Research concurrency cannot exceed maximum trials")
        if not self.selection_rule.strip():
            raise ValueError("Research experiment selection_rule is required")
        if self.holdout_parameter_changes:
            raise ValueError(
                "Research cannot change parameters after Holdout inspection"
            )
        normalized: dict[str, tuple[Any, ...]] = {}
        for name, candidates in self.parameter_space.items():
            key = str(name).strip()
            values = tuple(candidates)
            if not key or not values:
                raise ValueError(
                    "Research parameter space names and candidate sets are required"
                )
            if any(
                value is not None and not isinstance(value, (str, int, float, bool))
                for value in values
            ):
                raise ValueError("Research parameter candidates must be JSON scalars")
            normalized[key] = values
        object.__setattr__(self, "parameter_space", normalized)

    def as_dict(self) -> dict[str, Any]:
        return {
            "parameter_space": {
                name: list(values)
                for name, values in sorted(self.parameter_space.items())
            },
            "maximum_trials": self.maximum_trials,
            "max_concurrency": self.max_concurrency,
            "selection_rule": self.selection_rule,
            "holdout_parameter_changes": self.holdout_parameter_changes,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchExperimentPolicy":
        raw_space = value.get("parameter_space", {})
        if not isinstance(raw_space, Mapping):
            raise ValueError("Research parameter_space must be an object")
        return cls(
            parameter_space={
                str(name): tuple(candidates)
                if isinstance(candidates, (list, tuple))
                else ()
                for name, candidates in raw_space.items()
            },
            maximum_trials=value.get("maximum_trials", 1),
            max_concurrency=value.get("max_concurrency", 1),
            selection_rule=str(
                value.get("selection_rule", "pre-registered single specification")
            ),
            holdout_parameter_changes=value.get("holdout_parameter_changes", False),
        )


@dataclass(frozen=True, slots=True)
class ResearchSpec:
    research_id: str
    hypothesis: str
    dataset_set: DatasetSetRef
    observation_rule: str
    feature_availability_rule: str
    label_rule: str
    in_sample: ResearchPeriod
    validation: ResearchPeriod
    holdout: ResearchPeriod
    baseline: str
    seed: int
    code_version: str
    quote_stale_window_nanos: int
    cost_scenarios: tuple[str, ...] = _COST_SCENARIOS
    experiment: ResearchExperimentPolicy = field(
        default_factory=ResearchExperimentPolicy
    )

    def __post_init__(self) -> None:
        for name in (
            "research_id",
            "hypothesis",
            "observation_rule",
            "baseline",
            "code_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"Research {name} is required")
        if self.feature_availability_rule != "feature.available_at <= observation_time":
            raise ValueError("Research must enforce Point-in-time feature availability")
        if self.label_rule != "label.start_time > observation_time":
            raise ValueError("Research must enforce future-only labels")
        if not (
            _time(self.in_sample.end)
            <= _time(self.validation.start)
            < _time(self.validation.end)
            <= _time(self.holdout.start)
        ):
            raise ValueError("Research periods must be ordered and non-overlapping")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("Research seed must be an integer")
        if self.quote_stale_window_nanos <= 0:
            raise ValueError("Research Quote stale window must be positive")
        if tuple(self.cost_scenarios) != _COST_SCENARIOS:
            raise ValueError(
                "Research requires mid sensitivity, conservative Bid/Ask, fees and stress"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "dataset_set": self.dataset_set.as_dict(),
            "experiment": self.experiment.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchSpec":
        required = {
            "research_id",
            "hypothesis",
            "dataset_set",
            "observation_rule",
            "feature_availability_rule",
            "label_rule",
            "in_sample",
            "validation",
            "holdout",
            "baseline",
            "seed",
            "code_version",
            "quote_stale_window_nanos",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ValueError("Research plan is missing: " + ", ".join(missing))
        dataset_set = value["dataset_set"]
        if not isinstance(dataset_set, Mapping):
            raise ValueError("Research dataset_set must be an object")

        def period(name: str) -> ResearchPeriod:
            raw = value[name]
            if not isinstance(raw, Mapping):
                raise ValueError(f"Research {name} must be an object")
            try:
                return ResearchPeriod(start=str(raw["start"]), end=str(raw["end"]))
            except KeyError as error:
                raise ValueError(f"Research {name} requires start and end") from error

        scenarios = value.get("cost_scenarios", _COST_SCENARIOS)
        if not isinstance(scenarios, (list, tuple)):
            raise ValueError("Research cost_scenarios must be a sequence")
        experiment = value.get("experiment", {})
        return cls(
            research_id=str(value["research_id"]),
            hypothesis=str(value["hypothesis"]),
            dataset_set=DatasetSetRef.from_dict(dataset_set),
            observation_rule=str(value["observation_rule"]),
            feature_availability_rule=str(value["feature_availability_rule"]),
            label_rule=str(value["label_rule"]),
            in_sample=period("in_sample"),
            validation=period("validation"),
            holdout=period("holdout"),
            baseline=str(value["baseline"]),
            seed=value["seed"],
            code_version=str(value["code_version"]),
            quote_stale_window_nanos=value["quote_stale_window_nanos"],
            cost_scenarios=tuple(str(item) for item in scenarios),
            experiment=ResearchExperimentPolicy.from_dict(
                experiment if isinstance(experiment, Mapping) else {}
            ),
        )

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()


__all__ = ["ResearchExperimentPolicy", "ResearchPeriod", "ResearchSpec"]
